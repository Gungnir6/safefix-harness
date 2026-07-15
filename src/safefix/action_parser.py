from __future__ import annotations

import json
from enum import Enum, auto
from typing import Any, NoReturn

from pydantic import TypeAdapter, ValidationError

from safefix.domain import Action


ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)
_SAFE_LOCATION_COMPONENTS = frozenset(
    {
        "apply_patch",
        "args",
        "end_line",
        "expected_replacements",
        "expected_sha256",
        "file_glob",
        "finish",
        "id",
        "limit",
        "list_files",
        "max_results",
        "new_text",
        "old_text",
        "path",
        "pattern",
        "program",
        "read_file",
        "reason",
        "run_process",
        "run_validation",
        "search_text",
        "start_line",
        "summary",
        "type",
        "validator_id",
    }
)
_MAX_VALIDATION_FEEDBACK_ITEMS = 8


class _StrictJSONError(ValueError):
    pass


class _FailureCode(Enum):
    INVALID_JSON = auto()
    NON_OBJECT = auto()
    INVALID_ACTION = auto()
    INTERNAL = auto()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJSONError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonstandard_constant(constant: str) -> NoReturn:
    del constant
    raise _StrictJSONError("non-standard JSON constant")


def _decode_model_text(text: str) -> tuple[object | None, _FailureCode | None]:
    payload: object | None = None
    try:
        try:
            payload = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_constant,
            )
        except (json.JSONDecodeError, _StrictJSONError, ValueError, RecursionError):
            return None, _FailureCode.INVALID_JSON
        except Exception:
            return None, _FailureCode.INTERNAL
        return payload, None
    finally:
        del text, payload


def _safe_validation_feedback(exc: ValidationError) -> str | None:
    feedback: object | None = None
    try:
        try:
            feedback = _validation_feedback(exc)
        except Exception:
            return None
        return feedback if isinstance(feedback, str) else None
    finally:
        del exc, feedback


def _validate_action_payload(
    payload: dict[str, object],
) -> tuple[Action | None, str | None, _FailureCode | None]:
    action: Action | None = None
    feedback: str | None = None
    try:
        try:
            action = ACTION_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            feedback = _safe_validation_feedback(exc)
            if feedback is None:
                return None, None, _FailureCode.INTERNAL
            return None, feedback, _FailureCode.INVALID_ACTION
        except Exception:
            return None, None, _FailureCode.INTERNAL
        return action, None, None
    finally:
        del payload, action, feedback


class ActionParserInternalError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("action parser internal failure")


class ActionParseError(ValueError):
    def __init__(self, feedback: str) -> None:
        super().__init__("model action could not be parsed")
        self.feedback = f"INVALID_ACTION: {feedback}"


class ActionParser:
    def parse(self, text: str) -> Action:
        action: Action | None = None
        feedback: str | None = None
        failure: _FailureCode | None = None
        payload: object | None = None
        try:
            try:
                payload, failure = _decode_model_text(text)
                if failure is None:
                    if not isinstance(payload, dict):
                        failure = _FailureCode.NON_OBJECT
                    else:
                        action, feedback, failure = _validate_action_payload(payload)
            except Exception:
                failure = _FailureCode.INTERNAL

            if failure is not None:
                if failure is _FailureCode.INTERNAL:
                    raise ActionParserInternalError from None
                if feedback is None:
                    if failure is _FailureCode.INVALID_JSON:
                        feedback = "$: invalid JSON"
                    elif failure is _FailureCode.NON_OBJECT:
                        feedback = "$: action must be a JSON object"
                    else:  # pragma: no cover - helper contract
                        feedback = "$: invalid action"
                raise ActionParseError(feedback) from None

            if action is None:  # pragma: no cover - defensive invariant
                raise ActionParserInternalError from None
            return action
        finally:
            del text, payload, action, feedback


def _validation_feedback(exc: ValidationError) -> str:
    feedback: list[str] = []
    issues: list[Any] = []
    issue: Any = None
    issue_type = ""
    location = ""
    detail = ""
    remaining = 0
    try:
        issues = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        for issue in issues[:_MAX_VALIDATION_FEEDBACK_ITEMS]:
            issue_type = str(issue.get("type", ""))
            location = _safe_location(issue.get("loc", ()))
            if issue_type == "union_tag_invalid":
                location = "$.type"
                detail = "unsupported action type"
            elif issue_type == "union_tag_not_found":
                location = "$.type"
                detail = "field required"
            elif issue_type == "missing":
                detail = "field required"
            elif issue_type == "extra_forbidden":
                detail = "unexpected field"
            else:
                detail = "invalid value"
            feedback.append(f"{location}: {detail}")
        remaining = len(issues) - _MAX_VALIDATION_FEEDBACK_ITEMS
        if remaining > 0:
            feedback.append(f"TRUNCATED: {remaining} additional errors omitted")
        return "; ".join(feedback) or "$: invalid action"
    finally:
        del exc, issues, issue, issue_type, location, detail, remaining, feedback


def _safe_location(raw_location: Any) -> str:
    components: list[str] = []
    component: Any = None
    try:
        if not isinstance(raw_location, tuple):
            return "$"
        for component in raw_location:
            if isinstance(component, int):
                components.append(f"[{component}]")
            elif isinstance(component, str) and component in _SAFE_LOCATION_COMPONENTS:
                components.append(f".{component}")
            else:
                components.append(".?")
        return "$" + "".join(components)
    finally:
        del raw_location, components, component
