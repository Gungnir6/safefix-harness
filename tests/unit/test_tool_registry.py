from __future__ import annotations

import traceback
from dataclasses import dataclass, field

import pytest

from safefix.domain import (
    Action,
    FinishAction,
    ReadFileAction,
    ToolResult,
)
from safefix.tools.registry import ToolRegistry


@dataclass
class RecordingReadTool:
    calls: list[Action] = field(default_factory=list)

    @property
    def action_type(self) -> type[object]:
        return ReadFileAction

    async def execute(self, action: Action) -> ToolResult:
        self.calls.append(action)
        return ToolResult(action_id=action.id, success=True, stdout_summary="ok")


@pytest.mark.asyncio
async def test_registry_dispatches_exact_action_class_once() -> None:
    tool = RecordingReadTool()
    registry = ToolRegistry((tool,))
    action = ReadFileAction(  # type: ignore[call-arg]
        id="a1", reason="inspect", path="app.py"
    )

    result = await registry.dispatch(action)

    assert result == ToolResult(action_id="a1", success=True, stdout_summary="ok")
    assert tool.calls == [action]


@pytest.mark.asyncio
async def test_registry_returns_failure_when_tool_is_missing() -> None:
    action = FinishAction(id="a2", reason="done", summary="complete")

    result = await ToolRegistry().dispatch(action)

    assert result == ToolResult.failure(
        "a2", "TOOL_NOT_FOUND", "no tool is registered for this action"
    )


def test_registry_rejects_duplicate_action_type() -> None:
    registry = ToolRegistry((RecordingReadTool(),))

    with pytest.raises(
        ValueError, match="^tool is already registered for action type$"
    ):
        registry.register(RecordingReadTool())


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["read_file", {"type": "read_file"}, object()])
async def test_registry_rejects_untyped_inputs(raw: object) -> None:
    with pytest.raises(TypeError, match="^dispatch requires a structured Action$"):
        await ToolRegistry().dispatch(raw)


@pytest.mark.asyncio
async def test_registry_traceback_locals_do_not_retain_raw_input() -> None:
    sentinel = "TOP-SECRET-RAW-ACTION"
    try:
        await ToolRegistry().dispatch(sentinel)
    except TypeError as exc:
        frames = [
            frame
            for frame in traceback.extract_tb(exc.__traceback__)
            if frame.filename.replace("\\", "/").endswith("safefix/tools/registry.py")
        ]
        local_values: list[str] = []
        tb = exc.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_filename.replace("\\", "/").endswith(
                "safefix/tools/registry.py"
            ):
                local_values.extend(
                    repr(value) for value in tb.tb_frame.f_locals.values()
                )
            tb = tb.tb_next
        assert frames
        assert all(sentinel not in value for value in local_values)
        assert exc.__cause__ is None
        assert exc.__context__ is None
    else:
        pytest.fail("TypeError was not raised")
