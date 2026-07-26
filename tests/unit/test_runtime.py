from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import cast

import httpx
import pytest

from safefix.config import (
    BudgetSettings,
    LLMSettings,
    MemorySettings,
    PolicySettings,
    SafeFixSettings,
    ValidatorSettings,
)
from safefix.credentials import CredentialError, CredentialService
from safefix.domain import RunStatus
from safefix.execution_workspace import PreparedWorkspace, prepare_workspace
from safefix.governance.approvals import ApprovalStateMachine
from safefix.governance.audit import AuditStore, AuditUnavailable
from safefix.memory import MemoryStore
from safefix.runtime import RuntimeConfigurationError, create_runtime


_TRACEBACK_TEST_SECRET = "sk-traceback-never-leaks"


class FakeCredentials:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.requests: list[str] = []

    def get_for_request(self, provider: str) -> str:
        self.requests.append(provider)
        if self.value is None:
            raise CredentialError("credential is not configured")
        return self.value


def _credentials(value: str | None = None) -> CredentialService:
    return cast(CredentialService, FakeCredentials(value))


def _settings() -> SafeFixSettings:
    return SafeFixSettings(
        llm=LLMSettings(
            endpoint="https://provider.test/v1",
            model="test-model",
        ),
        validators=(
            ValidatorSettings(
                id="python-check",
                kind="test",
                program=sys.executable,
                args=("-c", "raise SystemExit(0)"),
                timeout_seconds=30,
                success_exit_codes=frozenset({0}),
                output_limit_bytes=1024,
            ),
        ),
        policy=PolicySettings(
            sensitive_patterns=(".env",),
            allowed_programs=(sys.executable,),
            denied_programs=(),
        ),
        budget=BudgetSettings(
            repair_rounds=2,
            no_progress_rounds=2,
            total_steps=10,
            wall_time_seconds=60,
        ),
        memory=MemorySettings(retrieval_limit=3, character_budget=256),
    )


def _fixture_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        'def greeting():\n    return "hello"\n',
        encoding="utf-8",
    )
    (project / ".env").write_text("API_KEY=fixture-secret\n", encoding="utf-8")
    return project


def _prepared(tmp_path: Path) -> tuple[PreparedWorkspace, Path]:
    data_dir = tmp_path / "data"
    prepared = prepare_workspace(
        _fixture_project(tmp_path),
        data_dir,
        in_place=False,
        sensitive_patterns=(".env",),
    )
    return prepared, data_dir


def _assert_exception_graph_is_secret_free(
    error: BaseException,
    secret: str,
) -> None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        assert secret not in repr(current)
        traceback = current.__traceback__
        while traceback is not None:
            for name, value in traceback.tb_frame.f_locals.items():
                assert secret not in repr(value), name
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)


@pytest.mark.asyncio
async def test_runtime_uses_real_agent_loop_and_persists_audit(
    tmp_path: Path,
) -> None:
    prepared, data_dir = _prepared(tmp_path)
    actions = (
        '{"type":"list_files","id":"1","reason":"inspect","path":"."}',
        '{"type":"finish","id":"2","reason":"done","summary":"inspection complete"}',
    )
    runtime = create_runtime(
        _settings(),
        prepared,
        data_dir,
        provider="mock",
        credential_service=_credentials(),
        mock_actions=actions,
    )

    snapshot = await runtime.service.create(
        task="inspect the project",
        project_path=str(prepared.path),
        project_id=str(prepared.source),
        provider="mock",
    )

    assert snapshot.status is RunStatus.SUCCESS
    assert runtime.database_path.is_file()
    assert runtime.model_name == "test-model"
    assert runtime.provider == "mock"
    events = runtime.list_events(snapshot.run_id)
    assert [event.event_type for event in events]
    assert runtime.list_events(snapshot.run_id, after_sequence=2) == events[2:]
    await runtime.aclose()


