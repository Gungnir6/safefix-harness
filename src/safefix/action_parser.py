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


class _StrictJSONError(ValueError):
    pass


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
        if isinstance(exc, ValueError):
            return cls("$: action must be a JSON object")
        return cls("$: action parsing failed")


class ActionParser:
    def parse(self, text: str) -> Action:
        action: Action | None = None
        error: ActionParseError | None = None
        payload: object | None = None
        try:
            payload = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_constant,
            )
            if not isinstance(payload, dict):
                raise ValueError("action must be a JSON object")
            action = ACTION_ADAPTER.validate_python(payload)
        except Exception as exc:
            error = ActionParseError.from_exception(exc)

        if error is not None:
            del text, payload
            raise error from None
        if action is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("parser produced neither an action nor an error")
        return action


def _validation_feedback(exc: ValidationError) -> str:
    feedback: list[str] = []
    for issue in exc.errors(
        include_url=False, include_context=False, include_input=False
    ):
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
