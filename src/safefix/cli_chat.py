from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import getpass
from pathlib import Path
import subprocess
import sys
from typing import Any, TextIO

from safefix.cli_runner import CliRunOptions, RunSummary, run_cli
from safefix.cli_setup import SetupOptions, ensure_setup
from safefix.config import ConfigError
from safefix.credentials import CredentialError


@dataclass(frozen=True, slots=True)
class ChatOptions:
    project: Path
    config: Path | None
    data_dir: Path | None
    provider: str


def _run_git_diff(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        shell=False,
    )


def _write_help(stdout: TextIO) -> None:
    stdout.write(
        "命令: /help 帮助，/status 最近结果，/diff 查看改动，"
        "/new 清除会话状态，/exit 退出。\n"
    )


def _write_status(summary: RunSummary | None, stdout: TextIO) -> None:
    if summary is None:
        stdout.write("还没有执行任务。\n")
        return
    stdout.write(f"最近任务: {summary.status}（退出码 {summary.exit_code}）\n")
    if summary.workspace is not None:
        stdout.write(f"结果工作区: {summary.workspace}\n")


def _write_diff(
    summary: RunSummary | None,
    *,
    stdout: TextIO,
    diff_runner: Callable[[tuple[str, ...]], Any],
) -> None:
    if summary is None or summary.workspace is None:
        stdout.write("还没有可查看的结果工作区。\n")
        return
    command = (
        "git",
        "-C",
        summary.workspace,
        "diff",
        "--no-ext-diff",
        "--",
    )
    try:
        result = diff_runner(command)
    except (OSError, subprocess.SubprocessError):
        stdout.write("无法读取 Git 改动。\n")
        return
    if result.returncode != 0:
        stdout.write("结果工作区不是可读取的 Git 仓库。\n")
    elif result.stdout:
        stdout.write(result.stdout)
        if not result.stdout.endswith("\n"):
            stdout.write("\n")
    else:
        stdout.write("没有未提交的 Git 改动。\n")


def run_chat(
    options: ChatOptions,
    *,
    credential_service: Any,
    input_fn: Callable[[str], str] = input,
    secret_input_fn: Callable[[str], str] = getpass.getpass,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    task_runner: Callable[..., int] = run_cli,
    diff_runner: Callable[[tuple[str, ...]], Any] = _run_git_diff,
) -> int:
    try:
        config = ensure_setup(
            SetupOptions(
                project=options.project,
                config=options.config,
                provider=options.provider,
            ),
            credential_service=credential_service,
            input_fn=input_fn,
            secret_input_fn=secret_input_fn,
            stdout=stdout,
        )
    except (KeyboardInterrupt, EOFError):
        stdout.write("配置已取消。\n")
        return 6
    except (ConfigError, CredentialError, OSError) as exc:
        stdout.write(f"配置失败：{exc}\n")
        return 2

    stdout.write("\nSafeFix 对话模式。输入任务，或输入 /help 查看命令。\n")
    latest: RunSummary | None = None
    while True:
        try:
            message = input_fn("SafeFix > ").strip()
        except (EOFError, StopIteration):
            stdout.write("\n再见。\n")
            return 0
        except KeyboardInterrupt:
            stdout.write("\n再见。\n")
            return 0
        if not message:
            continue
        command = message.casefold()
        if command in {"/exit", "/quit"}:
            stdout.write("再见。\n")
            return 0
        if command == "/help":
            _write_help(stdout)
            continue
        if command == "/status":
            _write_status(latest, stdout)
            continue
        if command == "/new":
            latest = None
            stdout.write("已清除当前会话状态；工作区和审计记录仍保留。\n")
            continue
        if command == "/diff":
            _write_diff(latest, stdout=stdout, diff_runner=diff_runner)
            continue
        if message.startswith("/"):
            stdout.write("未知命令。\n")
            _write_help(stdout)
            continue

        def remember(summary: RunSummary) -> None:
            nonlocal latest
            latest = summary

        try:
            task_runner(
                CliRunOptions(
                    project=options.project,
                    task=message,
                    config=config,
                    data_dir=options.data_dir,
                    provider=options.provider,
                    in_place=False,
                    mock_script=None,
                    non_interactive=False,
                    json_output=False,
                ),
                credential_service=credential_service,
                input_fn=input_fn,
                stdout=stdout,
                stderr=stderr,
                summary_observer=remember,
            )
        except KeyboardInterrupt:
            stdout.write("\n当前任务已取消，可以继续输入。\n")
        except Exception:
            stdout.write("当前任务异常结束，可以继续输入。\n")
