from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import sys
from typing import Annotated, Literal, Self

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    model_validator,
)

from safefix.domain import NonEmptyStr


class ConfigError(ValueError):
    """Raised when a SafeFix configuration cannot be loaded safely."""


class _FrozenSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


NonEmptyItems = tuple[NonEmptyStr, ...]


def _ensure_unique(values: Iterable[str], field_name: str) -> None:
    seen: set[str] = set()
    for value in values:
        normalized = value.casefold()
        if normalized in seen:
            raise ValueError(f"{field_name} must not contain duplicate entries")
        seen.add(normalized)


class LLMSettings(_FrozenSettings):
    endpoint: HttpUrl
    model: NonEmptyStr


class ValidatorSettings(_FrozenSettings):
    id: NonEmptyStr
    kind: Literal["test", "lint", "type"]
    program: NonEmptyStr
    args: tuple[str, ...]
    timeout_seconds: int = Field(ge=1, le=3600)
    success_exit_codes: frozenset[int] = Field(min_length=1)
    output_limit_bytes: int = Field(ge=1024, le=10_485_760)


class PolicySettings(_FrozenSettings):
    sensitive_patterns: NonEmptyItems = (".env", "**/*.pem", "**/.ssh/**")
    allowed_programs: NonEmptyItems = ()
    denied_programs: NonEmptyItems = ()

    @model_validator(mode="after")
    def validate_program_rules(self) -> Self:
        _ensure_unique(self.sensitive_patterns, "sensitive_patterns")
        _ensure_unique(self.allowed_programs, "allowed_programs")
        _ensure_unique(self.denied_programs, "denied_programs")
        overlap = {program.casefold() for program in self.allowed_programs} & {
            program.casefold() for program in self.denied_programs
        }
        if overlap:
            raise ValueError("allowed_programs and denied_programs must not overlap")
        return self


class BudgetSettings(_FrozenSettings):
    repair_rounds: int = Field(default=3, ge=1, le=10)
    no_progress_rounds: int = Field(default=2, ge=1, le=10)
    total_steps: int = Field(default=20, ge=1, le=1000)
    wall_time_seconds: int = Field(default=900, ge=1, le=86_400)

    @model_validator(mode="after")
    def validate_cross_field_limits(self) -> Self:
        if self.no_progress_rounds > self.repair_rounds:
            raise ValueError("no_progress_rounds must not exceed repair_rounds")
        if self.total_steps < self.repair_rounds:
            raise ValueError("total_steps must not be below repair_rounds")
        return self


class MemorySettings(_FrozenSettings):
    retrieval_limit: int = Field(default=5, ge=1, le=50)
    character_budget: int = Field(default=4000, ge=256, le=100_000)


class SafeFixSettings(_FrozenSettings):
    llm: LLMSettings
    validators: Annotated[tuple[ValidatorSettings, ...], Field(min_length=1)]
    policy: PolicySettings = PolicySettings()
    budget: BudgetSettings = BudgetSettings()
    memory: MemorySettings = MemorySettings()

    @model_validator(mode="after")
    def validate_validator_ids(self) -> Self:
        _ensure_unique((validator.id for validator in self.validators), "validators")
        return self


def default_settings_yaml() -> str:
    raw = {
        "llm": {
            "endpoint": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini",
        },
        "validators": [
            {
                "id": "pytest",
                "kind": "test",
                "program": sys.executable,
                "args": ["-m", "pytest", "-q"],
                "timeout_seconds": 120,
                "success_exit_codes": [0],
                "output_limit_bytes": 65536,
            }
        ],
        "policy": {
            "sensitive_patterns": [".env", ".env.*", "**/*.pem", "**/.ssh/**"],
            "allowed_programs": [sys.executable, "git"],
            "denied_programs": ["sudo", "su", "powershell", "pwsh", "cmd", "sh", "bash"],
        },
        "budget": {
            "repair_rounds": 3,
            "no_progress_rounds": 2,
            "total_steps": 20,
            "wall_time_seconds": 900,
        },
        "memory": {"retrieval_limit": 5, "character_budget": 4000},
    }
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)


def _safe_yaml_error(error: yaml.YAMLError) -> str:
    mark = getattr(error, "problem_mark", None)
    if mark is None:
        return "invalid YAML"
    return f"invalid YAML at line {mark.line + 1}, column {mark.column + 1}"


def _format_validation_errors(error: ValidationError) -> str:
    messages: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"]) or "configuration"
        messages.append(f"{location}: {detail['msg']}")
    return "; ".join(messages)


def load_settings(path: Path) -> SafeFixSettings:
    error_message: str | None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        error_message = f"cannot read configuration: {path}"
    except yaml.YAMLError as exc:
        error_message = _safe_yaml_error(exc)
    else:
        error_message = None
    if error_message is not None:
        raise ConfigError(error_message)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        del raw
        raise ConfigError("configuration root must be a mapping")
    try:
        return SafeFixSettings.model_validate(raw)
    except ValidationError as exc:
        error_message = _format_validation_errors(exc)
    del raw
    raise ConfigError(error_message)
