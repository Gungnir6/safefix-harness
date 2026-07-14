from datetime import UTC, datetime
from enum import Enum
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from safefix.domain import (
    AccessKind,
    Action,
    ApplyPatchAction,
    ApprovalRequest,
    ApprovalStatus,
    BudgetState,
    DecisionOutcome,
    Feedback,
    FeedbackCategory,
    FinishAction,
    ListFilesAction,
    PolicyDecision,
    ProgressResult,
    ReadFileAction,
    RiskLevel,
    RunProcessAction,
    RunSnapshot,
    RunStatus,
    RunValidationAction,
    SearchTextAction,
    StopDecision,
    Task,
    TaskMode,
    ToolResult,
    action_digest,
)


ACTION_CASES: tuple[tuple[type[BaseModel], dict[str, Any], str], ...] = (
    (ListFilesAction, {"id": "a1", "reason": "inspect"}, "list_files"),
    (
        ReadFileAction,
        {"id": "a1", "reason": "inspect", "path": "src/app.py"},
        "read_file",
    ),
    (
        SearchTextAction,
        {"id": "a1", "reason": "inspect", "pattern": "needle"},
        "search_text",
    ),
    (
        ApplyPatchAction,
        {
            "id": "a1",
            "reason": "repair",
            "path": "src/app.py",
            "expected_sha256": "0" * 64,
            "old_text": "before",
            "new_text": "after",
        },
        "apply_patch",
    ),
    (
        RunValidationAction,
        {"id": "a1", "reason": "check", "validator_id": "pytest"},
        "run_validation",
    ),
    (
        RunProcessAction,
        {"id": "a1", "reason": "check", "program": "python"},
        "run_process",
    ),
    (
        FinishAction,
        {"id": "a1", "reason": "done", "summary": "fixed"},
        "finish",
    ),
)


MODEL_CLASSES = (
    *(case[0] for case in ACTION_CASES),
    Task,
    BudgetState,
    ToolResult,
    PolicyDecision,
    Feedback,
    ProgressResult,
    StopDecision,
    ApprovalRequest,
    RunSnapshot,
)


