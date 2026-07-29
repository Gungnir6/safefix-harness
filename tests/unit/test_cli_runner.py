from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO
import json
from pathlib import Path
from typing import Any

import pytest

from safefix.cli_runner import (
    CliRunOptions,
    _approval_prompt,
    _render_events,
    run_cli,
)
from safefix.config import ConfigError, default_settings_yaml
from safefix.credentials import CredentialError
from safefix.domain import (
    ApprovalRequest,
    ApprovalStatus,
    BudgetState,
    RunSnapshot,
    RunStatus,
)
from safefix.execution_workspace import (
    PreparedWorkspace,
    WorkspacePreparationError,
)
from safefix.governance.audit import AuditEvent, AuditUnavailable
from safefix.task_service import ApprovalAccess


class FakeCredentials:
    pass


def _snapshot(
    status: RunStatus,
    *,
    stop_reason: str | None = None,
    changed_files: tuple[str, ...] = (),
) -> RunSnapshot:
    now = datetime.now(UTC)
    return RunSnapshot(
        run_id="run-1",
        task_id="task-1",
        project_id="C:/source",
        workspace_root="C:/workspace",
        description="fix tests",
        status=status,
        repair_round=1,
        step_count=2,
        budget=BudgetState(
            max_steps=10,
            remaining_steps=8,
            max_repair_rounds=3,
            remaining_repairs=2,
        ),
        version=2,
        pending_approval_id=(
            "approval-1" if status is RunStatus.AWAITING_APPROVAL else None
        ),
        changed_files=changed_files,
        stop_reason=stop_reason,
        created_at=now,
        updated_at=now,
    )


def _approval() -> ApprovalAccess:
    now = datetime.now(UTC)
    action = {
        "type": "run_process",
        "id": "process-1",
        "reason": "save the verified repair",
        "program": "git",
        "args": ["commit", "-m", "fix tests"],
    }
    request = ApprovalRequest(
        id="approval-1",
        run_id="run-1",
        action_hash="a" * 64,
        status=ApprovalStatus.PENDING,
        one_time_token_hash="b" * 64,
        frozen_action_json=json.dumps(action, separators=(",", ":")),
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        rule_ids=("CMD_GIT_WRITE",),
    )
    return ApprovalAccess(request, "one-time-capability", "csrf-secret")


class FakeService:
    def __init__(
        self,
        created: RunSnapshot | BaseException,
        *,
        approved: RunSnapshot | None = None,
        rejected: RunSnapshot | None = None,
        approval_access: ApprovalAccess | None = None,
    ) -> None:
        self._created = created
        self._approved = approved
        self._rejected = rejected
        self._approval_access = approval_access or _approval()
        self.create_calls: list[dict[str, Any]] = []
        self.approved: list[tuple[str, str]] = []
        self.rejected: list[tuple[str, str]] = []

    async def create(self, **kwargs: Any) -> RunSnapshot:
        self.create_calls.append(kwargs)
        if isinstance(self._created, BaseException):
            raise self._created
        return self._created

    def get_approval(self, run_id: str) -> ApprovalAccess:
        assert run_id == "run-1"
        return self._approval_access

    async def approve(self, run_id: str, capability: str) -> RunSnapshot:
        self.approved.append((run_id, capability))
        assert self._approved is not None
        return self._approved

    async def reject(self, run_id: str, capability: str) -> RunSnapshot:
        self.rejected.append((run_id, capability))
        assert self._rejected is not None
        return self._rejected


class FakeRuntime:
    def __init__(
        self,
        tmp_path: Path,
        service: FakeService,
        *,
        event_batches: list[list[AuditEvent]] | None = None,
    ) -> None:
        self.service = service
        self.database_path = tmp_path / "safefix.sqlite3"
        self.model_name = "test-model"
        self.provider = "mock"
        self._event_batches = event_batches or [[]]
        self.list_calls: list[tuple[str, int]] = []
        self.closed = 0

    def list_events(
        self, run_id: str, *, after_sequence: int = 0
    ) -> list[AuditEvent]:
        self.list_calls.append((run_id, after_sequence))
        index = min(len(self.list_calls) - 1, len(self._event_batches) - 1)
        return self._event_batches[index]

    async def aclose(self) -> None:
        self.closed += 1


