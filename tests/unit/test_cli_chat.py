from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from safefix.cli_chat import ChatOptions, run_chat
from safefix.cli_runner import RunSummary
from safefix.config import default_settings_yaml


class FakeCredentials:
    def status(self, provider: str) -> object:
        return SimpleNamespace(
            provider=provider,
            configured=True,
            source="keyring",
            warning=None,
        )


def _input(*answers: str):
    iterator = iter(answers)
    return lambda prompt: next(iterator)


def _options(tmp_path: Path) -> ChatOptions:
    project = tmp_path / "project"
    project.mkdir()
    config = project / "safefix.yaml"
    config.write_text(default_settings_yaml(), encoding="utf-8")
    return ChatOptions(
        project=project,
        config=config,
        data_dir=tmp_path / "data",
        provider="mock",
    )


def _summary(workspace: Path, *, status: str = "SUCCESS") -> RunSummary:
    return RunSummary(
        run_id="run-1",
        status=status,
        exit_code=0 if status == "SUCCESS" else 5,
        workspace=str(workspace),
        mode="isolated",
        changed_files=("calculator.py",),
        stop_reason="validation succeeded",
        audit_database=str(workspace.parent / "safefix.sqlite3"),
    )


def test_chat_runs_two_natural_language_tasks_with_existing_runner(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path)
    calls: list[object] = []

    def task_runner(run_options, *, summary_observer, **kwargs):
        calls.append(run_options)
        summary_observer(_summary(tmp_path / f"workspace-{len(calls)}"))
        return 0

    result = run_chat(
        options,
        credential_service=FakeCredentials(),
        input_fn=_input("修复测试", "检查类型错误", "/exit"),
        secret_input_fn=lambda prompt: "",
        stdout=StringIO(),
        stderr=StringIO(),
        task_runner=task_runner,
    )

    assert result == 0
    assert [call.task for call in calls] == ["修复测试", "检查类型错误"]
    assert all(call.in_place is False for call in calls)
    assert all(call.config == options.config for call in calls)


def test_chat_help_status_new_and_exit(tmp_path: Path) -> None:
    options = _options(tmp_path)
    stdout = StringIO()

    def task_runner(run_options, *, summary_observer, **kwargs):
        summary_observer(_summary(tmp_path / "workspace"))
        return 0

    result = run_chat(
        options,
        credential_service=FakeCredentials(),
        input_fn=_input("修复测试", "/help", "/status", "/new", "/status", "/exit"),
        secret_input_fn=lambda prompt: "",
        stdout=stdout,
        stderr=StringIO(),
        task_runner=task_runner,
    )

    output = stdout.getvalue()
    assert result == 0
    assert "/diff" in output
    assert "最近任务: SUCCESS" in output
    assert "已清除当前会话状态" in output
    assert "还没有执行任务" in output


def test_chat_diff_uses_fixed_read_only_git_arguments(tmp_path: Path) -> None:
    options = _options(tmp_path)
    workspace = tmp_path / "result workspace"
    commands: list[tuple[str, ...]] = []
    stdout = StringIO()

    def task_runner(run_options, *, summary_observer, **kwargs):
        summary_observer(_summary(workspace))
        return 0

    def diff_runner(command: tuple[str, ...]):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="diff output\n", stderr="")

    run_chat(
        options,
        credential_service=FakeCredentials(),
        input_fn=_input("修复测试", "/diff", "/exit"),
        secret_input_fn=lambda prompt: "",
        stdout=stdout,
        stderr=StringIO(),
        task_runner=task_runner,
        diff_runner=diff_runner,
    )

    assert commands == [
        (
            "git",
            "-C",
            str(workspace),
            "diff",
            "--no-ext-diff",
            "--",
        )
    ]
    assert "diff output" in stdout.getvalue()


def test_chat_recovers_after_one_task_error(tmp_path: Path) -> None:
    options = _options(tmp_path)
    tasks: list[str] = []

    def task_runner(run_options, *, summary_observer, **kwargs):
        tasks.append(run_options.task)
        status = "ERROR" if len(tasks) == 1 else "SUCCESS"
        summary_observer(_summary(tmp_path / "workspace", status=status))
        return 5 if status == "ERROR" else 0

    result = run_chat(
        options,
        credential_service=FakeCredentials(),
        input_fn=_input("第一次", "第二次", "/exit"),
        secret_input_fn=lambda prompt: "",
        stdout=StringIO(),
        stderr=StringIO(),
        task_runner=task_runner,
    )

    assert result == 0
    assert tasks == ["第一次", "第二次"]


def test_chat_cancellation_during_setup_returns_without_traceback(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    stdout = StringIO()

    def cancel(prompt: str) -> str:
        raise KeyboardInterrupt

    result = run_chat(
        ChatOptions(
            project=project,
            config=None,
            data_dir=None,
            provider="openai-compatible",
        ),
        credential_service=FakeCredentials(),
        input_fn=cancel,
        secret_input_fn=cancel,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert result == 6
    assert "已取消" in stdout.getvalue()
    assert "Traceback" not in stdout.getvalue()
