from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from safefix.domain import Action


ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


class ActionParseError(ValueError):
    def __init__(self, feedback: str) -> None:
        super().__init__("model action could not be parsed")
        self.feedback = feedback

    @classmethod
    def from_exception(
        cls,
        exc: json.JSONDecodeError | ValidationError | ValueError,
    ) -> ActionParseError:
        if isinstance(exc, json.JSONDecodeError):
            return cls("$: invalid JSON")
        if isinstance(exc, ValidationError):
            return cls(_validation_feedback(exc))
        return cls("$: action must be a JSON object")


class ActionParser:
    def parse(self, text: str) -> Action:
        action: Action | None = None
        error: ActionParseError | None = None
        payload: object | None = None
        try:
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("action must be a JSON object")
            action = ACTION_ADAPTER.validate_python(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
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
        elif isinstance(component, str) and component.replace("_", "").isalnum():
            components.append(f".{component}")
        else:
            components.append(".?")
    return "$" + "".join(components)
