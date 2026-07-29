from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from safefix.cli import build_parser, main
from safefix.config import load_settings
from safefix.task_service import TaskService


class FakeCredentials:
    def __init__(self) -> None:
        self.value: str | None = None
        self.cleared = False

    def set(self, provider: str, value: str) -> None:
        self.value = value

    def status(self, provider: str) -> object:
        return SimpleNamespace(
            provider=provider,
            configured=self.value is not None,
            source="keyring" if self.value else None,
            warning=None,
        )

    def clear(self, provider: str) -> None:
        self.cleared = True


def test_run_parser_exposes_safe_defaults() -> None:
    args = build_parser().parse_args(["run", "C:/project", "--task", "fix tests"])

    assert args.project == Path("C:/project")
    assert args.config == Path("safefix.yaml")
    assert args.data_dir is None
    assert args.provider == "openai-compatible"
    assert args.in_place is False
    assert args.mock_script is None
    assert args.non_interactive is False
    assert args.json is False


def test_setup_parser_exposes_project_config_and_provider() -> None:
    args = build_parser().parse_args(
        [
            "setup",
            "C:/project",
            "--config",
            "C:/config.yaml",
            "--provider",
            "custom-provider",
        ]
    )

    assert args.command == "setup"
    assert args.project == Path("C:/project")
    assert args.config == Path("C:/config.yaml")
    assert args.provider == "custom-provider"


def test_no_arguments_selects_chat_in_current_directory() -> None:
    args = build_parser().parse_args([])

    assert args.command == "chat"
    assert args.project == Path(".")
    assert args.config is None
    assert args.data_dir is None
    assert args.provider == "openai-compatible"


def test_explicit_chat_parser_accepts_runtime_locations() -> None:
    args = build_parser().parse_args(
        [
            "chat",
            "C:/project",
            "--config",
            "C:/config.yaml",
            "--data-dir",
            "C:/data",
            "--provider",
            "custom-provider",
        ]
    )

    assert args.command == "chat"
    assert args.project == Path("C:/project")
    assert args.config == Path("C:/config.yaml")
    assert args.data_dir == Path("C:/data")
    assert args.provider == "custom-provider"


def test_config_init_writes_valid_template_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "safefix.yaml"

    assert main(["config", "init", str(path)]) == 0
    assert load_settings(path).validators[0].id == "pytest"
    original = path.read_text(encoding="utf-8")

    assert main(["config", "init", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == original


def test_run_delegates_to_production_runner_by_default(monkeypatch) -> None:
    captured: list[object] = []

    def fake_run_cli(options: object) -> int:
        captured.append(options)
        return 5

    monkeypatch.setattr("safefix.cli_runner.run_cli", fake_run_cli)

    result = main(
        [
            "run",
            "C:/project",
            "--task",
            "fix tests",
            "--config",
            "custom.yaml",
            "--data-dir",
            "C:/data",
            "--provider",
            "mock",
            "--mock-script",
            "actions.jsonl",
            "--in-place",
            "--non-interactive",
            "--json",
        ]
    )

    assert result == 5
    assert len(captured) == 1
    options = captured[0]
    assert options.project == Path("C:/project")
    assert options.task == "fix tests"
    assert options.config == Path("custom.yaml")
    assert options.data_dir == Path("C:/data")
    assert options.provider == "mock"
    assert options.mock_script == Path("actions.jsonl")
    assert options.in_place is True
    assert options.non_interactive is True
    assert options.json_output is True


def test_credentials_set_uses_hidden_input_and_never_prints_secret(
    monkeypatch, capsys
) -> None:
    credentials = FakeCredentials()
    secret = "sk-do-not-print"
    monkeypatch.setattr("safefix.cli.getpass.getpass", lambda prompt: secret)

    result = main(
        ["credentials", "set", "--provider", "openai-compatible"],
        credential_service=credentials,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert credentials.value == secret
    assert secret not in captured.out + captured.err


def test_credentials_status_and_clear_are_safe(capsys) -> None:
    credentials = FakeCredentials()
    credentials.value = "secret"

    assert main(["credentials", "status"], credential_service=credentials) == 0
    assert (
        main(["credentials", "clear", "--yes"], credential_service=credentials)
        == 0
    )

    output = capsys.readouterr().out
    assert "configured: yes" in output
    assert "source: keyring" in output
    assert "secret" not in output
    assert credentials.cleared is True


def test_serve_defaults_to_loopback() -> None:
    calls: list[tuple[str, int, bool]] = []

    result = main(
        ["serve"],
        serve=lambda host, port, public: calls.append((host, port, public)),
    )

    assert result == 0
    assert calls == [("127.0.0.1", 8000, False)]


@pytest.mark.asyncio
async def test_task_service_creates_and_controls_run() -> None:
    snapshot = SimpleNamespace(
        run_id="run-1", pending_approval_id=None, project_id="project"
    )

    class Loop:
        async def start(self, task):
            snapshot.task = task
            return snapshot

        async def cancel(self, run_id):
            snapshot.cancelled = run_id
            return snapshot

    class Runs:
        def get(self, run_id):
            return snapshot if run_id == "run-1" else None

    loop = Loop()
    service = TaskService(
        lambda project_path, provider: loop,
        Runs(),
    )

    created = await service.create(
        task="fix value", project_path="C:/project", provider="mock"
    )
    cancelled = await service.cancel("run-1")

    assert created is snapshot
    assert snapshot.task.description == "fix value"
    assert snapshot.task.workspace_root == "C:/project"
    assert cancelled is snapshot
    assert snapshot.cancelled == "run-1"
