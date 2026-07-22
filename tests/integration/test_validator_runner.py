from __future__ import annotations

import sys
from pathlib import Path

import pytest

from safefix.config import ValidatorSettings
from safefix.domain import RunValidationAction
from safefix.governance.paths import WorkspaceBoundary
from safefix.tools.process import ProcessTool, ValidatorRunner


def _validator(identifier: str, code: str) -> ValidatorSettings:
    return ValidatorSettings(
        id=identifier,
        kind="test",
        program=sys.executable,
        args=("-c", code),
        timeout_seconds=2,
        success_exit_codes=frozenset({0}),
        output_limit_bytes=1024,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_validator_runner_uses_configured_command(workspace: Path) -> None:
    runner = ValidatorRunner(
        ProcessTool(WorkspaceBoundary(workspace, ())),
        (_validator("tests", "print('validator ok')"),),
    )

    result = await runner.run("tests")

    assert result.success is True
    assert result.stdout_summary.strip() == "validator ok"


@pytest.mark.asyncio
async def test_validator_runner_executes_structured_action(workspace: Path) -> None:
    runner = ValidatorRunner(
        ProcessTool(WorkspaceBoundary(workspace, ())),
        (_validator("tests", "print('ok')"),),
    )
    action = RunValidationAction(
        id="validate-1", reason="check", validator_id="tests"
    )

    result = await runner.execute(action)

    assert runner.action_type is RunValidationAction
    assert result.success is True
    assert result.action_id == "validate-1"


@pytest.mark.asyncio
async def test_validator_runner_reports_failure_and_unknown_id(workspace: Path) -> None:
    runner = ValidatorRunner(
        ProcessTool(WorkspaceBoundary(workspace, ())),
        (_validator("tests", "raise SystemExit(3)"),),
    )

    failed = await runner.run("tests")
    unknown = await runner.run("missing")

    assert failed.success is False
    assert failed.exit_code == 3
    assert failed.error_type == "PROCESS_EXIT_NONZERO"
    assert unknown.error_type == "VALIDATOR_NOT_FOUND"
