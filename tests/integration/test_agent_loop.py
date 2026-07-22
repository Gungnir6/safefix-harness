from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from safefix.action_parser import ActionParser
from safefix.agent_loop import AgentLoop
from safefix.config import (
    BudgetSettings,
    LLMSettings,
    MemorySettings,
    SafeFixSettings,
    ValidatorSettings,
)
from safefix.context import ContextBuilder
from safefix.domain import (
    ApplyPatchAction,
    DecisionOutcome,
    PolicyDecision,
    RiskLevel,
    RunProcessAction,
    RunStatus,
    RunValidationAction,
    ToolResult,
)
from safefix.feedback import FeedbackEngine
from safefix.llm.mock import ScriptedMockLLM
from safefix.run_store import RunStore


class FakePolicy:
    def decide(self, action: object) -> PolicyDecision:
        approval = isinstance(action, RunProcessAction)
        return PolicyDecision(
            action_id=action.id,  # type: ignore[attr-defined]
            outcome=(
                DecisionOutcome.REQUIRE_APPROVAL
                if approval
                else DecisionOutcome.ALLOW
            ),
            risk_level=RiskLevel.MEDIUM if approval else RiskLevel.LOW,
            rule_ids=("TEST_RULE",),
            explanation="test policy",
        )


class FakeApprovals:
    def __init__(self) -> None:
        self.action: object | None = None
        self.token = "approval-token"

    def request(self, run_id: str, action: object, *args: object, **kwargs: object) -> object:
        del run_id, args, kwargs
        self.action = action
        return SimpleNamespace(id="approval-1", token=self.token)

    def approve(self, approval_id: str, token: str, action: object) -> object:
        assert approval_id == "approval-1"
        assert token == self.token
        assert action == self.action
        return SimpleNamespace(id=approval_id)

    def reject(self, approval_id: str, token: str) -> object:
        assert approval_id == "approval-1"
        assert token == self.token
        return SimpleNamespace(id=approval_id)

    def cancel(self, approval_id: str) -> object:
        return SimpleNamespace(id=approval_id)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[str] = []

    def append(self, run_id: str, event_type: str, payload: object) -> object:
        del run_id, payload
        self.events.append(event_type)
        return object()


class SpyRegistry:
    def __init__(self, validation_successes: tuple[bool, ...] = (True,)) -> None:
        self.calls: list[object] = []
        self._validation_successes = list(validation_successes)

    async def dispatch(self, action: object) -> ToolResult:
        self.calls.append(action)
        if isinstance(action, RunValidationAction):
            success = self._validation_successes.pop(0)
            if success:
                return ToolResult(action_id=action.id, success=True, exit_code=0)
            return ToolResult(
                action_id=action.id,
                success=False,
                exit_code=1,
                stdout_summary="1 failed",
                error_type="PROCESS_EXIT_NONZERO",
            )
        changed = (action.path,) if isinstance(action, ApplyPatchAction) else ()
        return ToolResult(
            action_id=action.id,  # type: ignore[attr-defined]
            success=True,
            exit_code=0,
            changed_files=changed,
        )


@dataclass
class LoopFixture:
    loop: AgentLoop
    registry: SpyRegistry
    approvals: FakeApprovals


def _settings() -> SafeFixSettings:
    return SafeFixSettings(
        llm=LLMSettings(endpoint="https://example.test/v1", model="mock"),
        validators=(
            ValidatorSettings(
                id="pytest",
                kind="test",
                program=sys.executable,
                args=("-m", "pytest", "-q"),
                timeout_seconds=30,
                success_exit_codes=frozenset({0}),
                output_limit_bytes=1024,
            ),
        ),
        budget=BudgetSettings(
            repair_rounds=3,
            no_progress_rounds=2,
            total_steps=10,
            wall_time_seconds=60,
        ),
        memory=MemorySettings(retrieval_limit=3, character_budget=256),
    )


def _loop(
    script: list[str], validation_successes: tuple[bool, ...] = (True,)
) -> LoopFixture:
    settings = _settings()
    registry = SpyRegistry(validation_successes)
    approvals = FakeApprovals()
    loop = AgentLoop(
        llm=ScriptedMockLLM(script),
        context=ContextBuilder(None, settings.memory),
        action_parser=ActionParser(),
        policy=FakePolicy(),
        approvals=approvals,
        tools=registry,
        feedback=FeedbackEngine(no_progress_limit=2),
        run_store=RunStore(sqlite3.connect(":memory:")),
        audit=FakeAudit(),
        settings=settings,
    )
    return LoopFixture(loop, registry, approvals)


@pytest.mark.asyncio
async def test_loop_pauses_before_dangerous_tool_execution() -> None:
    fixture = _loop(
        ['{"type":"run_process","id":"a1","reason":"commit","program":"git","args":["commit"]}']
    )

    snapshot = await fixture.loop.start(project="fixture", description="commit changes")

    assert snapshot.status is RunStatus.AWAITING_APPROVAL
    assert fixture.registry.calls == []
    assert snapshot.pending_approval_id == "approval-1"
    assert (
        fixture.loop.take_approval_capability("approval-1")
        == fixture.approvals.token
    )
    assert fixture.loop.take_approval_capability("approval-1") is None


@pytest.mark.asyncio
async def test_approval_resume_executes_frozen_action_and_finishes() -> None:
    fixture = _loop(
        [
            '{"type":"run_process","id":"a1","reason":"commit","program":"git","args":["commit"]}',
            '{"type":"finish","id":"done","reason":"done","summary":"finished"}',
        ]
    )
    paused = await fixture.loop.start(project="fixture", description="commit changes")

    snapshot = await fixture.loop.resume_approved(
        paused.pending_approval_id, fixture.approvals.token  # type: ignore[arg-type]
    )

    assert snapshot.status is RunStatus.SUCCESS
    assert isinstance(fixture.registry.calls[0], RunProcessAction)
    assert any(isinstance(call, RunValidationAction) for call in fixture.registry.calls)


@pytest.mark.asyncio
async def test_failed_validation_allows_two_repairs_then_succeeds() -> None:
    patch_one = '{"type":"apply_patch","id":"p1","reason":"fix","path":"app.py","expected_sha256":"' + "0" * 64 + '","old_text":"a","new_text":"b","expected_replacements":1}'
    patch_two = '{"type":"apply_patch","id":"p2","reason":"fix again","path":"app.py","expected_sha256":"' + "1" * 64 + '","old_text":"b","new_text":"c","expected_replacements":1}'
    fixture = _loop([patch_one, patch_two], validation_successes=(False, True))

    snapshot = await fixture.loop.start(project="fixture", description="fix value")

    assert snapshot.status is RunStatus.SUCCESS
    assert snapshot.repair_round == 2
    assert snapshot.action_digests[0] != snapshot.action_digests[1]
    assert any(item.category.value == "TEST_FAILURE" for item in snapshot.feedback_history)


@pytest.mark.asyncio
async def test_cancel_pending_run() -> None:
    fixture = _loop(
        ['{"type":"run_process","id":"a1","reason":"commit","program":"git","args":["commit"]}']
    )
    paused = await fixture.loop.start(project="fixture", description="commit changes")

    cancelled = await fixture.loop.cancel(paused.run_id)

    assert cancelled.status is RunStatus.CANCELLED

