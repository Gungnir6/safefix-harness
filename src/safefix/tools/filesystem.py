from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pathspec

from safefix.domain import (
    AccessKind,
    Action,
    ListFilesAction,
    ReadFileAction,
    ToolResult,
)
from safefix.governance.paths import (
    PathOutsideWorkspace,
    SensitivePathDenied,
    SymlinkEscapeDenied,
    WorkspaceBoundary,
)


_PATH_ERRORS = (PathOutsideWorkspace, SensitivePathDenied, SymlinkEscapeDenied)


@dataclass(frozen=True, slots=True)
class FilesystemLimits:
    max_read_bytes: int = 65_536
    max_search_files: int = 1_000
    max_search_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if min(
            self.max_read_bytes,
            self.max_search_files,
            self.max_search_output_bytes,
        ) <= 0:
            raise ValueError("filesystem limits must be positive")


def _normalize_ignored_directories(directories: tuple[str, ...]) -> tuple[str, ...]:
    normalized = {".git"}
    for directory in directories:
        components = directory.split("/")
        path = PurePosixPath(directory)
        if (
            not directory
            or "\\" in directory
            or path.is_absolute()
            or any(component in {"", ".", ".."} for component in components)
            or ":" in components[0]
        ):
            raise ValueError(
                "ignored directories must be safe relative POSIX paths"
            )
        normalized.add(path.as_posix())
    return tuple(sorted(normalized))


def _relative_posix(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def _compile_gitwildmatch(pattern: str) -> pathspec.PathSpec | None:
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", (pattern,))
    except Exception:
        return None


def _path_denied(action_id: str) -> ToolResult:
    return ToolResult.failure(action_id, "PATH_DENIED", "path access is denied")


def _io_failure(action_id: str) -> ToolResult:
    return ToolResult.failure(action_id, "IO_ERROR", "filesystem operation failed")


def _success(action_id: str, output: str, started_ns: int) -> ToolResult:
    duration_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    return ToolResult(
        action_id=action_id,
        success=True,
        stdout_summary=output,
        duration_ms=duration_ms,
    )


def _is_ignored_directory(relative: str, ignored: tuple[str, ...]) -> bool:
    parts = PurePosixPath(relative).parts
    if ".git" in parts:
        return True
    return any(relative == item or relative.startswith(f"{item}/") for item in ignored)


class ListFilesTool:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        ignored_directories: tuple[str, ...] = (),
    ) -> None:
        self._boundary = boundary
        self._ignored_directories = _normalize_ignored_directories(
            ignored_directories
        )
        self._workspace = boundary.resolve(".", AccessKind.LIST)

    @property
    def action_type(self) -> type[object]:
        return ListFilesAction

    async def execute(self, action: Action) -> ToolResult:
        if not isinstance(action, ListFilesAction):
            action_id = action.id
            del action
            return ToolResult.failure(
                action_id,
                "UNSUPPORTED_ACTION",
                "tool does not support this action",
            )
        typed_action = action
        del action
        try:
            return await asyncio.to_thread(self._execute_sync, typed_action)
        finally:
            del typed_action

    def _execute_sync(self, action: ListFilesAction) -> ToolResult:
        action_id = action.id
        requested_path = action.path
        pattern = action.pattern
        limit = action.limit
        del action
        started_ns = time.monotonic_ns()

        matcher = _compile_gitwildmatch(pattern)
        del pattern
        if matcher is None:
            return ToolResult.failure(
                action_id, "INVALID_GLOB", "file pattern is invalid"
            )

        try:
            root = self._boundary.resolve(requested_path, AccessKind.LIST)
            del requested_path
            if not root.exists():
                return ToolResult.failure(
                    action_id, "NOT_FOUND", "requested path does not exist"
                )
            if not root.is_dir():
                return ToolResult.failure(
                    action_id,
                    "NOT_DIRECTORY",
                    "requested path is not a directory",
                )

            matches: list[str] = []

            def raise_walk_error(error: OSError) -> None:
                raise error

            for current, directories, filenames in os.walk(
                root, topdown=True, onerror=raise_walk_error, followlinks=False
            ):
                current_path = Path(current)
                retained_directories: list[str] = []
                for directory in sorted(directories):
                    candidate = current_path / directory
                    relative = _relative_posix(self._workspace, candidate)
                    if candidate.is_symlink() or _is_ignored_directory(
                        relative, self._ignored_directories
                    ):
                        continue
                    try:
                        self._boundary.resolve(str(candidate), AccessKind.LIST)
                    except _PATH_ERRORS:
                        continue
                    retained_directories.append(directory)
                directories[:] = retained_directories

                for filename in sorted(filenames):
                    candidate = current_path / filename
                    relative = _relative_posix(self._workspace, candidate)
                    try:
                        self._boundary.resolve(str(candidate), AccessKind.LIST)
                    except _PATH_ERRORS:
                        continue
                    if matcher.match_file(relative):
                        matches.append(relative)

            matches.sort()
            truncated = len(matches) > limit
            selected = matches[:limit]
            output = "\n".join(selected)
            if truncated:
                output = f"{output}\n[truncated]" if output else "[truncated]"
            del matches, selected, matcher, root
            return _success(action_id, output, started_ns)
        except _PATH_ERRORS:
            return _path_denied(action_id)
        except Exception:
            return _io_failure(action_id)


class ReadFileTool:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        limits: FilesystemLimits | None = None,
    ) -> None:
        self._boundary = boundary
        self._limits = limits or FilesystemLimits()

    @property
    def action_type(self) -> type[object]:
        return ReadFileAction

    async def execute(self, action: Action) -> ToolResult:
        if not isinstance(action, ReadFileAction):
            action_id = action.id
            del action
            return ToolResult.failure(
                action_id,
                "UNSUPPORTED_ACTION",
                "tool does not support this action",
            )
        typed_action = action
        del action
        try:
            return await asyncio.to_thread(self._execute_sync, typed_action)
        finally:
            del typed_action

    def _execute_sync(self, action: ReadFileAction) -> ToolResult:
        action_id = action.id
        requested_path = action.path
        start_line = action.start_line
        end_line = action.end_line
        del action
        started_ns = time.monotonic_ns()

        try:
            target = self._boundary.resolve(requested_path, AccessKind.READ)
            del requested_path
            if not target.exists():
                return ToolResult.failure(
                    action_id, "NOT_FOUND", "requested path does not exist"
                )
            if not target.is_file():
                return ToolResult.failure(
                    action_id, "NOT_FILE", "requested path is not a file"
                )

            try:
                with target.open("r", encoding="utf-8", errors="strict") as stream:
                    text = stream.read()
            except UnicodeDecodeError:
                return ToolResult.failure(
                    action_id, "BINARY_FILE", "file is not valid UTF-8 text"
                )

            lines = text.splitlines(keepends=True)
            del text
            output = "".join(lines[start_line - 1 : end_line])
            del lines, target
            if len(output.encode("utf-8")) > self._limits.max_read_bytes:
                del output
                return ToolResult.failure(
                    action_id,
                    "FILE_TOO_LARGE",
                    "selected file content exceeds the read limit",
                )
            return _success(action_id, output, started_ns)
        except _PATH_ERRORS:
            return _path_denied(action_id)
        except Exception:
            return _io_failure(action_id)
