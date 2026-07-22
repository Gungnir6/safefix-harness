from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterable, Mapping

from safefix.config import ValidatorSettings
from safefix.domain import (
    AccessKind,
    RunProcessAction,
    RunValidationAction,
    ToolResult,
)
from safefix.governance.paths import WorkspaceBoundary


_INHERITED_ENVIRONMENT = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
)


def _safe_environment(additions: Mapping[str, str]) -> dict[str, str]:
    environment = {
        key: value
        for key in _INHERITED_ENVIRONMENT
        if (value := os.environ.get(key)) is not None
    }
    environment.update(additions)
    return environment


def _summary(data: bytes, limit: int) -> str:
    return data[:limit].decode("utf-8", errors="replace")


class ProcessTool:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        environment: Mapping[str, str] | None = None,
        output_limit_bytes: int = 65_536,
    ) -> None:
        if output_limit_bytes < 1:
            raise ValueError("output_limit_bytes must be positive")
        self._workspace = boundary.resolve(".", AccessKind.READ)
        self._environment = _safe_environment(environment or {})
        self._output_limit_bytes = output_limit_bytes

    @property
    def action_type(self) -> type[object]:
        return RunProcessAction

    async def execute(
        self,
        action: RunProcessAction,
        *,
        timeout_seconds: float = 60,
        output_limit_bytes: int | None = None,
        success_exit_codes: frozenset[int] = frozenset({0}),
    ) -> ToolResult:
        started_ns = time.perf_counter_ns()
        limit = output_limit_bytes or self._output_limit_bytes
        try:
            process = await asyncio.create_subprocess_exec(
                action.program,
                *action.args,
                cwd=self._workspace,
                env=self._environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return ToolResult.failure(
                action.id, "PROCESS_NOT_FOUND", "process program was not found"
            )
        except OSError:
            return ToolResult.failure(
                action.id, "PROCESS_START_FAILED", "process could not be started"
            )

        try:
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )
            except TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
                return ToolResult(
                    action_id=action.id,
                    success=False,
                    exit_code=process.returncode,
                    stdout_summary=_summary(stdout, limit),
                    stderr_summary=_summary(stderr, limit) or "process timed out",
                    duration_ms=(time.perf_counter_ns() - started_ns) // 1_000_000,
                    error_type="PROCESS_TIMEOUT",
                )
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise

        success = process.returncode in success_exit_codes
        return ToolResult(
            action_id=action.id,
            success=success,
            exit_code=process.returncode,
            stdout_summary=_summary(stdout, limit),
            stderr_summary=_summary(stderr, limit),
            duration_ms=(time.perf_counter_ns() - started_ns) // 1_000_000,
            error_type=None if success else "PROCESS_EXIT_NONZERO",
        )


class ValidatorRunner:
    def __init__(
        self, process_tool: ProcessTool, validators: Iterable[ValidatorSettings]
    ) -> None:
        self._process_tool = process_tool
        self._validators = {validator.id: validator for validator in validators}

    @property
    def action_type(self) -> type[object]:
        return RunValidationAction

    async def execute(self, action: RunValidationAction) -> ToolResult:
        return await self._run(action.validator_id, action.id)

    async def run(self, validator_id: str) -> ToolResult:
        return await self._run(validator_id, validator_id)

    async def _run(self, validator_id: str, action_id: str) -> ToolResult:
        validator = self._validators.get(validator_id)
        if validator is None:
            return ToolResult.failure(
                action_id,
                "VALIDATOR_NOT_FOUND",
                "validator id is not configured",
            )
        action = RunProcessAction(
            id=action_id,
            reason=f"run configured validator {validator.id}",
            program=validator.program,
            args=validator.args,
        )
        return await self._process_tool.execute(
            action,
            timeout_seconds=validator.timeout_seconds,
            output_limit_bytes=validator.output_limit_bytes,
            success_exit_codes=validator.success_exit_codes,
        )
