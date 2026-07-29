from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
import sqlite3
import threading
from typing import Any, NoReturn, cast

import httpx

from safefix.action_parser import ActionParser
from safefix.agent_loop import AgentLoop
from safefix.config import SafeFixSettings
from safefix.context import ContextBuilder
from safefix.credentials import CredentialService
from safefix.execution_workspace import PreparedWorkspace
from safefix.feedback import FeedbackEngine
from safefix.governance.approvals import ApprovalStateMachine
from safefix.governance.audit import AuditEvent, AuditStore
from safefix.governance.paths import WorkspaceBoundary
from safefix.governance.policy import PolicyEngine
from safefix.llm.mock import ScriptedMockLLM
from safefix.llm.openai_compatible import OpenAICompatibleClient
from safefix.memory import MemoryStore
from safefix.run_store import RunStore
from safefix.task_service import TaskService
from safefix.tools.base import Tool
from safefix.tools.filesystem import (
    ApplyPatchTool,
    ListFilesTool,
    ReadFileTool,
    SearchTextTool,
)
from safefix.tools.process import ProcessTool, ValidatorRunner
from safefix.tools.registry import ToolRegistry


class RuntimeConfigurationError(RuntimeError):
    """The requested production runtime cannot be assembled safely."""


class _BuildFailure(Enum):
    CONFIGURATION = auto()
    KEYBOARD_INTERRUPT = auto()
    SYSTEM_EXIT = auto()


@dataclass(frozen=True, slots=True)
class _BuildFailureResult:
    kind: _BuildFailure
    system_exit_code: int | None = None


@dataclass(slots=True)
class RuntimeSession:
    service: TaskService
    audit: AuditStore
    database_path: Path
    model_name: str
    provider: str
    _connection: sqlite3.Connection = field(repr=False)
    _http: httpx.AsyncClient = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[AuditEvent]:
        return [
            event
            for event in self.audit.list_events(run_id)
            if event.sequence > after_sequence
        ]

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._http.aclose()
        finally:
            self._connection.close()


def _close_http_in_worker(http: httpx.AsyncClient) -> None:
    def close() -> None:
        try:
            asyncio.run(http.aclose())
        except BaseException:
            pass

    worker = threading.Thread(target=close)
    worker.start()
    worker.join()


def _close_failed_http(http: httpx.AsyncClient) -> None:
    _close_http_in_worker(http)


def _fallback_failed_http_close(http: httpx.AsyncClient) -> None:
    _close_http_in_worker(http)


def _scrub_secret_holders(
    audit: AuditStore | None,
    memory: MemoryStore | None,
    approvals: ApprovalStateMachine | None,
) -> None:
    if audit is not None:
        audit._secrets = ()
    if memory is not None:
        memory._secrets = ()
    if approvals is not None:
        approvals._secrets = ()
        approvals._audit._secrets = ()


