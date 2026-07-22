from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class CredentialError(RuntimeError):
    """A credential could not be stored or loaded safely."""


class KeyringBackend(Protocol):
    def set_password(self, service: str, provider: str, value: str) -> None: ...

    def get_password(self, service: str, provider: str) -> str | None: ...

    def delete_password(self, service: str, provider: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    provider: str
    configured: bool
    source: str | None = None
    warning: str | None = None


class CredentialService:
    def __init__(
        self,
        backend: KeyringBackend | None,
        *,
        service_name: str = "safefix",
        secret_file: Path | None = None,
        env_file: Path | None = None,
    ) -> None:
        self._backend = backend
        self._service_name = service_name
        self._secret_file = secret_file
        self._env_file = env_file

    def set(self, provider: str, value: str) -> None:
        if not value.strip():
            raise CredentialError("credential cannot be empty")
        if self._backend is None:
            raise CredentialError("secure credential storage is unavailable")
        try:
            self._backend.set_password(self._service_name, provider, value)
        except Exception as exc:
            raise CredentialError("failed to store credential") from exc

    def clear(self, provider: str) -> None:
        if self._backend is None:
            return
        try:
            self._backend.delete_password(self._service_name, provider)
        except Exception as exc:
            raise CredentialError("failed to clear credential") from exc

    def status(self, provider: str) -> CredentialStatus:
        found = self._find(provider)
        if found is None:
            return CredentialStatus(provider=provider, configured=False)
        _, source = found
        warning = (
            "plaintext env-file credential is enabled"
            if source == "env-file"
            else None
        )
        return CredentialStatus(provider, True, source, warning)

    def get_for_request(self, provider: str) -> str:
        found = self._find(provider)
        if found is None:
            raise CredentialError("credential is not configured")
        return found[0]

    def _find(self, provider: str) -> tuple[str, str] | None:
        if self._secret_file is not None:
            return self._read_secret_file(), "secret-file"
        if self._env_file is not None:
            value = self._read_env_file(provider)
            if value is not None:
                return value, "env-file"
        if self._backend is not None:
            try:
                value = self._backend.get_password(self._service_name, provider)
            except Exception as exc:
                raise CredentialError("failed to load credential") from exc
            if value:
                return value, "keyring"
        return None

    def _read_secret_file(self) -> str:
        assert self._secret_file is not None
        try:
            if not self._secret_file.is_file():
                raise OSError
            value = self._secret_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise CredentialError("failed to read credential file") from exc
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        if not value:
            raise CredentialError("credential file is empty")
        return value

    def _read_env_file(self, provider: str) -> str | None:
        assert self._env_file is not None
        name = re.sub(r"[^A-Z0-9]", "_", provider.upper()) + "_API_KEY"
        try:
            lines = self._env_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CredentialError("failed to read env credential file") from exc
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value or None
        return None
