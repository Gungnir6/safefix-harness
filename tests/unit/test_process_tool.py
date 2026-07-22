from __future__ import annotations

import sys
from pathlib import Path

import pytest

from safefix.domain import RunProcessAction
from safefix.governance.paths import WorkspaceBoundary
from safefix.tools.process import ProcessTool


@pytest.fixture
def process_tool(tmp_path: Path) -> ProcessTool:
    return ProcessTool(WorkspaceBoundary(tmp_path, ()), output_limit_bytes=1024)


@pytest.mark.asyncio
async def test_process_passes_metacharacters_as_literal_arguments(
    process_tool: ProcessTool,
) -> None:
    action = RunProcessAction(
        id="a1",
        reason="literal",
        program=sys.executable,
        args=("-c", "import sys; print(sys.argv[1])", "; echo injected"),
    )

    result = await process_tool.execute(action)

    assert result.success is True
    assert result.stdout_summary.strip() == "; echo injected"


@pytest.mark.asyncio
async def test_process_timeout_returns_structured_error(
    process_tool: ProcessTool,
) -> None:
    action = RunProcessAction(
        id="a2",
        reason="timeout",
        program=sys.executable,
        args=("-c", "import time; time.sleep(5)"),
    )

    result = await process_tool.execute(action, timeout_seconds=0.05)

    assert result.error_type == "PROCESS_TIMEOUT"


@pytest.mark.asyncio
async def test_process_not_found_returns_structured_error(
    process_tool: ProcessTool,
) -> None:
    action = RunProcessAction(
        id="a3", reason="missing", program="definitely-not-a-real-program-7f85"
    )

    result = await process_tool.execute(action)

    assert result.error_type == "PROCESS_NOT_FOUND"


@pytest.mark.asyncio
async def test_process_output_is_truncated_to_byte_limit(
    process_tool: ProcessTool,
) -> None:
    action = RunProcessAction(
        id="a4",
        reason="bounded output",
        program=sys.executable,
        args=("-c", "print('x' * 5000)"),
    )

    result = await process_tool.execute(action)

    assert result.success is True
    assert len(result.stdout_summary.encode("utf-8")) <= 1024

