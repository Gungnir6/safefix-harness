from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from safefix.domain import (
    BudgetState,
    Feedback,
    FeedbackCategory,
    ProgressResult,
    RunStatus,
    StopDecision,
    ToolResult,
)


_FAILED_COUNT = re.compile(r"\b(\d+)\s+(?:tests?\s+)?failed\b", re.IGNORECASE)
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s:]+")
_POSIX_TEMP_PATH = re.compile(r"(?<!\w)/(?:tmp|private/tmp|var/tmp)/[^\s:]+")
_INTERESTING_LINE = re.compile(
    r"fail|error|timeout|denied|exception|\bE\d{3,4}\b", re.IGNORECASE
)
_SEVERITY = {
    FeedbackCategory.VALIDATION_SUCCESS: 0,
    FeedbackCategory.TEST_FAILURE: 1,
    FeedbackCategory.LINT_FAILURE: 2,
    FeedbackCategory.TYPE_ERROR: 3,
    FeedbackCategory.TIMEOUT: 4,
    FeedbackCategory.TOOL_ERROR: 5,
    FeedbackCategory.POLICY_REJECTION: 6,
}


def _category(result: ToolResult) -> FeedbackCategory:
    error_type = (result.error_type or "").upper()
    action_id = result.action_id.casefold()
    if "TIMEOUT" in error_type:
        return FeedbackCategory.TIMEOUT
    if "POLICY" in error_type or error_type.endswith("_DENIED"):
        return FeedbackCategory.POLICY_REJECTION
    if error_type and error_type != "PROCESS_EXIT_NONZERO":
        return FeedbackCategory.TOOL_ERROR
    if any(name in action_id for name in ("ruff", "flake", "pylint", "lint")):
        return FeedbackCategory.LINT_FAILURE
    if any(name in action_id for name in ("mypy", "pyright", "type")):
        return FeedbackCategory.TYPE_ERROR
    return FeedbackCategory.TEST_FAILURE


def _normalize(text: str) -> str:
    text = _TIMESTAMP.sub("<timestamp>", text)
    text = _WINDOWS_PATH.sub("<path>", text)
    return _POSIX_TEMP_PATH.sub("<path>", text)


def _failure_summary(results: Sequence[ToolResult]) -> str:
    lines: list[str] = []
    for result in results:
        text = _normalize("\n".join((result.stdout_summary, result.stderr_summary)))
        candidates = [line.strip() for line in text.splitlines() if line.strip()]
        interesting = [line for line in candidates if _INTERESTING_LINE.search(line)]
        for line in (interesting or candidates)[:20]:
            lines.append(line[:300])
            if len(lines) >= 20:
                break
        if not candidates and result.error_type:
            lines.append(result.error_type)
        if len(lines) >= 20:
            break
    return "\n".join(lines)[:4000] or "validation failed"


def _failure_count(results: Sequence[ToolResult]) -> int:
    count = 0
    for result in results:
        text = "\n".join((result.stdout_summary, result.stderr_summary))
        matches = [int(match) for match in _FAILED_COUNT.findall(text)]
        count += max(matches) if matches else 1
    return count


class FeedbackEngine:
    def __init__(self, *, no_progress_limit: int = 2) -> None:
        if no_progress_limit < 1:
            raise ValueError("no_progress_limit must be positive")
        self._no_progress_limit = no_progress_limit

    def from_results(
        self,
        results: Iterable[ToolResult],
        changed_files: Iterable[str],
        remaining_steps: int,
        remaining_repairs: int,
    ) -> Feedback:
        collected = tuple(results)
        failed = tuple(result for result in collected if not result.success)
        if failed:
            category = max(
                (_category(result) for result in failed),
                key=lambda item: _SEVERITY[item],
            )
            summary = _failure_summary(failed)
            failure_count = _failure_count(failed)
        elif collected:
            category = FeedbackCategory.VALIDATION_SUCCESS
            summary = "validation succeeded"
            failure_count = 0
        else:
            category = FeedbackCategory.TOOL_ERROR
            summary = "no validator results"
            failure_count = 1

        fingerprint = hashlib.sha256(
            json.dumps(
                {"category": category.value, "failures": summary},
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return Feedback(
            category=category,
            summary=summary,
            failure_count=failure_count,
            fingerprint=fingerprint,
            remaining_steps=remaining_steps,
            remaining_repairs=remaining_repairs,
            changed_files=tuple(dict.fromkeys(changed_files)),
        )

    def compare(self, previous: Feedback, current: Feedback) -> ProgressResult:
        if current.category == FeedbackCategory.VALIDATION_SUCCESS:
            return ProgressResult(made_progress=True, reason="validation succeeded")
        if current.failure_count < previous.failure_count:
            return ProgressResult(made_progress=True, reason="failure count decreased")
        if current.failure_count > previous.failure_count:
            return ProgressResult(made_progress=False, reason="failure count increased")
        if (
            current.fingerprint != previous.fingerprint
            and _SEVERITY[current.category] <= _SEVERITY[previous.category]
        ):
            return ProgressResult(
                made_progress=True, reason="failure fingerprint changed"
            )
        return ProgressResult(made_progress=False, reason="no objective progress")

    def should_stop(
        self,
        history: Sequence[Feedback],
        budget: BudgetState,
        action_digests: Sequence[str] = (),
    ) -> StopDecision | None:
        if history and history[-1].category == FeedbackCategory.VALIDATION_SUCCESS:
            return StopDecision(code=RunStatus.SUCCESS, reason="validation succeeded")
        if budget.remaining_steps == 0:
            return StopDecision(
                code=RunStatus.BUDGET_EXCEEDED, reason="step budget exhausted"
            )
        if budget.remaining_repairs == 0:
            return StopDecision(
                code=RunStatus.BUDGET_EXCEEDED, reason="repair budget exhausted"
            )
        if budget.deadline_at is not None and datetime.now(UTC) >= budget.deadline_at:
            return StopDecision(
                code=RunStatus.BUDGET_EXCEEDED, reason="time budget exhausted"
            )
        if len(action_digests) >= self._no_progress_limit:
            recent_actions = action_digests[-self._no_progress_limit :]
            if len(set(recent_actions)) == 1:
                return StopDecision(
                    code=RunStatus.NO_PROGRESS, reason="action repeated without progress"
                )
        if len(history) >= self._no_progress_limit:
            recent = history[-self._no_progress_limit :]
            if len({feedback.fingerprint for feedback in recent}) == 1:
                return StopDecision(
                    code=RunStatus.NO_PROGRESS,
                    reason="validation failure repeated without progress",
                )
        return None
