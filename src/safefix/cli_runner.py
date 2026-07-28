from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, TextIO

from safefix.config import ConfigError, load_settings
from safefix.credentials import CredentialError, CredentialService
from safefix.domain import ApprovalRequest, RunSnapshot, RunStatus
from safefix.execution_workspace import (
    PreparedWorkspace,
    WorkspacePreparationError,
    default_data_dir,
    prepare_workspace,
    record_run_id,
)
from safefix.governance.approvals import ApprovalError
from safefix.governance.audit import AuditEvent, AuditUnavailable
from safefix.llm.openai_compatible import ProviderError
from safefix.runtime import (
    RuntimeConfigurationError,
    RuntimeSession,
    create_runtime,
)
from safefix.task_service import TaskServiceError


_DEFAULT_INPUT = input
_DEFAULT_STDOUT = sys.stdout
_DEFAULT_STDERR = sys.stderr
_MAX_RENDERED_EVENT_CHARS = 2_048
_MAX_RENDERED_VALUE_CHARS = 512
_SENSITIVE_KEY_PARTS = (
    "token",
    "key",
    "secret",
    "password",
    "authorization",
    "capability",
    "csrf",
)
_EVENT_LABELS = {
    "ACTION": "模型动作",
    "POLICY_DECISION": "策略判断",
    "TOOL_RESULT": "工具结果",
    "FEEDBACK": "验证反馈",
    "APPROVAL_REQUESTED": "已请求审批",
    "APPROVAL_APPROVED": "审批已通过",
    "APPROVAL_REJECTED": "审批已拒绝",
    "APPROVAL_EXPIRED": "审批已过期",
    "APPROVAL_CANCELLED": "审批已取消",
}
_UNAVAILABLE_REASONS = (
    "audit unavailable",
    "governance unavailable",
    "approval service unavailable",
    "storage unavailable",
    "persistence unavailable",
    "memory storage is unavailable",
)


@dataclass(frozen=True, slots=True)
class CliRunOptions:
    project: Path
    task: str
    config: Path
    data_dir: Path | None
    provider: str
    in_place: bool
    mock_script: Path | None
    non_interactive: bool
    json_output: bool


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str | None
    status: str
    exit_code: int
    workspace: str | None
    mode: str
    changed_files: tuple[str, ...]
    stop_reason: str | None
    audit_database: str | None

    @classmethod
    def from_snapshot(
        cls,
        snapshot: RunSnapshot,
        prepared: PreparedWorkspace,
        database_path: Path,
    ) -> RunSummary:
        return cls(
            run_id=snapshot.run_id,
            status=snapshot.status.value,
            exit_code=_snapshot_exit_code(snapshot),
            workspace=str(prepared.path),
            mode=prepared.mode,
            changed_files=snapshot.changed_files,
            stop_reason=snapshot.stop_reason,
            audit_database=str(database_path),
        )


def _credential_service() -> CredentialService:
    import keyring

    return CredentialService(keyring)


def load_mock_actions(script_path: Path, workspace: Path) -> tuple[str, ...]:
    del workspace
    try:
        return tuple(
            line.strip()
            for line in script_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, UnicodeError) as exc:
        raise ConfigError("cannot read mock action script") from exc


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _bounded_text(value: str) -> str:
    if len(value) <= _MAX_RENDERED_VALUE_CHARS:
        return value
    return value[:_MAX_RENDERED_VALUE_CHARS] + "…[已截断]"


def _safe_payload(value: Any, *, depth: int = 0) -> Any:
    if depth >= 6:
        return "[已截断]"
    if isinstance(value, Mapping):
        mapping_result: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:50]:
            safe_key = _bounded_text(str(key))
            mapping_result[safe_key] = (
                "[REDACTED]"
                if _is_sensitive_key(safe_key)
                else _safe_payload(item, depth=depth + 1)
            )
        if len(items) > 50:
            mapping_result["truncated"] = f"{len(items) - 50} fields omitted"
        return mapping_result
    if isinstance(value, (list, tuple)):
        sequence_result = [
            _safe_payload(item, depth=depth + 1) for item in value[:50]
        ]
        if len(value) > 50:
            sequence_result.append(f"[{len(value) - 50} items omitted]")
        return sequence_result
    if isinstance(value, str):
        return _bounded_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_text(str(value))


