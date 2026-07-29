from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Literal
import uuid

import pathspec


class WorkspacePreparationError(RuntimeError):
    """Raised when an execution workspace cannot be prepared safely."""


WorkspaceMode = Literal["isolated", "in_place"]


@dataclass(frozen=True)
class PreparedWorkspace:
    execution_id: str
    source: Path
    path: Path
    mode: WorkspaceMode
    metadata_path: Path | None


_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "build",
        "dist",
        ".safefix",
    }
)


def default_data_dir(
    *,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    environment = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform

    if configured := environment.get("SAFEFIX_DATA_DIR"):
        return Path(configured).expanduser().resolve()
    if platform.startswith("win"):
        local_app_data = environment.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data).expanduser().resolve() / "SafeFix"

    if xdg_data_home := environment.get("XDG_DATA_HOME"):
        return Path(xdg_data_home).expanduser().resolve() / "safefix"
    home = Path.home() if home is None else home
    return home / ".local" / "share" / "safefix"


def _metadata(
    *, execution_id: str, source: Path, mode: WorkspaceMode, run_id: str | None
) -> dict[str, str | None]:
    return {
        "execution_id": execution_id,
        "source": str(source),
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
    }


def _write_metadata(path: Path, metadata: Mapping[str, str | None]) -> None:
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise WorkspacePreparationError("cannot write execution metadata") from exc


def _copy_ignore(
    source: Path, sensitive_patterns: tuple[str, ...]
) -> Callable[[str, list[str]], set[str]]:
    try:
        sensitive_paths = pathspec.PathSpec.from_lines(
            "gitwildmatch", sensitive_patterns
        )
    except (TypeError, ValueError) as exc:
        raise WorkspacePreparationError("invalid sensitive path pattern") from exc

    def ignore(directory: str, names: list[str]) -> set[str]:
        directory_path = Path(directory)
        try:
            relative_directory = directory_path.relative_to(source)
        except ValueError:
            return set(names)

        ignored: set[str] = set()
        for name in names:
            if name in _IGNORED_NAMES:
                ignored.add(name)
                continue
            candidate = relative_directory / name
            candidate_path = candidate.as_posix()
            source_path = directory_path / name
            try:
                if source_path.is_symlink():
                    ignored.add(name)
                    continue
                matches_sensitive_pattern = sensitive_paths.match_file(candidate_path)
                if source_path.is_dir():
                    matches_sensitive_pattern |= sensitive_paths.match_file(
                        f"{candidate_path}/"
                    )
            except OSError:
                ignored.add(name)
                continue
            if matches_sensitive_pattern:
                ignored.add(name)
        return ignored

    return ignore


def _resolve_project(project: Path) -> Path:
    try:
        source = project.resolve(strict=True)
    except OSError as exc:
        raise WorkspacePreparationError("project directory does not exist") from exc
    if not source.is_dir():
        raise WorkspacePreparationError("project path must be a directory")
    return source


def prepare_workspace(
    project: Path,
    data_dir: Path,
    *,
    in_place: bool,
    sensitive_patterns: tuple[str, ...],
) -> PreparedWorkspace:
    source = _resolve_project(project)
    data_directory = data_dir.resolve(strict=False)
    if data_directory.is_relative_to(source):
        raise WorkspacePreparationError("data directory must not be inside the project")
    execution_id = str(uuid.uuid4())
    if in_place:
        return PreparedWorkspace(execution_id, source, source, "in_place", None)

    execution_directory = data_directory / "runs" / execution_id
    workspace = execution_directory / "workspace"
    try:
        execution_directory.mkdir(parents=True)
    except FileExistsError as exc:
        raise WorkspacePreparationError("execution directory already exists") from exc
    except OSError as exc:
        raise WorkspacePreparationError("cannot create execution directory") from exc

    try:
        shutil.copytree(source, workspace, ignore=_copy_ignore(source, sensitive_patterns))
    except OSError as exc:
        raise WorkspacePreparationError("cannot copy project into execution workspace") from exc

    metadata_path = execution_directory / "execution.json"
    _write_metadata(
        metadata_path,
        _metadata(
            execution_id=execution_id,
            source=source,
            mode="isolated",
            run_id=None,
        ),
    )
    return PreparedWorkspace(execution_id, source, workspace, "isolated", metadata_path)


def record_run_id(prepared: PreparedWorkspace, run_id: str) -> None:
    if prepared.mode == "in_place":
        return
    if prepared.metadata_path is None:
        raise WorkspacePreparationError("isolated workspace is missing metadata")
    try:
        raw = json.loads(prepared.metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspacePreparationError("cannot read execution metadata") from exc
    if not isinstance(raw, dict):
        raise WorkspacePreparationError("invalid execution metadata")
    raw["run_id"] = run_id
    _write_metadata(prepared.metadata_path, raw)