def _event(sequence: int, event_type: str, payload: object) -> AuditEvent:
    return AuditEvent(
        run_id="run-1",
        sequence=sequence,
        event_type=event_type,
        redacted_payload=payload,
        previous_hash="",
        event_hash="",
        created_at=datetime.now(UTC),
    )


def _options(tmp_path: Path, **overrides: Any) -> CliRunOptions:
    project = tmp_path / "source"
    project.mkdir(exist_ok=True)
    config = tmp_path / "safefix.yaml"
    config.write_text(default_settings_yaml(), encoding="utf-8")
    values: dict[str, Any] = {
        "project": project,
        "task": "fix tests",
        "config": config,
        "data_dir": tmp_path / "data",
        "provider": "mock",
        "in_place": False,
        "mock_script": None,
        "non_interactive": False,
        "json_output": False,
    }
    values.update(overrides)
    return CliRunOptions(**values)


def _prepared(tmp_path: Path, *, mode: str = "isolated") -> PreparedWorkspace:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    if mode == "in_place":
        return PreparedWorkspace("exec-1", source, source, "in_place", None)
    execution = tmp_path / "data" / "runs" / "exec-1"
    workspace = execution / "workspace"
    workspace.mkdir(parents=True)
    metadata = execution / "execution.json"
    metadata.write_text(
        json.dumps(
            {
                "execution_id": "exec-1",
                "source": str(source),
                "mode": "isolated",
                "created_at": datetime.now(UTC).isoformat(),
                "run_id": None,
            }
        ),
        encoding="utf-8",
    )
    return PreparedWorkspace("exec-1", source, workspace, "isolated", metadata)


def _workspace_factory(prepared: PreparedWorkspace):
    def factory(*args: Any, **kwargs: Any) -> PreparedWorkspace:
        return prepared

    return factory


def _unexpected_runtime(*args: Any, **kwargs: Any) -> FakeRuntime:
    pytest.fail("runtime must not be created")


def test_cli_approves_only_the_presented_action_once(tmp_path: Path) -> None:
    service = FakeService(
        _snapshot(RunStatus.AWAITING_APPROVAL),
        approved=_snapshot(
            RunStatus.SUCCESS,
            stop_reason="validation succeeded",
            changed_files=("calculator.py",),
        ),
    )
    events = [
        _event(1, "ACTION", {"type": "run_process", "program": "git"}),
        _event(2, "POLICY_DECISION", {"outcome": "REQUIRE_APPROVAL"}),
    ]
    runtime = FakeRuntime(
        tmp_path,
        service,
        event_batches=[events, [*events, _event(3, "TOOL_RESULT", {"success": True})]],
    )
    prepared = _prepared(tmp_path)
    stdout = StringIO()

    result = run_cli(
        _options(tmp_path),
        credential_service=FakeCredentials(),
        input_fn=lambda prompt: "y",
        stdout=stdout,
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(prepared),
    )

    assert result == 0
    assert service.approved == [("run-1", "one-time-capability")]
    assert service.rejected == []
    assert runtime.list_calls == [("run-1", 0), ("run-1", 2)]
    assert runtime.closed == 1
    output = stdout.getvalue()
    assert "需要一次性审批" in output
    assert '程序: "git"' in output
    assert '参数: ["commit", "-m", "fix tests"]' in output
    assert "one-time-capability" not in output
    assert "csrf-secret" not in output
    assert output.count('"success": true') == 1
    assert json.loads(prepared.metadata_path.read_text(encoding="utf-8"))[
        "run_id"
    ] == "run-1"


def test_non_interactive_run_rejects_approval_without_prompt(
    tmp_path: Path,
) -> None:
    service = FakeService(
        _snapshot(RunStatus.AWAITING_APPROVAL),
        rejected=_snapshot(RunStatus.CANCELLED, stop_reason="approval rejected"),
    )
    runtime = FakeRuntime(tmp_path, service)
    prompted = False

    def input_fn(prompt: str) -> str:
        nonlocal prompted
        prompted = True
        return "y"

    result = run_cli(
        _options(tmp_path, non_interactive=True),
        credential_service=FakeCredentials(),
        input_fn=input_fn,
        stdout=StringIO(),
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path)),
    )

    assert result == 6
    assert service.rejected == [("run-1", "one-time-capability")]
    assert service.approved == []
    assert prompted is False
    assert runtime.closed == 1


