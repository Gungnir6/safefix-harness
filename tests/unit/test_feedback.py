from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from safefix.domain import BudgetState, FeedbackCategory, RunStatus, ToolResult
from safefix.feedback import FeedbackEngine


def _result(
    action_id: str,
    output: str,
    exit_code: int = 1,
    error_type: str | None = None,
) -> ToolResult:
    return ToolResult(
        action_id=action_id,
        success=exit_code == 0,
        exit_code=exit_code,
        stdout_summary=output,
        error_type=error_type,
    )


def _budget(
    *, steps: int = 2, repairs: int = 1, deadline_at: datetime | None = None
) -> BudgetState:
    return BudgetState(
        max_steps=20,
        remaining_steps=steps,
        max_repair_rounds=3,
        remaining_repairs=repairs,
        deadline_at=deadline_at,
    )


def test_fewer_failed_tests_counts_as_progress() -> None:
    engine = FeedbackEngine()
    previous = engine.from_results(
        [_result("pytest", "2 failed, 3 passed")],
        ("app.py",),
        remaining_steps=2,
        remaining_repairs=2,
    )
    current = engine.from_results(
        [_result("pytest", "1 failed, 4 passed")],
        ("app.py",),
        remaining_steps=1,
        remaining_repairs=1,
    )

    assert previous.failure_count == 2
    assert current.failure_count == 1
    assert engine.compare(previous, current).made_progress is True


def test_two_equal_failure_fingerprints_stop_no_progress() -> None:
    engine = FeedbackEngine(no_progress_limit=2)
    feedback = engine.from_results(
        [_result("pytest", "1 failed: test_value")],
        ("app.py",),
        remaining_steps=2,
        remaining_repairs=1,
    )

    decision = engine.should_stop([feedback, feedback], _budget())

    assert decision is not None
    assert decision.code == RunStatus.NO_PROGRESS


@pytest.mark.parametrize(
    ("result", "category"),
    [
        (_result("pytest", "1 failed"), FeedbackCategory.TEST_FAILURE),
        (_result("ruff", "E501", error_type="PROCESS_EXIT_NONZERO"), FeedbackCategory.LINT_FAILURE),
        (_result("mypy", "error: bad type", error_type="PROCESS_EXIT_NONZERO"), FeedbackCategory.TYPE_ERROR),
        (_result("pytest", "", error_type="PROCESS_TIMEOUT"), FeedbackCategory.TIMEOUT),
        (_result("tool", "", error_type="PROCESS_NOT_FOUND"), FeedbackCategory.TOOL_ERROR),
        (_result("policy", "", error_type="POLICY_DENIED"), FeedbackCategory.POLICY_REJECTION),
    ],
)
def test_failure_classification(result: ToolResult, category: FeedbackCategory) -> None:
    feedback = FeedbackEngine().from_results(
        [result], (), remaining_steps=1, remaining_repairs=1
    )

    assert feedback.category == category


def test_mixed_validators_ignore_success_and_report_test_regression() -> None:
    feedback = FeedbackEngine().from_results(
        [_result("ruff", "ok", exit_code=0), _result("pytest", "2 failed")],
        (),
        remaining_steps=1,
        remaining_repairs=1,
    )

    assert feedback.category == FeedbackCategory.TEST_FAILURE
    assert feedback.failure_count == 2


@pytest.mark.parametrize(
    "budget",
    [
        _budget(steps=0),
        _budget(repairs=0),
        _budget(deadline_at=datetime.now(UTC) - timedelta(seconds=1)),
    ],
)
def test_exhausted_budget_stops_run(budget: BudgetState) -> None:
    feedback = FeedbackEngine().from_results(
        [_result("pytest", "1 failed")],
        (),
        remaining_steps=budget.remaining_steps,
        remaining_repairs=budget.remaining_repairs,
    )

    decision = FeedbackEngine().should_stop([feedback], budget)

    assert decision is not None
    assert decision.code == RunStatus.BUDGET_EXCEEDED


def test_success_and_repeated_action_digest_stop() -> None:
    engine = FeedbackEngine(no_progress_limit=2)
    success = engine.from_results(
        [_result("pytest", "5 passed", exit_code=0)],
        (),
        remaining_steps=1,
        remaining_repairs=1,
    )
    failure = engine.from_results(
        [_result("pytest", "1 failed")],
        (),
        remaining_steps=1,
        remaining_repairs=1,
    )

    assert engine.should_stop([success], _budget()).code == RunStatus.SUCCESS  # type: ignore[union-attr]
    assert engine.should_stop([failure], _budget(), ("same", "same")).code == RunStatus.NO_PROGRESS  # type: ignore[union-attr]


def test_volatile_paths_and_timestamps_do_not_change_fingerprint() -> None:
    engine = FeedbackEngine()
    first = engine.from_results(
        [_result("pytest", "2026-01-01T10:20:30 C:\\tmp\\a.py: test failed")],
        (),
        remaining_steps=2,
        remaining_repairs=1,
    )
    second = engine.from_results(
        [_result("pytest", "2027-02-02T11:21:31 C:\\other\\a.py: test failed")],
        (),
        remaining_steps=1,
        remaining_repairs=1,
    )

    assert first.fingerprint == second.fingerprint