def test_mock_provider_requires_scripted_actions(tmp_path: Path) -> None:
    prepared, data_dir = _prepared(tmp_path)

    with pytest.raises(
        RuntimeConfigurationError, match="mock actions are required"
    ):
        create_runtime(
            _settings(),
            prepared,
            data_dir,
            provider="mock",
            credential_service=_credentials(),
        )


def test_unsupported_provider_fails_before_model_call(tmp_path: Path) -> None:
    prepared, data_dir = _prepared(tmp_path)
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: (
            requests.append(request)
            or httpx.Response(500, request=request)
        )
    )

    with pytest.raises(RuntimeConfigurationError, match="unsupported provider"):
        create_runtime(
            _settings(),
            prepared,
            data_dir,
            provider="unknown",
            credential_service=_credentials("secret"),
            http_transport=transport,
        )

    assert requests == []


def test_openai_compatible_requires_startup_credential(tmp_path: Path) -> None:
    prepared, data_dir = _prepared(tmp_path)

    with pytest.raises(CredentialError, match="credential is not configured"):
        create_runtime(
            _settings(),
            prepared,
            data_dir,
            provider="openai-compatible",
            credential_service=_credentials(),
            http_transport=httpx.MockTransport(
                lambda request: httpx.Response(500, request=request)
            ),
        )


@pytest.mark.asyncio
async def test_openai_compatible_runs_inspect_and_finish_through_http_client(
    tmp_path: Path,
) -> None:
    prepared, data_dir = _prepared(tmp_path)
    actions = iter(
        (
            '{"type":"list_files","id":"1","reason":"inspect","path":"."}',
            '{"type":"finish","id":"2","reason":"done","summary":"complete"}',
        )
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": next(actions)}}]},
        )

    runtime = create_runtime(
        _settings(),
        prepared,
        data_dir,
        provider="openai-compatible",
        credential_service=_credentials("sk-test-key"),
        http_transport=httpx.MockTransport(handler),
    )

    snapshot = await runtime.service.create(
        task="inspect the project",
        project_path=str(prepared.path),
        project_id=str(prepared.source),
        provider="openai-compatible",
    )

    assert snapshot.status is RunStatus.SUCCESS
    assert len(requests) == 2
    assert all(
        request.headers["Authorization"] == "Bearer sk-test-key"
        for request in requests
    )
    assert all(
        json.loads(request.content)["model"] == "test-model"
        for request in requests
    )
    await runtime.aclose()


@pytest.mark.asyncio
async def test_runtime_registers_filesystem_process_and_validation_tools(
    tmp_path: Path,
) -> None:
    prepared, data_dir = _prepared(tmp_path)
    program = json.dumps(sys.executable)
    args = json.dumps(["-c", "raise SystemExit(0)"])
    actions = (
        '{"type":"list_files","id":"list","reason":"inspect","path":"."}',
        '{"type":"read_file","id":"read","reason":"inspect","path":"app.py"}',
        '{"type":"search_text","id":"search","reason":"inspect",'
        '"pattern":"hello","path":"."}',
        f'{{"type":"run_process","id":"process","reason":"check",'
        f'"program":{program},"args":{args}}}',
        '{"type":"run_validation","id":"validation","reason":"validate",'
        '"validator_id":"python-check"}',
    )
    runtime = create_runtime(
        _settings(),
        prepared,
        data_dir,
        provider="mock",
        credential_service=_credentials(),
        mock_actions=actions,
    )

    snapshot = await runtime.service.create(
        task="exercise registered tools",
        project_path=str(prepared.path),
        provider="mock",
    )

    assert snapshot.status is RunStatus.SUCCESS
    tool_action_ids = {
        event.redacted_payload["action_id"]
        for event in runtime.list_events(snapshot.run_id)
        if event.event_type == "TOOL_RESULT"
    }
    assert {"list", "read", "search", "process", "validation"} <= tool_action_ids
    await runtime.aclose()


