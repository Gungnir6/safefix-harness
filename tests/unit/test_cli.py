from __future__ import annotations

from types import SimpleNamespace

import pytest

from safefix.cli import main
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