def _event_label(event: AuditEvent) -> str:
    if event.event_type == "FEEDBACK" and isinstance(
        event.redacted_payload, Mapping
    ):
        category = event.redacted_payload.get("category")
        if category == "VALIDATION_SUCCESS":
            return "验证通过"
        if category in {"TEST_FAILURE", "LINT_FAILURE", "TYPE_ERROR"}:
            return "验证失败"
    return _EVENT_LABELS.get(event.event_type, _bounded_text(event.event_type))


def _render_events(
    events: Sequence[AuditEvent],
    *,
    after_sequence: int,
    stdout: TextIO,
) -> int:
    last_sequence = after_sequence
    for event in sorted(events, key=lambda item: item.sequence):
        if event.sequence <= last_sequence:
            continue
        payload = json.dumps(
            _safe_payload(event.redacted_payload),
            ensure_ascii=False,
            sort_keys=True,
        )
        if len(payload) > _MAX_RENDERED_EVENT_CHARS:
            payload = (
                payload[: _MAX_RENDERED_EVENT_CHARS - 8] + "…[已截断]"
            )
        stdout.write(f"[{event.sequence}] {_event_label(event)}: {payload}\n")
        last_sequence = max(last_sequence, event.sequence)
    return last_sequence


def _approval_prompt(request: ApprovalRequest) -> str:
    action_type = "unknown"
    detail = "参数: 动作详情不可用"
    reason = json.dumps("未提供", ensure_ascii=False)
    try:
        raw = json.loads(request.frozen_action_json)
        if not isinstance(raw, dict):
            raise ValueError
        safe = _safe_payload(raw)
        if not isinstance(safe, dict):
            raise ValueError
        action_type = _bounded_text(str(safe.get("type", "unknown")))
        reason = json.dumps(
            _bounded_text(str(safe.get("reason", "未提供"))),
            ensure_ascii=False,
        )
        if action_type == "run_process":
            program = json.dumps(
                _bounded_text(str(safe.get("program", ""))),
                ensure_ascii=False,
            )
            args = safe.get("args", [])
            if not isinstance(args, list):
                args = []
            encoded_args = json.dumps(args, ensure_ascii=False)
            detail = (
                f"程序: {_bounded_text(program)}\n"
                f"参数: {_bounded_text(encoded_args)}"
            )
        else:
            visible = {
                key: value
                for key, value in safe.items()
                if key not in {"id", "reason", "type"}
            }
            detail = "参数: " + _bounded_text(
                json.dumps(visible, ensure_ascii=False, sort_keys=True)
            )
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return (
        "\n需要一次性审批\n"
        f"动作: {action_type}\n"
        f"{detail}\n"
        f"理由: {reason}\n"
        "风险等级: MEDIUM\n"
        "Approve this action once? [y/N] "
    )


def _snapshot_exit_code(snapshot: RunSnapshot) -> int:
    if snapshot.status is RunStatus.SUCCESS:
        return 0
    if snapshot.status is RunStatus.CANCELLED:
        return 6
    reason = (snapshot.stop_reason or "").casefold()
    if "approval rejected" in reason or "rejected by user" in reason:
        return 6
    if any(marker in reason for marker in _UNAVAILABLE_REASONS):
        return 7
    if snapshot.status in {
        RunStatus.BLOCKED,
        RunStatus.NO_PROGRESS,
        RunStatus.BUDGET_EXCEEDED,
        RunStatus.FAILED,
    }:
        return 5
    return 7