@pytest.mark.asyncio
async def test_runtime_registers_patch_tool(tmp_path: Path) -> None:
    prepared, data_dir = _prepared(tmp_path)
    target = prepared.path / "app.py"
    expected_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    action = json.dumps(
        {
            "type": "apply_patch",
            "id": "patch",
            "reason": "repair",
            "path": "app.py",
            "expected_sha256": expected_hash,
            "old_text": '"hello"',
            "new_text": '"hi"',
            "expected_replacements": 1,
        }
    )
    runtime = create_runtime(
        _settings(),
        prepared,
        data_dir,
        provider="mock",
        credential_service=_credentials(),
        mock_actions=(action,),
    )

    snapshot = await runtime.service.create(
        task="change the greeting",
        project_path=str(prepared.path),
        provider="mock",
    )

    assert snapshot.status is RunStatus.SUCCESS
    assert '"hi"' in target.read_text(encoding="utf-8")
    assert snapshot.changed_files == ("app.py",)
    await runtime.aclose()


@pytest.mark.asyncio
async def test_runtime_closes_store_and_http_resources(tmp_path: Path) -> None:
    prepared, data_dir = _prepared(tmp_path)
    runtime = create_runtime(
        _settings(),
        prepared,
        data_dir,
        provider="mock",
        credential_service=_credentials(),
        mock_actions=(
            '{"type":"finish","id":"1","reason":"done","summary":"complete"}',
        ),
    )
    snapshot = await runtime.service.create(
        task="validate",
        project_path=str(prepared.path),
        provider="mock",
    )

    await runtime.aclose()

    with pytest.raises(AuditUnavailable, match="Audit storage is unavailable"):
        runtime.list_events(snapshot.run_id)


