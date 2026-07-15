from collections.abc import Callable
from pathlib import Path
import traceback
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from pydantic import ValidationError

from safefix.config import (
    BudgetSettings,
    ConfigError,
    MemorySettings,
    PolicySettings,
    SafeFixSettings,
    ValidatorSettings,
    load_settings,
)


def test_config_rejects_unknown_and_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "safefix.yaml"
    path.write_text(
        "llm:\n  model: test\n  api_key: secret\nunknown: true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as error:
        load_settings(path)
    assert "api_key" in str(error.value)
    assert "unknown" in str(error.value)


def valid_config() -> dict[str, Any]:
    return {
        "llm": {"endpoint": "https://api.example.test/v1", "model": "test-model"},
        "validators": [
            {
                "id": "pytest",
                "kind": "test",
                "program": "python",
                "args": ["-m", "pytest"],
                "timeout_seconds": 120,
                "success_exit_codes": [0],
                "output_limit_bytes": 65_536,
            }
        ],
    }


def write_config(tmp_path: Path, raw: object) -> Path:
    path = tmp_path / "safefix.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize("timeout_seconds", (0, 3601))
def test_config_rejects_timeout_outside_allowed_range(
    tmp_path: Path, timeout_seconds: int
) -> None:
    raw = valid_config()
    raw["validators"][0]["timeout_seconds"] = timeout_seconds  # type: ignore[index]
    with pytest.raises(ConfigError, match=r"validators\.0\.timeout_seconds"):
        load_settings(write_config(tmp_path, raw))


def test_config_rejects_empty_success_exit_codes(tmp_path: Path) -> None:
    raw = valid_config()
    raw["validators"][0]["success_exit_codes"] = []  # type: ignore[index]
    with pytest.raises(ConfigError, match=r"validators\.0\.success_exit_codes"):
        load_settings(write_config(tmp_path, raw))


def test_validator_args_accept_arbitrary_strings() -> None:
    raw = valid_config()["validators"][0]  # type: ignore[index]
    raw["args"] = ("--expression", "")  # type: ignore[index]

    settings = ValidatorSettings.model_validate(raw)

    assert settings.args == ("--expression", "")


@pytest.mark.parametrize("output_limit_bytes", (1023, 10_485_761))
def test_config_rejects_output_limit_outside_allowed_range(
    tmp_path: Path, output_limit_bytes: int
) -> None:
    raw = valid_config()
    raw["validators"][0]["output_limit_bytes"] = output_limit_bytes  # type: ignore[index]
    with pytest.raises(ConfigError, match=r"validators\.0\.output_limit_bytes"):
        load_settings(write_config(tmp_path, raw))


def test_config_rejects_validator_ids_that_collide_after_casefolding(
    tmp_path: Path,
) -> None:
    raw = valid_config()
    duplicate = dict(raw["validators"][0])  # type: ignore[index]
    duplicate["id"] = " PYTEST "
    raw["validators"].append(duplicate)  # type: ignore[union-attr]
    with pytest.raises(
        ConfigError, match="validators must not contain duplicate entries"
    ):
        load_settings(write_config(tmp_path, raw))


def test_config_requires_at_least_one_validator(tmp_path: Path) -> None:
    raw = valid_config()
    raw["validators"] = []

    with pytest.raises(ConfigError, match="validators"):
        load_settings(write_config(tmp_path, raw))


def test_config_rejects_conflicting_allowed_and_denied_programs(tmp_path: Path) -> None:
    raw = valid_config()
    raw["policy"] = {"allowed_programs": ["Python"], "denied_programs": [" python "]}
    with pytest.raises(ConfigError, match="allowed_programs and denied_programs"):
        load_settings(write_config(tmp_path, raw))


@pytest.mark.parametrize(
    ("raw", "location"),
    (
        (
            lambda: {
                **valid_config(),
                "llm": {"endpoint": "https://api.example.test/v1"},
            },
            "llm.model",
        ),
        (
            lambda: {
                **valid_config(),
                "llm": {"endpoint": "not-a-url", "model": "model"},
            },
            "llm.endpoint",
        ),
    ),
    ids=("missing-model", "invalid-endpoint"),
)
def test_config_reports_required_llm_fields(
    tmp_path: Path, raw: Callable[[], dict[str, Any]], location: str
) -> None:
    with pytest.raises(ConfigError, match=location):
        load_settings(write_config(tmp_path, raw()))


@pytest.mark.parametrize(
    ("section", "field", "location"),
    (
        ("llm", "model", "llm.model"),
        ("validators", "id", "validators.0.id"),
        ("validators", "program", "validators.0.program"),
    ),
)
def test_config_rejects_whitespace_only_required_strings(
    tmp_path: Path, section: str, field: str, location: str
) -> None:
    raw = valid_config()
    target = raw[section][0] if section == "validators" else raw[section]
    target[field] = "   "

    with pytest.raises(ConfigError, match=location):
        load_settings(write_config(tmp_path, raw))


def test_config_strips_declared_nonempty_strings(tmp_path: Path) -> None:
    raw = valid_config()
    raw["llm"]["model"] = "  test-model  "
    raw["validators"][0]["id"] = "  pytest  "
    raw["validators"][0]["program"] = "  python  "

    settings = load_settings(write_config(tmp_path, raw))

    assert settings.llm.model == "test-model"
    assert settings.validators[0].id == "pytest"
    assert settings.validators[0].program == "python"


@pytest.mark.parametrize(
    "raw",
    ([], "not a mapping", 123),
    ids=("sequence", "string", "number"),
)
def test_config_requires_mapping_root(tmp_path: Path, raw: object) -> None:
    with pytest.raises(ConfigError, match="configuration root must be a mapping"):
        load_settings(write_config(tmp_path, raw))


def test_config_reports_malformed_yaml_without_echoing_source(tmp_path: Path) -> None:
    path = tmp_path / "safefix.yaml"
    source = "llm: [unterminated"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_settings(path)
    assert "invalid YAML at line" in str(error.value)
    assert source not in str(error.value)


def test_malformed_yaml_traceback_does_not_disclose_source(tmp_path: Path) -> None:
    sentinel = "MALFORMED_YAML_TRACEBACK_SENTINEL"
    path = tmp_path / "safefix.yaml"
    path.write_text(f"llm: [{sentinel}", encoding="utf-8")

    with pytest.raises(ConfigError) as error:
        load_settings(path)

    assert "invalid YAML at line 1, column" in str(error.value)
    assert sentinel not in "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_config_converts_invalid_utf8_to_safe_file_error(tmp_path: Path) -> None:
    path = tmp_path / "safefix.yaml"
    path.write_bytes(b"\xff")

    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_settings(path)


def test_config_converts_missing_file_to_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_settings(tmp_path / "missing.yaml")


def test_config_does_not_expand_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEFIX_MODEL", "expanded-secret-value")
    raw = valid_config()
    raw["llm"]["model"] = "${SAFEFIX_MODEL}"

    settings = load_settings(write_config(tmp_path, raw))

    assert settings.llm.model == "${SAFEFIX_MODEL}"


@pytest.mark.parametrize(
    ("values", "message"),
    (
        ({"repair_rounds": 0}, "repair_rounds"),
        ({"repair_rounds": 11}, "repair_rounds"),
        ({"no_progress_rounds": 0}, "no_progress_rounds"),
        ({"no_progress_rounds": 11}, "no_progress_rounds"),
        ({"total_steps": 0}, "total_steps"),
        ({"total_steps": 1001}, "total_steps"),
        ({"wall_time_seconds": 0}, "wall_time_seconds"),
        ({"wall_time_seconds": 86_401}, "wall_time_seconds"),
        ({"repair_rounds": 3, "no_progress_rounds": 4}, "no_progress_rounds"),
        ({"repair_rounds": 3, "total_steps": 2}, "total_steps"),
    ),
)
def test_budget_enforces_bounds_and_cross_field_rules(
    values: dict[str, int], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        BudgetSettings(**values)


def test_budget_accepts_inclusive_boundaries() -> None:
    assert (
        BudgetSettings(
            repair_rounds=1,
            no_progress_rounds=1,
            total_steps=1,
            wall_time_seconds=1,
        ).total_steps
        == 1
    )
    assert (
        BudgetSettings(
            repair_rounds=10,
            no_progress_rounds=10,
            total_steps=1000,
            wall_time_seconds=86_400,
        ).wall_time_seconds
        == 86_400
    )


@pytest.mark.parametrize(
    ("retrieval_limit", "character_budget"),
    ((0, 4000), (51, 4000), (5, 255), (5, 100_001)),
)
def test_memory_enforces_bounds(retrieval_limit: int, character_budget: int) -> None:
    with pytest.raises(ValidationError):
        MemorySettings(
            retrieval_limit=retrieval_limit,
            character_budget=character_budget,
        )


def test_memory_accepts_inclusive_boundaries() -> None:
    assert MemorySettings(retrieval_limit=1, character_budget=256).retrieval_limit == 1
    assert (
        MemorySettings(retrieval_limit=50, character_budget=100_000).character_budget
        == 100_000
    )


def test_policy_rejects_duplicate_entries_after_stripping_and_casefolding() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        PolicySettings(allowed_programs=("Python", " python "))


@pytest.mark.parametrize(
    "field",
    ("sensitive_patterns", "allowed_programs", "denied_programs"),
)
def test_policy_rejects_whitespace_only_entries(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        PolicySettings.model_validate({field: ["   "]})


def test_settings_are_frozen_and_forbid_unknown_fields() -> None:
    validator = ValidatorSettings(
        id="pytest",
        kind="test",
        program="python",
        args=("-m", "pytest"),
        timeout_seconds=120,
        success_exit_codes=frozenset({0}),
        output_limit_bytes=65_536,
    )
    settings = SafeFixSettings.model_validate(valid_config())
    with pytest.raises(ValidationError):
        validator.program = "pytest"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SafeFixSettings.model_validate({**valid_config(), "unknown": True})
    assert settings.policy.sensitive_patterns == (".env", "**/*.pem", "**/.ssh/**")


def test_config_error_does_not_disclose_rejected_secret_value(tmp_path: Path) -> None:
    secret = "do-not-echo-this-secret"
    raw = valid_config()
    raw["llm"]["api_key"] = secret  # type: ignore[index]
    with pytest.raises(ConfigError) as error:
        load_settings(write_config(tmp_path, raw))
    assert "llm.api_key" in str(error.value)
    assert secret not in str(error.value)


def test_validation_traceback_does_not_disclose_rejected_secret(
    tmp_path: Path,
) -> None:
    sentinel = "PYDANTIC_TRACEBACK_SECRET_SENTINEL"
    raw = valid_config()
    raw["llm"]["api_key"] = sentinel

    with pytest.raises(ConfigError) as error:
        load_settings(write_config(tmp_path, raw))

    assert "llm.api_key" in str(error.value)
    assert sentinel not in "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_example_configuration_loads_without_any_key_field() -> None:
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(root / "examples" / "safefix.yaml")
    assert settings.model_dump(mode="json")["validators"][0]["id"] == "pytest"
    assert "key" not in yaml.safe_dump(settings.model_dump(mode="json")).casefold()