def snapshot_data(**updates: Any) -> dict[str, Any]:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    data: dict[str, Any] = {
        "run_id": "run-1",
        "task_id": "task-1",
        "project_id": "project-1",
        "workspace_root": "C:/workspace",
        "description": "repair the project",
        "status": RunStatus.NO_PROGRESS,
        "repair_round": 1,
        "step_count": 2,
        "budget": BudgetState(
            max_steps=10,
            remaining_steps=8,
            max_repair_rounds=3,
            remaining_repairs=2,
        ),
        "version": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    data.update(updates)
    return data


def test_action_digest_is_stable_for_equal_actions() -> None:
    first = ReadFileAction(
        id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=20
    )
    second = ReadFileAction(
        id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=20
    )
    assert first.type == "read_file"
    assert action_digest(first) == action_digest(second)


def test_action_digest_changes_when_payload_changes() -> None:
    first = ReadFileAction(
        id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=20
    )
    second = ReadFileAction(
        id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=21
    )
    assert action_digest(first) != action_digest(second)


@pytest.mark.parametrize(
    ("model_type", "kwargs", "discriminator"),
    ACTION_CASES,
    ids=[case[2] for case in ACTION_CASES],
)
def test_every_action_forbids_extra_fields_and_serializes_discriminator(
    model_type: type[BaseModel],
    kwargs: dict[str, Any],
    discriminator: str,
) -> None:
    action = model_type(**kwargs)
    assert action.model_dump()["type"] == discriminator
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model_type(**kwargs, unknown_field=True)


@pytest.mark.parametrize(
    ("model_type", "kwargs", "discriminator"),
    ACTION_CASES,
    ids=[case[2] for case in ACTION_CASES],
)
def test_action_union_uses_type_discriminator(
    model_type: type[BaseModel],
    kwargs: dict[str, Any],
    discriminator: str,
) -> None:
    payload = {**kwargs, "type": discriminator}
    assert isinstance(TypeAdapter(Action).validate_python(payload), model_type)


@pytest.mark.parametrize(
    "values",
    (
        {"path": "src/app.py", "start_line": 0, "end_line": 1},
        {"path": "src/app.py", "start_line": 10, "end_line": 9},
        {"path": "src/app.py", "start_line": 1, "end_line": 501},
    ),
    ids=("start-before-one", "end-before-start", "span-over-500"),
)
def test_read_file_rejects_invalid_line_ranges(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReadFileAction(id="a1", reason="inspect", **values)


def test_run_process_rejects_whitespace_only_program() -> None:
    with pytest.raises(ValidationError):
        RunProcessAction(id="a1", reason="check", program="   ")


@pytest.mark.parametrize("field", ("remaining_steps", "remaining_repairs"))
def test_budget_rejects_negative_remaining_values(field: str) -> None:
    values = {
        "max_steps": 10,
        "remaining_steps": 5,
        "max_repair_rounds": 3,
        "remaining_repairs": 2,
    }
    values[field] = -1
    with pytest.raises(ValidationError):
        BudgetState(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    (("remaining_steps", 11), ("remaining_repairs", 4)),
)
def test_budget_rejects_remaining_values_above_maximum(
    field: str,
    value: int,
) -> None:
    values = {
        "max_steps": 10,
        "remaining_steps": 5,
        "max_repair_rounds": 3,
        "remaining_repairs": 2,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        BudgetState(**values)


@pytest.mark.parametrize("model_type", MODEL_CLASSES)
def test_every_model_is_frozen_and_forbids_extra_fields(
    model_type: type[BaseModel],
) -> None:
    assert model_type.model_config["frozen"] is True
    assert model_type.model_config["extra"] == "forbid"


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    (
        (TaskMode, {"LOCAL": "local", "PUBLIC_DEMO": "public-demo"}),
        (
            DecisionOutcome,
            {
                "ALLOW": "ALLOW",
                "REQUIRE_APPROVAL": "REQUIRE_APPROVAL",
                "DENY": "DENY",
            },
        ),
        (RiskLevel, {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH"}),
        (
            RunStatus,
            {
                "CREATED": "CREATED",
                "RUNNING": "RUNNING",
                "AWAITING_APPROVAL": "AWAITING_APPROVAL",
                "SUCCESS": "SUCCESS",
                "BLOCKED": "BLOCKED",
                "NO_PROGRESS": "NO_PROGRESS",
                "BUDGET_EXCEEDED": "BUDGET_EXCEEDED",
                "FAILED": "FAILED",
                "CANCELLED": "CANCELLED",
            },
        ),
        (
            FeedbackCategory,
            {
                "VALIDATION_SUCCESS": "VALIDATION_SUCCESS",
                "TEST_FAILURE": "TEST_FAILURE",
                "LINT_FAILURE": "LINT_FAILURE",
                "TYPE_ERROR": "TYPE_ERROR",
                "TIMEOUT": "TIMEOUT",
                "TOOL_ERROR": "TOOL_ERROR",
                "POLICY_REJECTION": "POLICY_REJECTION",
            },
        ),
        (
            ApprovalStatus,
            {
                "PENDING": "PENDING",
                "APPROVED": "APPROVED",
                "REJECTED": "REJECTED",
                "EXPIRED": "EXPIRED",
                "CANCELLED": "CANCELLED",
            },
        ),
        (
            AccessKind,
            {"READ": "READ", "WRITE": "WRITE", "LIST": "LIST", "SEARCH": "SEARCH"},
        ),
    ),
)
def test_enums_have_exact_values(
    enum_type: type[Enum],
    expected: dict[str, str],
) -> None:
    assert {member.name: member.value for member in enum_type} == expected


def test_tool_result_failure_uses_failure_defaults() -> None:
    result = ToolResult.failure("a1", "Timeout", "validation timed out")
    assert result == ToolResult(
        action_id="a1",
        success=False,
        stderr_summary="validation timed out",
        error_type="Timeout",
    )


def test_stop_decision_uses_run_status_code() -> None:
    decision = StopDecision(code=RunStatus.NO_PROGRESS, reason="no useful changes")
    assert decision.model_dump(mode="json") == {
        "code": "NO_PROGRESS",
        "reason": "no useful changes",
    }


def test_run_snapshot_accepts_plain_string_stop_reason() -> None:
    snapshot = RunSnapshot(**snapshot_data(stop_reason="no useful changes"))
    assert snapshot.stop_reason == "no useful changes"


def test_run_snapshot_rejects_stop_decision_as_stop_reason() -> None:
    decision = StopDecision(code=RunStatus.NO_PROGRESS, reason="no useful changes")
    with pytest.raises(ValidationError):
        RunSnapshot(**snapshot_data(stop_reason=decision))