def _build_runtime(
    settings: SafeFixSettings,
    prepared: PreparedWorkspace,
    database_path: Path,
    *,
    provider: str,
    credential_service: CredentialService,
    mock_actions: Sequence[str] | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> RuntimeSession | _BuildFailureResult:
    secret_values: tuple[str, ...] = ()
    secret_obtained = False
    connection: sqlite3.Connection | None = None
    http: httpx.AsyncClient | None = None
    audit: AuditStore | None = None
    memory: MemoryStore | None = None
    approvals: ApprovalStateMachine | None = None
    llm: Any = None
    loop: AgentLoop | None = None
    service: TaskService | None = None
    failure: BaseException | None = None
    failure_kind: _BuildFailure | None = None
    system_exit_code: int | None = None
    try:
        if provider == "openai-compatible":
            secret_values = (credential_service.get_for_request(provider),)
            secret_obtained = True
        connection = sqlite3.connect(database_path)
        boundary = WorkspaceBoundary(
            prepared.path,
            settings.policy.sensitive_patterns,
        )
        process = ProcessTool(boundary)
        validators = ValidatorRunner(process, settings.validators)
        tools = ToolRegistry(
            cast(
                tuple[Tool, ...],
                (
                    ListFilesTool(
                        boundary,
                        ignored_directories=(".git", ".venv", ".safefix"),
                    ),
                    ReadFileTool(boundary),
                    SearchTextTool(
                        boundary,
                        ignored_directories=(".git", ".venv", ".safefix"),
                    ),
                    ApplyPatchTool(boundary),
                    process,
                    validators,
                ),
            )
        )
        runs = RunStore(connection)
        audit = AuditStore(
            connection,
            configured_secret_values=secret_values,
        )
        memory = MemoryStore(
            connection,
            configured_secret_values=secret_values,
        )
        approvals = ApprovalStateMachine(
            connection,
            configured_secret_values=secret_values,
        )
        policy = PolicyEngine(settings, boundary)
        feedback = FeedbackEngine(no_progress_limit=settings.budget.no_progress_rounds)
        context = ContextBuilder(memory, settings.memory)
        parser = ActionParser()
        http = httpx.AsyncClient(transport=http_transport)
        if provider == "mock":
            llm = ScriptedMockLLM(tuple(mock_actions or ()))
        else:
            llm = OpenAICompatibleClient(
                http,
                credential_service,
                endpoint=str(settings.llm.endpoint),
                model=settings.llm.model,
                provider=provider,
            )
        loop = AgentLoop(
            llm=llm,
            context=context,
            action_parser=parser,
            policy=policy,
            approvals=approvals,
            tools=tools,
            feedback=feedback,
            run_store=runs,
            audit=audit,
            settings=settings,
        )
        loop_taken = False
        expected_workspace = prepared.path.resolve(strict=False)

        def loop_factory(project_path: str, requested_provider: str) -> AgentLoop:
            nonlocal loop_taken
            if Path(project_path).resolve(strict=False) != expected_workspace:
                raise RuntimeConfigurationError(
                    "runtime workspace does not match prepared workspace"
                )
            if requested_provider != provider:
                raise RuntimeConfigurationError(
                    "runtime provider does not match configured provider"
                )
            if loop_taken:
                raise RuntimeConfigurationError("runtime loop is already in use")
            loop_taken = True
            return cast(AgentLoop, loop)

        service = TaskService(
            loop_factory,
            runs,
            audit_store=audit,
            approval_store=approvals,
            memory_store=memory,
            credential_service=credential_service,
        )
        return RuntimeSession(
            service=service,
            audit=audit,
            database_path=database_path,
            model_name=settings.llm.model,
            provider=provider,
            _connection=connection,
            _http=http,
        )
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            failure_kind = _BuildFailure.KEYBOARD_INTERRUPT
        elif isinstance(exc, SystemExit):
            failure_kind = _BuildFailure.SYSTEM_EXIT
            if type(exc.code) is int or exc.code is None:
                system_exit_code = exc.code
            else:
                system_exit_code = 1
        else:
            failure = exc

    try:
        if http is not None:
            try:
                _close_failed_http(http)
            except BaseException:
                try:
                    _fallback_failed_http_close(http)
                except BaseException:
                    pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except BaseException:
                pass
    if secret_obtained:
        _scrub_secret_holders(audit, memory, approvals)
        secret_values = ()
        failure = None
        llm = None
        loop = None
        service = None
        audit = None
        memory = None
        approvals = None
        del credential_service
        return _BuildFailureResult(
            failure_kind or _BuildFailure.CONFIGURATION,
            system_exit_code,
        )
    if failure_kind is not None:
        return _BuildFailureResult(failure_kind, system_exit_code)
    assert failure is not None
    raise failure


def _raise_runtime_initialization_failed() -> NoReturn:
    raise RuntimeConfigurationError("runtime initialization failed") from None


def _raise_clean_signal(failure: _BuildFailureResult) -> NoReturn:
    if failure.kind is _BuildFailure.KEYBOARD_INTERRUPT:
        raise KeyboardInterrupt() from None
    if failure.kind is _BuildFailure.SYSTEM_EXIT:
        raise SystemExit(failure.system_exit_code) from None
    raise AssertionError("build failure is not a signal")


def create_runtime(
    settings: SafeFixSettings,
    prepared: PreparedWorkspace,
    data_dir: Path,
    *,
    provider: str,
    credential_service: CredentialService,
    mock_actions: Sequence[str] | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> RuntimeSession:
    if provider not in {"mock", "openai-compatible"}:
        raise RuntimeConfigurationError("unsupported provider")
    if provider == "mock" and mock_actions is None:
        raise RuntimeConfigurationError("mock actions are required")

    database_path = data_dir / "safefix.sqlite3"
    workspace_path = prepared.path.resolve(strict=False)
    if (
        data_dir.resolve(strict=False).is_relative_to(workspace_path)
        or database_path.resolve(strict=False).is_relative_to(workspace_path)
    ):
        raise RuntimeConfigurationError(
            "runtime data and database paths must be outside the workspace"
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    result = _build_runtime(
        settings,
        prepared,
        database_path,
        provider=provider,
        credential_service=credential_service,
        mock_actions=mock_actions,
        http_transport=http_transport,
    )
    del credential_service
    if (
        isinstance(result, _BuildFailureResult)
        and result.kind is _BuildFailure.CONFIGURATION
    ):
        _raise_runtime_initialization_failed()
    if isinstance(result, _BuildFailureResult):
        _raise_clean_signal(result)
    return result