@pytest.mark.parametrize(
    ("terminal_status", "stop_reason"),
    [
        (RunStatus.SUCCESS, "validation succeeded"),
        (RunStatus.BLOCKED, "policy denied action"),
    ],
)
def test_rejected_approval_forces_exit_six_after_runtime_reaches_terminal_status(
    tmp_path: Path,
    terminal_status: RunStatus,
    stop_reason: str,
) -> None:
    service = FakeService(
        _snapshot(RunStatus.AWAITING_APPROVAL),
        rejected=_snapshot(terminal_status, stop_reason=stop_reason),
    )
    runtime = FakeRuntime(tmp_path, service)

    result = run_cli(
        _options(tmp_path),
        credential_service=FakeCredentials(),
        input_fn=lambda prompt: "n",
        stdout=StringIO(),
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path)),
    )

    assert result == 6
    assert service.rejected == [("run-1", "one-time-capability")]


@pytest.mark.parametrize(
    ("status", "stop_reason", "expected"),
    [
        (RunStatus.SUCCESS, "validation succeeded", 0),
        (RunStatus.BLOCKED, "policy denied action", 5),
        (RunStatus.NO_PROGRESS, "no progress", 5),
        (RunStatus.BUDGET_EXCEEDED, "step budget exhausted", 5),
        (RunStatus.FAILED, "model call failed", 5),
        (RunStatus.CANCELLED, "cancelled", 6),
        (RunStatus.BLOCKED, "approval rejected", 6),
        (RunStatus.FAILED, "audit unavailable", 7),
        (RunStatus.FAILED, "governance unavailable", 7),
    ],
)
def test_terminal_status_maps_to_stable_exit_code(
    tmp_path: Path,
    status: RunStatus,
    stop_reason: str,
    expected: int,
) -> None:
    runtime = FakeRuntime(
        tmp_path,
        FakeService(_snapshot(status, stop_reason=stop_reason)),
    )

    result = run_cli(
        _options(tmp_path),
        credential_service=FakeCredentials(),
        stdout=StringIO(),
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path)),
    )

    assert result == expected
    assert runtime.closed == 1


@pytest.mark.parametrize(
    ("error", "expected", "expected_message"),
    [
        (ConfigError("cannot read configuration"), 2, "配置错误"),
        (CredentialError("credential is not configured"), 3, "credentials set"),
        (WorkspacePreparationError("project directory does not exist"), 4, "工作区错误"),
        (AuditUnavailable("Audit storage is unavailable"), 7, "运行时不可用"),
        (OSError("database path is not writable"), 7, "运行时不可用"),
    ],
)
def test_known_startup_errors_are_safe_and_have_stable_exit_codes(
    tmp_path: Path,
    error: Exception,
    expected: int,
    expected_message: str,
) -> None:
    stderr = StringIO()
    options = _options(tmp_path)
    if isinstance(error, ConfigError):
        options = _options(tmp_path, config=tmp_path / "missing.yaml")
        runtime_factory = _unexpected_runtime
        workspace_factory = _workspace_factory(_prepared(tmp_path))
    elif isinstance(error, WorkspacePreparationError):
        runtime_factory = _unexpected_runtime

        def workspace_factory(*args: Any, **kwargs: Any) -> PreparedWorkspace:
            raise error

    else:

        def runtime_factory(*args: Any, **kwargs: Any) -> FakeRuntime:
            raise error

        workspace_factory = _workspace_factory(_prepared(tmp_path))

    result = run_cli(
        options,
        credential_service=FakeCredentials(),
        stdout=StringIO(),
        stderr=stderr,
        runtime_factory=runtime_factory,
        workspace_factory=workspace_factory,
    )

    assert result == expected
    assert expected_message in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()
    assert "credential is not configured" not in stderr.getvalue()


