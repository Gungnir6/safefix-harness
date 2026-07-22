from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, NoReturn

from safefix.domain import (
    Action,
    ApplyPatchAction,
    FinishAction,
    ListFilesAction,
    ReadFileAction,
    RunProcessAction,
    RunValidationAction,
    SearchTextAction,
    ToolResult,
)
from safefix.tools.base import Tool


_ACTION_TYPES = (
    ListFilesAction,
    ReadFileAction,
    SearchTextAction,
    ApplyPatchAction,
    RunValidationAction,
    RunProcessAction,
    FinishAction,
)


@dataclass(frozen=True, slots=True)
class _DispatchOutcome:
    kind: Literal["dispatch", "missing", "invalid"]
    tool: Tool | None = None
    action: Action | None = None
    action_id: str | None = None


def _capture_dispatch(
    tools: Mapping[type[object], Tool], raw: object
) -> _DispatchOutcome:
    if not isinstance(raw, _ACTION_TYPES):
        del raw
        return _DispatchOutcome("invalid")
    tool = tools.get(type(raw))
    if tool is None:
        action_id = raw.id
        del raw
        return _DispatchOutcome("missing", action_id=action_id)
    return _DispatchOutcome("dispatch", tool=tool, action=raw)


def _raise_invalid_dispatch() -> NoReturn:
    raise TypeError("dispatch requires a structured Action")


def _capture_registration(
    tools: dict[type[object], Tool], tool: Tool
) -> Literal["registered", "duplicate", "invalid"]:
    action_type: type[object] | None = None
    known_type: type[object] | None = None
    property_failed = False
    valid_type = False
    try:
        try:
            action_type = tool.action_type
        except Exception:
            property_failed = True
        if property_failed:
            return "invalid"
        for known_type in _ACTION_TYPES:
            if action_type is known_type:
                valid_type = True
                break
        if not valid_type:
            return "invalid"
        assert action_type is not None
        if action_type in tools:
            return "duplicate"
        tools[action_type] = tool
        return "registered"
    finally:
        del tools, tool, action_type, known_type, property_failed, valid_type


def _raise_invalid_registration() -> NoReturn:
    raise TypeError("tool action_type must be a structured Action class")


def _raise_duplicate_registration() -> NoReturn:
    raise ValueError("tool is already registered for action type")


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        tool: Tool | None = None
        try:
            self._tools: dict[type[object], Tool] = {}
            for tool in tools:
                self.register(tool)
        finally:
            del self, tools, tool

    def register(self, tool: Tool) -> None:
        outcome = ""
        try:
            outcome = _capture_registration(self._tools, tool)
        finally:
            del self, tool
        if outcome == "invalid":
            del outcome
            _raise_invalid_registration()
        if outcome == "duplicate":
            del outcome
            _raise_duplicate_registration()
        del outcome

    async def dispatch(self, action: object) -> ToolResult:
        outcome = _capture_dispatch(self._tools, action)
        del action
        if outcome.kind == "invalid":
            del outcome
            _raise_invalid_dispatch()
        if outcome.kind == "missing":
            action_id = outcome.action_id
            del outcome
            assert action_id is not None
            return ToolResult.failure(
                action_id,
                "TOOL_NOT_FOUND",
                "no tool is registered for this action",
            )
        tool = outcome.tool
        typed_action = outcome.action
        del outcome
        assert tool is not None
        assert typed_action is not None
        try:
            return await tool.execute(typed_action)
        finally:
            del tool, typed_action