@pytest.mark.asyncio
async def test_constructor_failure_closes_every_opened_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, data_dir = _prepared(tmp_path)

    class RecordingHttpClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    http = RecordingHttpClient()
    connections: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def recording_connect(path: Path) -> sqlite3.Connection:
        connection = real_connect(path)
        connections.append(connection)
        return connection

    def fail_loop_construction(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("loop construction failed")

    monkeypatch.setattr("safefix.runtime.sqlite3.connect", recording_connect)
    monkeypatch.setattr(
        "safefix.runtime.httpx.AsyncClient",
        lambda *, transport: http,
    )
    monkeypatch.setattr("safefix.runtime.AgentLoop", fail_loop_construction)

    with pytest.raises(RuntimeError, match="loop construction failed"):
        create_runtime(
            _settings(),
            prepared,
            data_dir,
            provider="mock",
            credential_service=_credentials(),
            mock_actions=(
                '{"type":"finish","id":"1","reason":"done","summary":"complete"}',
            ),
        )

    assert http.closed is True
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")


@pytest.mark.asyncio
async def test_http_cleanup_failure_still_closes_sqlite_and_uses_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, data_dir = _prepared(tmp_path)

    class RecordingHttpClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    http = RecordingHttpClient()
    connections: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def recording_connect(path: Path) -> sqlite3.Connection:
        connection = real_connect(path)
        connections.append(connection)
        return connection

    def fail_loop_construction(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("loop construction failed")

    def fail_http_cleanup(http_client: object) -> None:
        del http_client
        raise RuntimeError("cleanup scheduling failed")

    monkeypatch.setattr("safefix.runtime.sqlite3.connect", recording_connect)
    monkeypatch.setattr(
        "safefix.runtime.httpx.AsyncClient",
        lambda *, transport: http,
    )
    monkeypatch.setattr("safefix.runtime.AgentLoop", fail_loop_construction)
    monkeypatch.setattr(
        "safefix.runtime._close_failed_http",
        fail_http_cleanup,
    )

    with pytest.raises(RuntimeError, match="loop construction failed"):
        create_runtime(
            _settings(),
            prepared,
            data_dir,
            provider="mock",
            credential_service=_credentials(),
            mock_actions=(
                '{"type":"finish","id":"1","reason":"done","summary":"complete"}',
            ),
        )

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")
    await asyncio.sleep(0)
    assert http.closed is True


@pytest.mark.asyncio
async def test_secret_bearing_constructor_failure_is_scrubbed_and_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, data_dir = _prepared(tmp_path)
    audits: list[AuditStore] = []
    memories: list[MemoryStore] = []
    approvals: list[ApprovalStateMachine] = []

    class RecordingAuditStore(AuditStore):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            audits.append(self)

    class RecordingMemoryStore(MemoryStore):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            memories.append(self)

    class RecordingApprovalStateMachine(ApprovalStateMachine):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            approvals.append(self)

    def fail_loop_construction(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError(
            f"provider driver exposed {_TRACEBACK_TEST_SECRET}"
        )

    monkeypatch.setattr("safefix.runtime.AuditStore", RecordingAuditStore)
    monkeypatch.setattr("safefix.runtime.MemoryStore", RecordingMemoryStore)
    monkeypatch.setattr(
        "safefix.runtime.ApprovalStateMachine",
        RecordingApprovalStateMachine,
    )
    monkeypatch.setattr("safefix.runtime.AgentLoop", fail_loop_construction)

    with pytest.raises(BaseException) as captured:
        create_runtime(
            _settings(),
            prepared,
            data_dir,
            provider="openai-compatible",
            credential_service=_credentials(_TRACEBACK_TEST_SECRET),
            http_transport=httpx.MockTransport(
                lambda request: httpx.Response(500, request=request)
            ),
        )

    error = captured.value
    assert type(error) is RuntimeConfigurationError
    assert str(error) == "runtime initialization failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    _assert_exception_graph_is_secret_free(error, _TRACEBACK_TEST_SECRET)
    assert len(audits) == len(memories) == len(approvals) == 1
    assert audits[0]._secrets == ()
    assert memories[0]._secrets == ()
    assert approvals[0]._secrets == ()
    assert approvals[0]._audit._secrets == ()


@pytest.mark.asyncio
async def test_runtime_rejects_wrong_workspace_and_second_run(
    tmp_path: Path,
) -> None:
    prepared, data_dir = _prepared(tmp_path)
    runtime = create_runtime(
        _settings(),
        prepared,
        data_dir,
        provider="mock",
        credential_service=_credentials(),
        mock_actions=(
            '{"type":"finish","id":"1","reason":"done","summary":"complete"}',
        ),
    )

    with pytest.raises(RuntimeConfigurationError, match="workspace"):
        await runtime.service.create(
            task="validate",
            project_path=str(prepared.source),
            provider="mock",
        )

    snapshot = await runtime.service.create(
        task="validate",
        project_path=str(prepared.path),
        provider="mock",
    )
    assert snapshot.status is RunStatus.SUCCESS
    with pytest.raises(RuntimeConfigurationError, match="already"):
        await runtime.service.create(
            task="validate again",
            project_path=str(prepared.path),
            provider="mock",
        )
    await runtime.aclose()


@pytest.mark.asyncio
async def test_configured_secret_is_redacted_from_audit_and_memory(
    tmp_path: Path,
) -> None:
    prepared, data_dir = _prepared(tmp_path)
    secret = "sk-never-persist-this"
    actions = iter(
        (
            json.dumps(
                {
                    "type": "list_files",
                    "id": "1",
                    "reason": f"inspect with {secret}",
                    "path": ".",
                }
            ),
            json.dumps(
                {
                    "type": "finish",
                    "id": "2",
                    "reason": f"finish with {secret}",
                    "summary": f"model summary {secret}",
                }
            ),
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": next(actions)}}]},
        )

    runtime = create_runtime(
        _settings(),
        prepared,
        data_dir,
        provider="openai-compatible",
        credential_service=_credentials(secret),
        http_transport=httpx.MockTransport(handler),
    )
    snapshot = await runtime.service.create(
        task="inspect safely",
        project_path=str(prepared.path),
        project_id=str(prepared.source),
        provider="openai-compatible",
    )

    events = runtime.list_events(snapshot.run_id)
    memories = runtime.service.list_memory(str(prepared.source))

    assert snapshot.status is RunStatus.SUCCESS
    assert secret not in repr(events)
    assert secret not in repr(memories)
    assert "[REDACTED]" in repr(events)
    await runtime.aclose()