def _render_summary(
    summary: RunSummary,
    *,
    json_output: bool,
    stdout: TextIO,
) -> int:
    if json_output:
        stdout.write(
            json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True) + "\n"
        )
        return summary.exit_code

    stdout.write(f"\n运行结果: {summary.status}\n")
    if summary.run_id is not None:
        stdout.write(f"运行 ID: {summary.run_id}\n")
    stdout.write(f"模式: {summary.mode}\n")
    if summary.stop_reason:
        stdout.write(f"停止原因: {_bounded_text(summary.stop_reason)}\n")
    stdout.write(
        "修改文件: "
        + (", ".join(summary.changed_files) if summary.changed_files else "无")
        + "\n"
    )
    if summary.workspace is not None:
        stdout.write(f"结果工作区: {summary.workspace}\n")
    if summary.audit_database is not None:
        stdout.write(f"审计数据库: {summary.audit_database}\n")
    return summary.exit_code


def _error_summary(options: CliRunOptions, exit_code: int) -> RunSummary:
    return RunSummary(
        run_id=None,
        status="ERROR",
        exit_code=exit_code,
        workspace=None,
        mode="in_place" if options.in_place else "isolated",
        changed_files=(),
        stop_reason=None,
        audit_database=None,
    )


def _write_start_banner(
    prepared: PreparedWorkspace,
    *,
    provider: str,
    model_name: str,
    stdout: TextIO,
) -> None:
    if prepared.mode == "in_place":
        stdout.write("警告：原地模式将直接修改原项目。\n")
    else:
        stdout.write("运行模式：隔离副本，原项目不会被修改。\n")
    stdout.write(f"有效工作区: {prepared.path}\n")
    stdout.write(f"模型: {provider}/{model_name}\n")


def _known_error(error: BaseException, provider: str) -> tuple[int, str] | None:
    if isinstance(error, ConfigError):
        return 2, f"配置错误：{error}"
    if isinstance(error, CredentialError):
        return (
            3,
            "凭据错误：未能读取模型凭据。请运行 "
            f"`safefix credentials set --provider {provider}`。",
        )
    if isinstance(error, (ProviderError, RuntimeConfigurationError)):
        return 3, "模型供应商错误：请检查 provider、模型配置和 Mock 脚本。"
    if isinstance(error, WorkspacePreparationError):
        return 4, "工作区错误：无法安全准备项目，请检查项目路径和数据目录。"
    if isinstance(
        error,
        (
            ApprovalError,
            AuditUnavailable,
            TaskServiceError,
            OSError,
            sqlite3.Error,
        ),
    ):
        return 7, "运行时不可用：治理、审计或持久化服务未能安全运行。"
    if isinstance(error, (KeyboardInterrupt, EOFError)):
        return 6, "运行已取消。"
    return None