@pytest.mark.parametrize(
    ("mode", "expected_message"),
    [
        ("isolated", "原项目不会被修改"),
        ("in_place", "警告：原地模式将直接修改原项目"),
    ],
)
def test_cli_prints_an_explicit_workspace_mode_banner(
    tmp_path: Path,
    mode: str,
    expected_message: str,
) -> None:
    stdout = StringIO()
    runtime = FakeRuntime(tmp_path, FakeService(_snapshot(RunStatus.SUCCESS)))

    result = run_cli(
        _options(tmp_path, in_place=mode == "in_place"),
        credential_service=FakeCredentials(),
        stdout=stdout,
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path, mode=mode)),
    )

    assert result == 0
    assert expected_message in stdout.getvalue()


def test_event_rendering_redacts_sensitive_fields_and_bounds_output() -> None:
    stdout = StringIO()
    events = [
        _event(
            1,
            "TOOL_RESULT",
            {
                "token": "one-time-capability",
                "nested": {"api_key": "sk-secret"},
                "stdout_summary": "x" * 20_000,
            },
        )
    ]

    last_sequence = _render_events(events, after_sequence=0, stdout=stdout)

    output = stdout.getvalue()
    assert last_sequence == 1
    assert "one-time-capability" not in output
    assert "sk-secret" not in output
    assert "[REDACTED]" in output
    assert "已截断" in output
    assert len(output) < 5_000


def test_event_rendering_escapes_all_dynamic_terminal_controls() -> None:
    stdout = StringIO()
    event = _event(
        1,
        "未知\x1b\u202e事件",
        {
            "键\u2066\x1b": "中文\u009b\u202e值",
            "normal": "可读中文",
        },
    )

    _render_events([event], after_sequence=0, stdout=stdout)

    output = stdout.getvalue()
    assert "未知\\u001b\\u202e事件" in output
    assert "中文" in output
    assert "可读中文" in output
    assert all(control not in output for control in ("\x1b", "\u009b", "\u202e", "\u2066"))
    payload = output.partition(": ")[2].removesuffix("\n")
    json.loads(payload)


def test_event_rendering_skips_already_presented_sequences() -> None:
    stdout = StringIO()

    last_sequence = _render_events(
        [
            _event(1, "ACTION", {"id": "old-action"}),
            _event(2, "FEEDBACK", {"summary": "new feedback"}),
        ],
        after_sequence=1,
        stdout=stdout,
    )

    assert last_sequence == 2
    assert "old-action" not in stdout.getvalue()
    assert "new feedback" in stdout.getvalue()


def test_event_rendering_orders_and_deduplicates_sequences_within_one_batch() -> None:
    stdout = StringIO()

    last_sequence = _render_events(
        [
            _event(3, "FEEDBACK", {"summary": "third"}),
            _event(2, "ACTION", {"reason": "second"}),
            _event(2, "ACTION", {"reason": "duplicate"}),
            _event(1, "ACTION", {"reason": "old"}),
        ],
        after_sequence=1,
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert last_sequence == 3
    assert output.count("[2]") == 1
    assert "second" in output
    assert "duplicate" not in output
    assert "old" not in output
    assert output.index("[2]") < output.index("[3]")


def test_json_output_is_one_machine_readable_summary_without_banner(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    runtime = FakeRuntime(
        tmp_path,
        FakeService(
            _snapshot(
                RunStatus.SUCCESS,
                stop_reason="validation succeeded",
                changed_files=("calculator.py",),
            )
        ),
    )

    result = run_cli(
        _options(tmp_path, json_output=True),
        credential_service=FakeCredentials(),
        stdout=stdout,
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path)),
    )

    assert result == 0
    summary = json.loads(stdout.getvalue())
    assert summary == {
        "audit_database": str(tmp_path / "safefix.sqlite3"),
        "changed_files": ["calculator.py"],
        "exit_code": 0,
        "mode": "isolated",
        "run_id": "run-1",
        "status": "SUCCESS",
        "stop_reason": "validation succeeded",
        "workspace": str(tmp_path / "data" / "runs" / "exec-1" / "workspace"),
    }
    assert "原项目不会被修改" not in stdout.getvalue()
    assert "模型动作" not in stdout.getvalue()


