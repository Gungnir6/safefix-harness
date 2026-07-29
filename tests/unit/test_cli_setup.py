from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from safefix.cli_setup import SetupOptions, ensure_setup, run_setup
from safefix.config import default_settings_yaml, load_settings


class FakeCredentials:
    def __init__(self, *, configured: bool = False) -> None:
        self.configured = configured
        self.stored: list[tuple[str, str]] = []

    def status(self, provider: str) -> object:
        return SimpleNamespace(
            provider=provider,
            configured=self.configured,
            source="keyring" if self.configured else None,
            warning=None,
        )

    def set(self, provider: str, value: str) -> None:
        self.stored.append((provider, value))
        self.configured = True


def _answers(*values: str):
    iterator = iter(values)
    return lambda prompt: next(iterator)


def test_setup_creates_loadable_config_with_selected_endpoint_and_model(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    credentials = FakeCredentials(configured=True)

    path = ensure_setup(
        SetupOptions(project=project, config=None, provider="openai-compatible"),
        credential_service=credentials,
        input_fn=_answers("https://example.test/v1", "test-model"),
        secret_input_fn=lambda prompt: "must-not-be-read",
        stdout=StringIO(),
    )

    settings = load_settings(path)
    assert path == project / "safefix.yaml"
    assert str(settings.llm.endpoint) == "https://example.test/v1"
    assert settings.llm.model == "test-model"


def test_setup_preserves_existing_config_and_skips_config_questions(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "safefix.yaml"
    original = default_settings_yaml()
    path.write_text(original, encoding="utf-8")

    def unexpected_input(prompt: str) -> str:
        raise AssertionError(f"unexpected configuration prompt: {prompt}")

    result = ensure_setup(
        SetupOptions(project=project, config=None, provider="mock"),
        credential_service=FakeCredentials(),
        input_fn=unexpected_input,
        secret_input_fn=unexpected_input,
        stdout=StringIO(),
    )

    assert result == path
    assert path.read_text(encoding="utf-8") == original


def test_setup_stores_missing_credential_without_printing_it(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "safefix.yaml").write_text(
        default_settings_yaml(), encoding="utf-8"
    )
    credentials = FakeCredentials()
    stdout = StringIO()
    secret = "sk-never-print-this"

    ensure_setup(
        SetupOptions(project=project, config=None, provider="openai-compatible"),
        credential_service=credentials,
        input_fn=lambda prompt: "",
        secret_input_fn=lambda prompt: secret,
        stdout=stdout,
    )

    assert credentials.stored == [("openai-compatible", secret)]
    assert secret not in stdout.getvalue()


def test_setup_returns_actionable_error_without_traceback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "safefix.yaml"
    path.write_text("not: valid: yaml", encoding="utf-8")
    stdout = StringIO()

    result = run_setup(
        SetupOptions(project=project, config=None, provider="mock"),
        credential_service=FakeCredentials(),
        input_fn=lambda prompt: "",
        secret_input_fn=lambda prompt: "",
        stdout=stdout,
    )

    assert result == 2
    assert "配置失败" in stdout.getvalue()
    assert "Traceback" not in stdout.getvalue()


def test_setup_cancellation_returns_without_traceback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    stdout = StringIO()

    def cancel(prompt: str) -> str:
        raise KeyboardInterrupt

    result = run_setup(
        SetupOptions(project=project, config=None, provider="openai-compatible"),
        credential_service=FakeCredentials(),
        input_fn=cancel,
        secret_input_fn=cancel,
        stdout=stdout,
    )

    assert result == 6
    assert "已取消" in stdout.getvalue()
    assert "Traceback" not in stdout.getvalue()
