from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Literal

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


class SensitiveTool:
    def __init__(
        self,
        behavior: Literal["read", "invalid", "oserror", "interrupt"],
        interrupt: BaseException | None = None,
    ) -> None:
        self.behavior = behavior
        self.interrupt = interrupt

    def __repr__(self) -> str:
        return "PRIVATE-TOOL-REPR"

    @property
    def action_type(self) -> type[object]:
        if self.behavior == "invalid":
            return SensitiveActionType
        if self.behavior == "oserror":
            raise OSError("PRIVATE-ACTION-TYPE-ERROR")
        if self.behavior == "interrupt":
            assert self.interrupt is not None
            raise self.interrupt
        return ReadFileAction

    async def execute(self, action: Action) -> ToolResult:
        raise AssertionError("not called")


class SensitiveActionType:
    def __repr__(self) -> str:
        return "PRIVATE-ACTION-TYPE-REPR"


def _registry_frame_locals(error: BaseException) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == "safefix.tools.registry":
            frames.append(dict(tb.tb_frame.f_locals))
        tb = tb.tb_next
    assert frames, "registry traceback frame was not found"
    return frames


def _assert_registry_error_is_clean(
    error: BaseException,
    expected_type: type[BaseException],
    expected_message: str,
) -> None:
    assert type(error) is expected_type
    assert str(error) == expected_message
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    for sentinel in (
        "PRIVATE-TOOL-REPR",
        "PRIVATE-ACTION-TYPE-REPR",
        "PRIVATE-ACTION-TYPE-ERROR",
    ):
        assert sentinel not in rendered
    forbidden_names = {"self", "tools", "tool", "action_type", "error"}
    for frame_locals in _registry_frame_locals(error):
        assert forbidden_names.isdisjoint(frame_locals)
        local_rendering = repr(frame_locals)
        for sentinel in (
            "PRIVATE-TOOL-REPR",
            "PRIVATE-ACTION-TYPE-REPR",
            "PRIVATE-ACTION-TYPE-ERROR",
        ):
            assert sentinel not in local_rendering


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


@pytest.mark.parametrize("entrypoint", ["constructor", "register"])
def test_registry_duplicate_error_is_fully_non_disclosing(entrypoint: str) -> None:
    first = SensitiveTool("read")
    duplicate = SensitiveTool("read")
    try:
        if entrypoint == "constructor":
            ToolRegistry((first, duplicate))
        else:
            registry = ToolRegistry((first,))
            registry.register(duplicate)
    except ValueError as error:
        _assert_registry_error_is_clean(
            error,
            ValueError,
            "tool is already registered for action type",
        )
    else:
        pytest.fail("ValueError was not raised")


@pytest.mark.parametrize("entrypoint", ["constructor", "register"])
@pytest.mark.parametrize("behavior", ["invalid", "oserror"])
def test_registry_invalid_action_type_is_fully_non_disclosing(
    entrypoint: str, behavior: Literal["invalid", "oserror"]
) -> None:
    tool = SensitiveTool(behavior)
    try:
        if entrypoint == "constructor":
            ToolRegistry((tool,))
        else:
            ToolRegistry().register(tool)
    except TypeError as error:
        _assert_registry_error_is_clean(
            error,
            TypeError,
            "tool action_type must be a structured Action class",
        )
    else:
        pytest.fail("TypeError was not raised")


@pytest.mark.parametrize("entrypoint", ["constructor", "register"])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_registry_action_type_process_control_is_exact_and_cleans_frames(
    entrypoint: str, exception_type: type[BaseException]
) -> None:
    interrupt = exception_type("EXPECTED-INTERRUPT")
    tool = SensitiveTool("interrupt", interrupt)
    with pytest.raises(exception_type) as error_info:
        if entrypoint == "constructor":
            ToolRegistry((tool,))
        else:
            ToolRegistry().register(tool)

    assert error_info.value is interrupt
    assert interrupt.__cause__ is None
    assert interrupt.__context__ is None
    for frame_locals in _registry_frame_locals(interrupt):
        assert {"self", "tools", "tool", "action_type"}.isdisjoint(frame_locals)
        assert "PRIVATE-TOOL-REPR" not in repr(frame_locals)


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
