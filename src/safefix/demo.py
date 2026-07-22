from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from safefix.action_parser import ActionParser
from safefix.config import LLMSettings, SafeFixSettings, ValidatorSettings
from safefix.domain import (
    Action,
    ApplyPatchAction,
    DecisionOutcome,
    FeedbackCategory,
    RiskLevel,
    RunProcessAction,
)
from safefix.feedback import FeedbackEngine
from safefix.governance.approvals import ActionMismatch, ApprovalAlreadyUsed
from safefix.governance.approvals import ApprovalStateMachine
from safefix.governance.paths import WorkspaceBoundary
from safefix.governance.policy import PolicyEngine
from safefix.llm.mock import ScriptedMockLLM
from safefix.tools.filesystem import ApplyPatchTool
from safefix.tools.process import ProcessTool, ValidatorRunner


@dataclass(frozen=True, slots=True)
class DemoResult:
    name: str
    passed: bool
    events: tuple[str, ...]
    workspace_removed: bool


_FIXTURE = Path(__file__).parents[2] / "examples" / "python_bug"


@contextmanager
def _isolated_fixture() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="safefix-demo-") as directory:
        workspace = Path(directory) / "python_bug"
        shutil.copytree(_FIXTURE, workspace)
        yield workspace


async def _parse_scripted(payload: dict[str, object]) -> Action:
    llm = ScriptedMockLLM((json.dumps(payload),))
    response = await llm.complete((), {})
    return ActionParser().parse(response.text)


def _settings() -> SafeFixSettings:
    return SafeFixSettings(
        llm=LLMSettings(endpoint="https://demo.invalid/v1", model="scripted"),
        validators=(
            ValidatorSettings(
                id="pytest",
                kind="test",
                program=sys.executable,
                args=("-m", "pytest", "-q"),
                timeout_seconds=30,
                success_exit_codes=frozenset({0}),
                output_limit_bytes=16_384,
            ),
        ),
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_guardrail_demo() -> DemoResult:
    with _isolated_fixture() as workspace:
        settings = _settings()
        policy = PolicyEngine(settings, WorkspaceBoundary(workspace, ()))
        action = asyncio.run(
            _parse_scripted(
                {
                    "type": "run_process",
                    "id": "guardrail",
                    "reason": "attempt privilege escalation",
                    "program": "sudo",
                    "args": ["rm", "-rf", "/"],
                }
            )
        )
        assert isinstance(action, RunProcessAction)
        decision = policy.decide(action)
        tool_calls: list[Action] = []
        if decision.outcome is DecisionOutcome.ALLOW:
            tool_calls.append(action)
        events = (
            f"POLICY:{decision.outcome.value}",
            f"RULE:{decision.rule_ids[0]}",
            f"TOOL_CALLS:{len(tool_calls)}",
        )
    return DemoResult("guardrail", events[0] == "POLICY:DENY", events, not workspace.exists())


async def _feedback_events(workspace: Path) -> tuple[str, ...]:
    boundary = WorkspaceBoundary(workspace, ())
    runner = ValidatorRunner(ProcessTool(boundary), _settings().validators)
    patcher = ApplyPatchTool(boundary)
    feedback = FeedbackEngine()
    calculator = workspace / "calculator.py"
    events: list[str] = []

    initial = await runner.run("pytest")
    initial_feedback = feedback.from_results((initial,), (), 5, 2)
    assert initial_feedback.category is FeedbackCategory.TEST_FAILURE
    events.append("VALIDATION:FAIL")

    wrong = await _parse_scripted(
        {
            "type": "apply_patch",
            "id": "wrong-patch",
            "reason": "first repair attempt",
            "path": "calculator.py",
            "expected_sha256": _digest(calculator),
            "old_text": "return left - right",
            "new_text": "return left * right",
            "expected_replacements": 1,
        }
    )
    assert isinstance(wrong, ApplyPatchAction)
    assert (await patcher.execute(wrong)).success
    events.append("PATCH:WRONG")

    still_failing = await runner.run("pytest")
    assert not still_failing.success
    events.append("VALIDATION:FAIL")

    correct = await _parse_scripted(
        {
            "type": "apply_patch",
            "id": "correct-patch",
            "reason": "repair from validator feedback",
            "path": "calculator.py",
            "expected_sha256": _digest(calculator),
            "old_text": "return left * right",
            "new_text": "return left + right",
            "expected_replacements": 1,
        }
    )
    assert isinstance(correct, ApplyPatchAction)
    assert (await patcher.execute(correct)).success
    events.append("PATCH:CORRECT")

    passing = await runner.run("pytest")
    passing_feedback = feedback.from_results((passing,), ("calculator.py",), 3, 0)
    assert passing_feedback.category is FeedbackCategory.VALIDATION_SUCCESS
    events.append("VALIDATION:PASS")
    return tuple(events)


def run_feedback_demo() -> DemoResult:
    with _isolated_fixture() as workspace:
        events = asyncio.run(_feedback_events(workspace))
    return DemoResult("feedback", events[-1] == "VALIDATION:PASS", events, not workspace.exists())


def run_approval_demo() -> DemoResult:
    with _isolated_fixture() as workspace:
        database = workspace / "approvals.sqlite3"
        action = asyncio.run(
            _parse_scripted(
                {
                    "type": "run_process",
                    "id": "git-write",
                    "reason": "record reviewed repair",
                    "program": "git",
                    "args": ["commit", "-m", "demo"],
                }
            )
        )
        assert isinstance(action, RunProcessAction)
        first_connection = sqlite3.connect(database)
        challenge = ApprovalStateMachine(first_connection).request(
            "demo-run", action, RiskLevel.MEDIUM, ("CMD_GIT_WRITE",), 300
        )
        first_connection.close()
        events = ["APPROVAL:PENDING"]

        second_connection = sqlite3.connect(database)
        approvals = ApprovalStateMachine(second_connection)
        events.append("STORE:REOPENED")
        changed = action.model_copy(update={"args": ("push", "origin", "main")})
        try:
            approvals.approve(challenge.id, challenge.token, changed)
        except ActionMismatch:
            events.append("ACTION_MISMATCH:BLOCKED")
        else:
            raise AssertionError("changed action was approved")

        approvals.approve(challenge.id, challenge.token, action)
        events.append("APPROVAL:APPROVED")
        tool_calls = [action]
        try:
            approvals.approve(challenge.id, challenge.token, action)
        except ApprovalAlreadyUsed:
            events.append("TOKEN_REPLAY:BLOCKED")
        else:
            raise AssertionError("approval token replay succeeded")
        events.append(f"TOOL_CALLS:{len(tool_calls)}")
        second_connection.close()
        final_events = tuple(events)
    return DemoResult("approval", True, final_events, not workspace.exists())


SCENARIOS: dict[str, Callable[[], DemoResult]] = {
    "guardrail": run_guardrail_demo,
    "feedback": run_feedback_demo,
    "approval": run_approval_demo,
}


def run_scenario(name: str) -> DemoResult:
    try:
        scenario = SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(f"unknown demo scenario: {name}") from exc
    return scenario()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m safefix.demo")
    parser.add_argument("scenario", choices=(*SCENARIOS, "all"), nargs="?", default="all")
    args = parser.parse_args(argv)
    names = tuple(SCENARIOS) if args.scenario == "all" else (args.scenario,)
    passed = True
    for name in names:
        result = run_scenario(name)
        passed = passed and result.passed
        print(f"{name}: {'PASS' if result.passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