def test_human_output_escapes_dynamic_summary_and_banner_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "项目\u202e\x1b"
    prepared = PreparedWorkspace("exec-1", source, source, "in_place", None)
    snapshot = _snapshot(
        RunStatus.SUCCESS,
        stop_reason="停止中文\x1b\u009b\u202e\u2066尾",
        changed_files=("文件\x1b.py", "第二\u202e.txt"),
    )
    runtime = FakeRuntime(tmp_path, FakeService(snapshot))
    runtime.provider = "供应商\u2066"
    runtime.model_name = "模型\u009b"
    runtime.database_path = tmp_path / "数据\u202e\x1b.sqlite3"
    stdout = StringIO()

    result = run_cli(
        _options(tmp_path, in_place=True),
        credential_service=FakeCredentials(),
        stdout=stdout,
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(prepared),
    )

    output = stdout.getvalue()
    assert result == 0
    assert "停止中文" in output
    assert "文件" in output
    assert "项目" in output
    assert "供应商" in output
    assert "模型" in output
    assert all(control not in output for control in ("\x1b", "\u009b", "\u202e", "\u2066"))
    assert "\\u001b" in output
    assert "\\u009b" in output
    assert "\\u202e" in output
    assert "\\u2066" in output


def test_json_output_preserves_raw_dynamic_summary_values(tmp_path: Path) -> None:
    source = tmp_path / "项目\u202e\x1b"
    prepared = PreparedWorkspace("exec-1", source, source, "in_place", None)
    stop_reason = "停止\x1b\u009b\u202e\u2066"
    changed_files = ("文件\x1b.py", "第二\u202e.txt")
    runtime = FakeRuntime(
        tmp_path,
        FakeService(
            _snapshot(
                RunStatus.SUCCESS,
                stop_reason=stop_reason,
                changed_files=changed_files,
            )
        ),
    )
    runtime.database_path = tmp_path / "数据\u2066.sqlite3"
    stdout = StringIO()

    result = run_cli(
        _options(tmp_path, in_place=True, json_output=True),
        credential_service=FakeCredentials(),
        stdout=stdout,
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(prepared),
    )

    summary = json.loads(stdout.getvalue())
    assert result == 0
    assert summary["stop_reason"] == stop_reason
    assert summary["changed_files"] == list(changed_files)
    assert summary["workspace"] == str(source)
    assert summary["audit_database"] == str(runtime.database_path)


def test_json_interactive_approval_keeps_stdout_machine_readable(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    prompts: list[str] = []
    service = FakeService(
        _snapshot(RunStatus.AWAITING_APPROVAL),
        approved=_snapshot(RunStatus.SUCCESS, stop_reason="validation succeeded"),
    )
    runtime = FakeRuntime(tmp_path, service)

    def input_fn(prompt: str) -> str:
        prompts.append(prompt)
        stdout.write(prompt)
        return "y"

    result = run_cli(
        _options(tmp_path, json_output=True),
        credential_service=FakeCredentials(),
        input_fn=input_fn,
        stdout=stdout,
        stderr=stderr,
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path)),
    )

    assert result == 0
    assert json.loads(stdout.getvalue())["status"] == "SUCCESS"
    assert prompts == [""]
    assert "需要一次性审批" in stderr.getvalue()
    assert "one-time-capability" not in stdout.getvalue() + stderr.getvalue()


