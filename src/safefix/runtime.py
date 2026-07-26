from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import threading
from typing import Any, cast

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


def _close_failed_http(http: httpx.AsyncClient) -> None:
    def close() -> None:
        try:
            asyncio.run(http.aclose())
        except BaseException:
            pass

    worker = threading.Thread(target=close)
    worker.start()
    worker.join()


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

    secret_values: tuple[str, ...] = ()
    if provider == "openai-compatible":
        secret_values = (credential_service.get_for_request(provider),)

    data_dir.mkdir(parents=True, exist_ok=True)
    database_path = data_dir / "safefix.sqlite3"
    connection: sqlite3.Connection | None = None
    http: httpx.AsyncClient | None = None
    try:
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
            llm: Any = ScriptedMockLLM(tuple(mock_actions or ()))
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
            return loop

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
    except BaseException:
        if http is not None:
            _close_failed_http(http)
        if connection is not None:
            connection.close()
        raise
