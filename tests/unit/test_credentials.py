from __future__ import annotations

from pathlib import Path

import pytest

from safefix.credentials import CredentialError, CredentialService


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, provider: str, value: str) -> None:
        self.values[(service, provider)] = value

    def get_password(self, service: str, provider: str) -> str | None:
        return self.values.get((service, provider))

    def delete_password(self, service: str, provider: str) -> None:
        self.values.pop((service, provider), None)


def test_status_never_returns_plaintext_key() -> None:
    backend = FakeKeyring()
    service = CredentialService(backend, service_name="safefix")
    service.set("openai-compatible", "sk-SECRET")

    status = service.status("openai-compatible")

    assert status.configured is True
    assert status.source == "keyring"
    assert "sk-SECRET" not in repr(status)
    assert service.get_for_request("openai-compatible") == "sk-SECRET"


def test_clear_and_empty_value_handling() -> None:
    backend = FakeKeyring()
    service = CredentialService(backend)

    with pytest.raises(CredentialError, match="empty"):
        service.set("provider", "   ")
    service.set("provider", "value")
    service.clear("provider")

    assert service.status("provider").configured is False
    with pytest.raises(CredentialError, match="not configured"):
        service.get_for_request("provider")


def test_explicit_secret_file_strips_one_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "provider.key"
    path.write_text("file-secret\n", encoding="utf-8")
    service = CredentialService(None, secret_file=path)

    assert service.get_for_request("provider") == "file-secret"
    assert service.status("provider").source == "secret-file"


def test_opt_in_env_file_reports_warning_status(tmp_path: Path) -> None:
    path = tmp_path / "credentials.env"
    path.write_text("OPENAI_COMPATIBLE_API_KEY=env-secret\n", encoding="utf-8")
    service = CredentialService(None, env_file=path)

    status = service.status("openai-compatible")

    assert service.get_for_request("openai-compatible") == "env-secret"
    assert status.configured is True
    assert status.source == "env-file"
    assert status.warning is not None
    assert "env-secret" not in repr(status)

