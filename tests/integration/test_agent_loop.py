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
        self.events: list[tuple[str, object]] = []

    def append(self, run_id: str, event_type: str, payload: object) -> object:
        del run_id
        self.events.append((event_type, payload))
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
    audit: FakeAudit


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
    script: list[str],
    validation_successes: tuple[bool, ...] = (True,),
    *,
    audit: FakeAudit | None = None,
) -> LoopFixture:
    settings = _settings()
    registry = SpyRegistry(validation_successes)
    approvals = FakeApprovals()
    audit = audit or FakeAudit()
    loop = AgentLoop(
        llm=ScriptedMockLLM(script),
        context=ContextBuilder(None, settings.memory),
        action_parser=ActionParser(),
        policy=FakePolicy(),
        approvals=approvals,
        tools=registry,
        feedback=FeedbackEngine(no_progress_limit=2),
        run_store=RunStore(sqlite3.connect(":memory:")),
        audit=audit,
        settings=settings,
    )
    return LoopFixture(loop, registry, approvals, audit)


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
    fixture = _loop(
        [
            patch_one,
            patch_two,
            '{"type":"finish","id":"done","reason":"done","summary":"fixed"}',
        ],
        validation_successes=(False, True, True),
    )

    snapshot = await fixture.loop.start(project="fixture", description="fix value")

    assert snapshot.status is RunStatus.SUCCESS
    assert snapshot.repair_round == 2
    assert snapshot.action_digests[0] != snapshot.action_digests[1]
    assert any(item.category.value == "TEST_FAILURE" for item in snapshot.feedback_history)


@pytest.mark.asyncio
async def test_validation_success_is_feedback_until_finish_succeeds() -> None:
    fixture = _loop(
        [
            '{"type":"run_validation","id":"v1","reason":"check",'
            '"validator_id":"pytest"}',
            '{"type":"finish","id":"done","reason":"done","summary":"complete"}',
        ],
        validation_successes=(True, True),
    )

    snapshot = await fixture.loop.start(
        project="fixture",
        description="validate then finish",
    )

    assert snapshot.status is RunStatus.SUCCESS
    assert [
        payload["id"]
        for event_type, payload in fixture.audit.events
        if event_type == "ACTION"
    ] == ["v1", "done"]


@pytest.mark.asyncio
async def test_patch_validation_success_is_feedback_until_finish_succeeds() -> None:
    patch = '{"type":"apply_patch","id":"p1","reason":"fix","path":"app.py","expected_sha256":"' + "0" * 64 + '","old_text":"a","new_text":"b","expected_replacements":1}'
    fixture = _loop(
        [
            patch,
            '{"type":"finish","id":"done","reason":"done","summary":"fixed"}',
        ],
        validation_successes=(True, True),
    )

    snapshot = await fixture.loop.start(project="fixture", description="fix value")

    assert snapshot.status is RunStatus.SUCCESS
    assert [
        payload["id"]
        for event_type, payload in fixture.audit.events
        if event_type == "ACTION"
    ] == ["p1", "done"]
    assert snapshot.changed_files == ("app.py",)


@pytest.mark.asyncio
async def test_failed_finish_validation_returns_feedback_and_allows_retry() -> None:
    fixture = _loop(
        [
            '{"type":"finish","id":"done-1","reason":"done","summary":"first"}',
            '{"type":"finish","id":"done-2","reason":"retry","summary":"second"}',
        ],
        validation_successes=(False, True),
    )

    snapshot = await fixture.loop.start(
        project="fixture",
        description="finish after validation",
    )

    assert snapshot.status is RunStatus.SUCCESS
    assert [
        payload["id"]
        for event_type, payload in fixture.audit.events
        if event_type == "ACTION"
    ] == ["done-1", "done-2"]
    assert snapshot.feedback_history[0].category.value == "TEST_FAILURE"


@pytest.mark.asyncio
async def test_cancel_pending_run() -> None:
    fixture = _loop(
        ['{"type":"run_process","id":"a1","reason":"commit","program":"git","args":["commit"]}']
    )
    paused = await fixture.loop.start(project="fixture", description="commit changes")

    cancelled = await fixture.loop.cancel(paused.run_id)

    assert cancelled.status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_tool_results_and_feedback_are_audited_in_action_order() -> None:
    fixture = _loop(
        [
            '{"type":"run_validation","id":"v1","reason":"check",'
            '"validator_id":"pytest"}',
            '{"type":"run_validation","id":"v2","reason":"check again",'
            '"validator_id":"pytest"}',
            '{"type":"finish","id":"done","reason":"done","summary":"complete"}',
        ],
        validation_successes=(False, True, True),
    )

    snapshot = await fixture.loop.start(
        project="fixture",
        description="validate the project",
    )

    event_types = [
        event_type for event_type, payload in fixture.audit.events
    ]
    assert snapshot.status is RunStatus.SUCCESS
    assert event_types == [
        "ACTION",
        "POLICY_DECISION",
        "TOOL_RESULT",
        "FEEDBACK",
        "ACTION",
        "POLICY_DECISION",
        "TOOL_RESULT",
        "FEEDBACK",
        "ACTION",
        "POLICY_DECISION",
        "TOOL_RESULT",
        "FEEDBACK",
    ]
    assert [
        payload["action_id"]
        for event_type, payload in fixture.audit.events
        if event_type == "TOOL_RESULT"
    ] == ["v1", "v2", "done:pytest"]


class FailingEvidenceAudit(FakeAudit):
    def __init__(self, failing_event_type: str) -> None:
        super().__init__()
        self._failing_event_type = failing_event_type

    def append(self, run_id: str, event_type: str, payload: object) -> object:
        if event_type == self._failing_event_type:
            raise RuntimeError("raw audit storage detail")
        return super().append(run_id, event_type, payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_event_type", ["TOOL_RESULT", "FEEDBACK"])
async def test_evidence_audit_failure_stops_before_another_action(
    failing_event_type: str,
) -> None:
    audit = FailingEvidenceAudit(failing_event_type)
    fixture = _loop(
        [
            '{"type":"run_validation","id":"v1","reason":"check",'
            '"validator_id":"pytest"}',
            '{"type":"finish","id":"done","reason":"done","summary":"complete"}',
        ],
        validation_successes=(False, True),
        audit=audit,
    )

    snapshot = await fixture.loop.start(
        project="fixture",
        description="validate the project",
    )

    assert snapshot.status is RunStatus.FAILED
    assert snapshot.stop_reason == "audit unavailable"
    assert len(fixture.registry.calls) == 1

