from __future__ import annotations

import json
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


class _NonObjectJSONError(ValueError):
    pass


_EXPECTED_INPUT_EXCEPTIONS = (
    json.JSONDecodeError,
    _StrictJSONError,
    ValidationError,
    _NonObjectJSONError,
    RecursionError,
)


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


class ActionParserInternalError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("action parser internal failure")


class ActionParseError(ValueError):
    def __init__(self, feedback: str) -> None:
        super().__init__("model action could not be parsed")
        self.feedback = f"INVALID_ACTION: {feedback}"

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
    ) -> ActionParseError:
        if isinstance(exc, json.JSONDecodeError):
            return cls("$: invalid JSON")
        if isinstance(exc, _StrictJSONError):
            return cls("$: invalid JSON")
        if isinstance(exc, ValidationError):
            return cls(_validation_feedback(exc))
        if isinstance(exc, _NonObjectJSONError):
            return cls("$: action must be a JSON object")
        return cls("$: action parsing failed")


class ActionParser:
    def parse(self, text: str) -> Action:
        action: Action | None = None
        error: ActionParseError | ActionParserInternalError | None = None
        payload: object | None = None
        try:
            payload = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_constant,
            )
            if not isinstance(payload, dict):
                raise _NonObjectJSONError("action must be a JSON object")
            action = ACTION_ADAPTER.validate_python(payload)
        except Exception as exc:
            if isinstance(exc, _EXPECTED_INPUT_EXCEPTIONS):
                error = ActionParseError.from_exception(exc)
            else:
                error = ActionParserInternalError()

        if error is not None:
            del text, payload
            raise error from None
        if action is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("parser produced neither an action nor an error")
        return action


def _validation_feedback(exc: ValidationError) -> str:
    feedback: list[str] = []
    issues = exc.errors(include_url=False, include_context=False, include_input=False)
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


def _safe_location(raw_location: Any) -> str:
    if not isinstance(raw_location, tuple):
        return "$"
    components: list[str] = []
    for component in raw_location:
        if isinstance(component, int):
            components.append(f"[{component}]")
        elif isinstance(component, str) and component in _SAFE_LOCATION_COMPONENTS:
            components.append(f".{component}")
        else:
            components.append(".?")
    return "$" + "".join(components)
