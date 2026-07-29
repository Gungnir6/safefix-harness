from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import yaml  # type: ignore[import-untyped]

from safefix.config import (
    ConfigError,
    SafeFixSettings,
    default_settings_yaml,
    load_settings,
)
from safefix.credentials import CredentialError


_DEFAULT_ENDPOINT = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True, slots=True)
class SetupOptions:
    project: Path
    config: Path | None
    provider: str


def _config_path(options: SetupOptions) -> Path:
    if options.config is not None:
        return options.config
    return options.project / "safefix.yaml"


def _create_config(
    path: Path,
    *,
    input_fn: Callable[[str], str],
) -> None:
    endpoint = input_fn(f"模型接口 [{_DEFAULT_ENDPOINT}]: ").strip()
    model = input_fn(f"模型名称 [{_DEFAULT_MODEL}]: ").strip()
    raw = yaml.safe_load(default_settings_yaml())
    raw["llm"]["endpoint"] = endpoint or _DEFAULT_ENDPOINT
    raw["llm"]["model"] = model or _DEFAULT_MODEL
    try:
        settings = SafeFixSettings.model_validate(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                settings.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
            newline="\n",
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ConfigError("cannot create a valid configuration") from exc


def ensure_setup(
    options: SetupOptions,
    *,
    credential_service: Any,
    input_fn: Callable[[str], str],
    secret_input_fn: Callable[[str], str],
    stdout: TextIO,
) -> Path:
    project = options.project.resolve(strict=False)
    if not project.is_dir():
        raise ConfigError(f"project directory does not exist: {project}")
    path = _config_path(options)
    if path.exists():
        load_settings(path)
        stdout.write(f"使用配置: {path}\n")
    else:
        _create_config(path, input_fn=input_fn)
        load_settings(path)
        stdout.write(f"已创建配置: {path}\n")

    if options.provider != "mock":
        status = credential_service.status(options.provider)
        if not status.configured:
            secret = secret_input_fn("API key（隐藏输入）: ")
            credential_service.set(options.provider, secret)
            stdout.write("API key 已安全保存。\n")
        else:
            stdout.write(f"API key 已配置（{status.source or '安全存储'}）。\n")
    return path


def run_setup(
    options: SetupOptions,
    *,
    credential_service: Any,
    input_fn: Callable[[str], str],
    secret_input_fn: Callable[[str], str],
    stdout: TextIO,
) -> int:
    try:
        ensure_setup(
            options,
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
    stdout.write("SafeFix 已可使用。\n")
    return 0