def test_overlong_approval_execution_is_rejected_without_reading_input(
    tmp_path: Path,
) -> None:
    access = _approval()
    dangerous_tail = "--upload=" + ("x" * 10_000) + "\nthen-delete"
    action = {
        "type": "run_process",
        "id": "process-1",
        "reason": "run verified command",
        "program": "git",
        "args": ["commit", dangerous_tail],
    }
    request = access.request.model_copy(
        update={
            "frozen_action_json": json.dumps(
                action,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )
    approval_access = ApprovalAccess(
        request,
        access.capability,
        access.csrf_token,
    )
    service = FakeService(
        _snapshot(RunStatus.AWAITING_APPROVAL),
        rejected=_snapshot(RunStatus.SUCCESS, stop_reason="validation succeeded"),
        approval_access=approval_access,
    )
    runtime = FakeRuntime(tmp_path, service)
    stdout = StringIO()
    input_called = False

    def input_fn(prompt: str) -> str:
        nonlocal input_called
        input_called = True
        return "y"

    result = run_cli(
        _options(tmp_path),
        credential_service=FakeCredentials(),
        input_fn=input_fn,
        stdout=stdout,
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path)),
    )

    assert result == 6
    assert input_called is False
    assert service.approved == []
    assert service.rejected == [("run-1", "one-time-capability")]
    assert "无法完整安全展示" in stdout.getvalue()
    assert "已截断" not in stdout.getvalue()
    assert "then-delete" not in stdout.getvalue()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConfigError("invalid after runtime creation"), 2),
        (KeyboardInterrupt(), 6),
    ],
)
def test_runtime_closes_when_run_creation_aborts(
    tmp_path: Path,
    error: BaseException,
    expected: int,
) -> None:
    runtime = FakeRuntime(tmp_path, FakeService(error))

    result = run_cli(
        _options(tmp_path),
        credential_service=FakeCredentials(),
        stdout=StringIO(),
        stderr=StringIO(),
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=_workspace_factory(_prepared(tmp_path)),
    )

    assert result == expected
    assert runtime.closed == 1


def test_approval_prompt_describes_frozen_action_without_sensitive_ids() -> None:
    access = _approval()

    prompt = _approval_prompt(access.request)

    assert "需要一次性审批" in prompt
    assert '程序: "git"' in prompt
    assert '参数: ["commit", "-m", "fix tests"]' in prompt
    assert '理由: "save the verified repair"' in prompt
    assert "规则: CMD_GIT_WRITE" in prompt
    assert access.capability not in prompt
    assert access.csrf_token not in prompt
    assert access.request.action_hash not in prompt
    assert access.request.one_time_token_hash not in prompt


def test_approval_prompt_preserves_argument_boundaries_and_escapes_controls() -> None:
    access = _approval()
    action = {
        "type": "run_process",
        "id": "process-1",
        "reason": "review\n\x1b[31munsafe",
        "program": "C:\\Program Files\\Git\\bin\\git.exe",
        "args": ["commit", "-m", "line one\nline two", "\x1b[31mred"],
    }
    request = access.request.model_copy(
        update={
            "frozen_action_json": json.dumps(
                action,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )

    prompt = _approval_prompt(request)

    assert '程序: "C:\\\\Program Files\\\\Git\\\\bin\\\\git.exe"' in prompt
    assert (
        '参数: ["commit", "-m", "line one\\nline two", "\\u001b[31mred"]'
        in prompt
    )
    assert '理由: "review\\n\\u001b[31munsafe"' in prompt
    assert "line one\nline two" not in prompt
    assert "\x1b" not in prompt
    assert access.capability not in prompt


def test_approval_prompt_escapes_terminal_controls_but_keeps_chinese_readable() -> None:
    access = _approval()
    action = {
        "type": "run_process",
        "id": "process-1",
        "reason": "批准中文\u009b警告\u202e尾",
        "program": "工具\u009b危险\u202eexe",
        "args": ["提交中文", "参数\u009b尾", "\u202e反转"],
    }
    request = access.request.model_copy(
        update={
            "frozen_action_json": json.dumps(
                action,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )

    prompt = _approval_prompt(request)

    assert "\u009b" not in prompt
    assert "\u202e" not in prompt
    assert '程序: "工具\\u009b危险\\u202eexe"' in prompt
    assert (
        '参数: ["提交中文", "参数\\u009b尾", "\\u202e反转"]'
        in prompt
    )
    assert '理由: "批准中文\\u009b警告\\u202e尾"' in prompt
    assert access.capability not in prompt


def test_approval_prompt_safely_bounds_rule_ids_and_labels_empty_rules() -> None:
    access = _approval()
    unsafe = "规则中文\x1b\u009b\u202e\u2066" + ("长" * 1_000)

    prompt = _approval_prompt(
        access.request.model_copy(update={"rule_ids": (unsafe,)})
    )
    empty_prompt = _approval_prompt(
        access.request.model_copy(update={"rule_ids": ()})
    )

    assert "规则中文" in prompt
    assert "已截断" in prompt
    assert all(control not in prompt for control in ("\x1b", "\u009b", "\u202e", "\u2066"))
    assert "规则: 无/未知" in empty_prompt
