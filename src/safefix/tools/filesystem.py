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
_CASE_INSENSITIVE_PATHS = os.path.normcase("A") == os.path.normcase("a")


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


def _lexical_path(workspace: Path, requested_path: str) -> Path:
    candidate = Path(requested_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return Path(os.path.abspath(candidate))


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
    match_relative = relative.casefold() if _CASE_INSENSITIVE_PATHS else relative
    match_ignored = (
        tuple(item.casefold() for item in ignored)
        if _CASE_INSENSITIVE_PATHS
        else ignored
    )
    parts = PurePosixPath(match_relative).parts
    if ".git" in parts:
        return True
    return any(
        match_relative == item or match_relative.startswith(f"{item}/")
        for item in match_ignored
    )


def _raise_walk_error(error: OSError) -> None:
    raise error


def _is_directory_link(path: Path) -> bool:
    junction_check = getattr(path, "is_junction", None)
    return path.is_symlink() or (
        junction_check is not None and bool(junction_check())
    )


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
        requested_path = ""
        pattern = ""
        matcher: pathspec.PathSpec | None = None
        lexical_root: Path | None = None
        root: Path | None = None
        verified_root: Path | None = None
        current = ""
        directories: list[str] = []
        filenames: list[str] = []
        current_path: Path | None = None
        retained_directories: list[str] = []
        directory = ""
        filename = ""
        candidate: Path | None = None
        relative = ""
        matches: list[str] = []
        selected: list[str] = []
        output = ""
        try:
            action_id = action.id
            requested_path = action.path
            pattern = action.pattern
            limit = action.limit
            started_ns = time.monotonic_ns()

            matcher = _compile_gitwildmatch(pattern)
            if matcher is None:
                return ToolResult.failure(
                    action_id, "INVALID_GLOB", "file pattern is invalid"
                )

            try:
                root = self._boundary.resolve(requested_path, AccessKind.LIST)
                lexical_root = _lexical_path(self._workspace, requested_path)
                if _is_directory_link(lexical_root):
                    return _path_denied(action_id)
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
                verified_root = self._boundary.resolve(
                    requested_path, AccessKind.LIST
                )
                if verified_root != root:
                    return _path_denied(action_id)
                root = verified_root
                relative = _relative_posix(self._workspace, root)
                if _is_ignored_directory(
                    relative, self._ignored_directories
                ):
                    return _path_denied(action_id)

                for current, directories, filenames in os.walk(
                    root,
                    topdown=True,
                    onerror=_raise_walk_error,
                    followlinks=False,
                ):
                    current_path = Path(current)
                    retained_directories = []
                    for directory in sorted(directories):
                        candidate = current_path / directory
                        relative = _relative_posix(self._workspace, candidate)
                        if _is_directory_link(candidate) or _is_ignored_directory(
                            relative, self._ignored_directories
                        ):
                            continue
                        try:
                            self._boundary.resolve(
                                str(candidate), AccessKind.LIST
                            )
                        except _PATH_ERRORS:
                            continue
                        retained_directories.append(directory)
                    directories[:] = retained_directories

                    for filename in sorted(filenames):
                        candidate = current_path / filename
                        relative = _relative_posix(self._workspace, candidate)
                        try:
                            self._boundary.resolve(
                                str(candidate), AccessKind.LIST
                            )
                        except _PATH_ERRORS:
                            continue
                        if matcher.match_file(relative):
                            matches.append(relative)

                matches.sort()
                truncated = len(matches) > limit
                selected = matches[:limit]
                output = "\n".join(selected)
                if truncated:
                    output = (
                        f"{output}\n[truncated]" if output else "[truncated]"
                    )
                return _success(action_id, output, started_ns)
            except _PATH_ERRORS:
                return _path_denied(action_id)
            except Exception:
                return _io_failure(action_id)
        finally:
            del (
                action,
                requested_path,
                pattern,
                matcher,
                lexical_root,
                root,
                verified_root,
                current,
                directories,
                filenames,
                current_path,
                retained_directories,
                directory,
                filename,
                candidate,
                relative,
                matches,
                selected,
                output,
            )


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
        requested_path = ""
        target: Path | None = None
        verified_target: Path | None = None
        stream: object | None = None
        raw = b""
        text = ""
        lines: list[str] = []
        output = ""
        try:
            try:
                action_id = action.id
                requested_path = action.path
                start_line = action.start_line
                end_line = action.end_line
                started_ns = time.monotonic_ns()

                target = self._boundary.resolve(
                    requested_path, AccessKind.READ
                )
                if not target.exists():
                    return ToolResult.failure(
                        action_id, "NOT_FOUND", "requested path does not exist"
                    )
                if not target.is_file():
                    return ToolResult.failure(
                        action_id, "NOT_FILE", "requested path is not a file"
                    )
                verified_target = self._boundary.resolve(
                    requested_path, AccessKind.READ
                )
                if verified_target != target:
                    return _path_denied(action_id)
                target = verified_target

                try:
                    with target.open("rb") as stream:
                        raw = stream.read()
                    text = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    return ToolResult.failure(
                        action_id,
                        "BINARY_FILE",
                        "file is not valid UTF-8 text",
                    )

                lines = text.splitlines(keepends=True)
                output = "".join(lines[start_line - 1 : end_line])
                if len(output.encode("utf-8")) > self._limits.max_read_bytes:
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
        finally:
            del (
                action,
                requested_path,
                target,
                verified_target,
                stream,
                raw,
                text,
                lines,
                output,
            )
