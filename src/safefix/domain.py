from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SearchPattern = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _normalize_to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_normalize_to_utc)]


class TaskMode(str, Enum):
    LOCAL = "local"
    PUBLIC_DEMO = "public-demo"


class DecisionOutcome(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    NO_PROGRESS = "NO_PROGRESS"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FeedbackCategory(str, Enum):
    VALIDATION_SUCCESS = "VALIDATION_SUCCESS"
    TEST_FAILURE = "TEST_FAILURE"
    LINT_FAILURE = "LINT_FAILURE"
    TYPE_ERROR = "TYPE_ERROR"
    TIMEOUT = "TIMEOUT"
    TOOL_ERROR = "TOOL_ERROR"
    POLICY_REJECTION = "POLICY_REJECTION"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class AccessKind(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    LIST = "LIST"
    SEARCH = "SEARCH"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _ActionBase(_FrozenModel):
    id: NonEmptyStr
    reason: NonEmptyStr


class ListFilesAction(_ActionBase):
    type: Literal["list_files"] = "list_files"
    path: NonEmptyStr = "."
    pattern: NonEmptyStr = "**/*"
    limit: int = Field(100, ge=1, le=1000)


class ReadFileAction(_ActionBase):
    type: Literal["read_file"] = "read_file"
    path: NonEmptyStr
    start_line: int = Field(1, ge=1)
    end_line: int = Field(200, ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.end_line - self.start_line + 1 > 500:
            raise ValueError("line span must not exceed 500")
        return self


class SearchTextAction(_ActionBase):
    type: Literal["search_text"] = "search_text"
    pattern: SearchPattern
    path: NonEmptyStr = "."
    file_glob: NonEmptyStr = "**/*"
    max_results: int = Field(50, ge=1, le=200)


class ApplyPatchAction(_ActionBase):
    type: Literal["apply_patch"] = "apply_patch"
    path: NonEmptyStr
    expected_sha256: Sha256Hex
    old_text: NonEmptyStr
    new_text: str
    expected_replacements: int = Field(1, ge=1, le=100)


class RunValidationAction(_ActionBase):
    type: Literal["run_validation"] = "run_validation"
    validator_id: NonEmptyStr


class RunProcessAction(_ActionBase):
    type: Literal["run_process"] = "run_process"
    program: NonEmptyStr
    args: tuple[str, ...] = ()


class FinishAction(_ActionBase):
    type: Literal["finish"] = "finish"
    summary: NonEmptyStr


Action = Annotated[
    ListFilesAction
    | ReadFileAction
    | SearchTextAction
    | ApplyPatchAction
    | RunValidationAction
    | RunProcessAction
    | FinishAction,
    Field(discriminator="type"),
]


def action_digest(action: Action) -> str:
    canonical = action.model_dump_json(exclude_none=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Task(_FrozenModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    workspace_root: NonEmptyStr
    description: NonEmptyStr
    mode: TaskMode
    created_at: datetime


class BudgetState(_FrozenModel):
    max_steps: int = Field(ge=1)
    remaining_steps: int = Field(ge=0)
    max_repair_rounds: int = Field(ge=1)
    remaining_repairs: int = Field(ge=0)
    deadline_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_remaining_budget(self) -> Self:
        if self.remaining_steps > self.max_steps:
            raise ValueError("remaining_steps must not exceed max_steps")
        if self.remaining_repairs > self.max_repair_rounds:
            raise ValueError("remaining_repairs must not exceed max_repair_rounds")
        return self


class ToolResult(_FrozenModel):
    action_id: str
    success: bool
    exit_code: int | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    changed_files: tuple[str, ...] = ()
    duration_ms: int = 0
    error_type: str | None = None

    @classmethod
    def failure(cls, action_id: str, error_type: str, message: str) -> Self:
        return cls(
            action_id=action_id,
            success=False,
            stderr_summary=message,
            error_type=error_type,
        )


class PolicyDecision(_FrozenModel):
    action_id: str
    outcome: DecisionOutcome
    risk_level: RiskLevel
    rule_ids: tuple[str, ...]
    explanation: str


class Feedback(_FrozenModel):
    category: FeedbackCategory
    summary: str
    failure_count: int
    fingerprint: str
    remaining_steps: int
    remaining_repairs: int
    changed_files: tuple[str, ...] = ()


class ProgressResult(_FrozenModel):
    made_progress: bool
    reason: str


class StopDecision(_FrozenModel):
    code: RunStatus
    reason: str


class ApprovalRequest(_FrozenModel):
    id: str
    run_id: str
    action_hash: str
    status: ApprovalStatus
    one_time_token_hash: str
    frozen_action_json: str
    created_at: datetime
    expires_at: datetime
    rule_ids: tuple[str, ...] = ()
    decided_at: datetime | None = None


class RunSnapshot(_FrozenModel):
    run_id: str
    task_id: str
    project_id: str
    workspace_root: str
    description: str
    status: RunStatus
    repair_round: int
    step_count: int
    budget: BudgetState
    version: int
    pending_approval_id: str | None = None
    action_digests: tuple[str, ...] = ()
    feedback_history: tuple[Feedback, ...] = ()
    latest_tool_result: ToolResult | None = None
    changed_files: tuple[str, ...] = ()
    stop_reason: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