async def _run_cli_async(
    options: CliRunOptions,
    *,
    credential_service: Any | None,
    input_fn: Callable[[str], str],
    stdout: TextIO,
    stderr: TextIO,
    runtime_factory: Callable[..., RuntimeSession],
    workspace_factory: Callable[..., PreparedWorkspace],
) -> int:
    runtime: RuntimeSession | None = None
    summary: RunSummary | None = None
    exit_code = 7
    error_message: str | None = None
    approval_rejected = False
    try:
        settings = load_settings(options.config)
        data_dir = options.data_dir or default_data_dir()
        prepared = workspace_factory(
            options.project,
            data_dir,
            in_place=options.in_place,
            sensitive_patterns=settings.policy.sensitive_patterns,
        )
        mock_actions = (
            load_mock_actions(options.mock_script, prepared.path)
            if options.mock_script is not None
            else None
        )
        runtime = runtime_factory(
            settings,
            prepared,
            data_dir,
            provider=options.provider,
            credential_service=credential_service or _credential_service(),
            mock_actions=mock_actions,
        )
        if not options.json_output:
            _write_start_banner(
                prepared,
                provider=runtime.provider,
                model_name=runtime.model_name,
                stdout=stdout,
            )
        snapshot = await runtime.service.create(
            task=options.task,
            project_path=str(prepared.path),
            project_id=str(prepared.source),
            provider=options.provider,
        )
        record_run_id(prepared, snapshot.run_id)
        last_sequence = 0
        events = runtime.list_events(
            snapshot.run_id,
            after_sequence=last_sequence,
        )
        if options.json_output:
            last_sequence = max(
                (event.sequence for event in events),
                default=last_sequence,
            )
        else:
            last_sequence = _render_events(
                events,
                after_sequence=last_sequence,
                stdout=stdout,
            )
        while snapshot.status is RunStatus.AWAITING_APPROVAL:
            access = runtime.service.get_approval(snapshot.run_id)
            prompt = _approval_prompt(access.request)
            if options.non_interactive:
                if not options.json_output:
                    stdout.write(prompt)
                    stdout.write("\n非交互模式：已拒绝此动作。\n")
                approved = False
            else:
                if options.json_output:
                    stderr.write(prompt)
                    answer = input_fn("")
                elif input_fn is _DEFAULT_INPUT and stdout is sys.stdout:
                    answer = input_fn(prompt)
                else:
                    stdout.write(prompt)
                    answer = input_fn(prompt)
                approved = answer.strip().lower() == "y"
            if not approved:
                approval_rejected = True
            snapshot = (
                await runtime.service.approve(
                    snapshot.run_id,
                    access.capability,
                )
                if approved
                else await runtime.service.reject(
                    snapshot.run_id,
                    access.capability,
                )
            )
            events = runtime.list_events(
                snapshot.run_id,
                after_sequence=last_sequence,
            )
            if options.json_output:
                last_sequence = max(
                    (event.sequence for event in events),
                    default=last_sequence,
                )
            else:
                last_sequence = _render_events(
                    events,
                    after_sequence=last_sequence,
                    stdout=stdout,
                )
        summary = RunSummary.from_snapshot(
            snapshot,
            prepared,
            runtime.database_path,
        )
        if approval_rejected and summary.exit_code != 7:
            summary = replace(summary, exit_code=6)
        exit_code = summary.exit_code
    except BaseException as error:
        known = _known_error(error, options.provider)
        if known is None:
            raise
        exit_code, error_message = known
    finally:
        if runtime is not None:
            try:
                await runtime.aclose()
            except BaseException as error:
                known = _known_error(error, options.provider)
                if known is None:
                    raise
                exit_code, error_message = known
                summary = None

    if summary is not None:
        return _render_summary(
            summary,
            json_output=options.json_output,
            stdout=stdout,
        )
    if options.json_output:
        return _render_summary(
            _error_summary(options, exit_code),
            json_output=True,
            stdout=stdout,
        )
    assert error_message is not None
    stderr.write(error_message + "\n")
    return exit_code


def run_cli(
    options: CliRunOptions,
    *,
    credential_service: Any | None = None,
    input_fn: Callable[[str], str] = input,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    runtime_factory: Callable[..., RuntimeSession] = create_runtime,
    workspace_factory: Callable[..., PreparedWorkspace] = prepare_workspace,
) -> int:
    if stdout is _DEFAULT_STDOUT:
        stdout = sys.stdout
    if stderr is _DEFAULT_STDERR:
        stderr = sys.stderr
    try:
        return asyncio.run(
            _run_cli_async(
                options,
                credential_service=credential_service,
                input_fn=input_fn,
                stdout=stdout,
                stderr=stderr,
                runtime_factory=runtime_factory,
                workspace_factory=workspace_factory,
            )
        )
    except KeyboardInterrupt:
        if options.json_output:
            return _render_summary(
                _error_summary(options, 6),
                json_output=True,
                stdout=stdout,
            )
        stderr.write("运行已取消。\n")
        return 6
