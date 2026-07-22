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
    SearchTextAction,
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
        if (
            min(
                self.max_read_bytes,
                self.max_search_files,
                self.max_search_output_bytes,
            )
            <= 0
        ):
            raise ValueError("filesystem limits must be positive")


def _normalize_ignored_directories(directories: tuple[str, ...]) -> tuple[str, ...]:
    normalized = {".git"}
    directory = ""
    components: list[str] = []
    component = ""
    unsafe_component = False
    path: PurePosixPath | None = None
    try:
        for directory in directories:
            components = directory.split("/")
            path = PurePosixPath(directory)
            unsafe_component = False
            for component in components:
                if component in {"", ".", ".."}:
                    unsafe_component = True
                    break
            if (
                not directory
                or "\\" in directory
                or path.is_absolute()
                or unsafe_component
                or ":" in components[0]
            ):
                raise ValueError(
                    "ignored directories must be safe relative POSIX paths"
                )
            normalized.add(path.as_posix())
        return tuple(sorted(normalized))
    finally:
        del (
            directories,
            normalized,
            directory,
            components,
            component,
            unsafe_component,
            path,
        )


def _relative_posix(root: Path, target: Path) -> str:
    try:
        return target.relative_to(root).as_posix()
    finally:
        del root, target


def _lexical_path(workspace: Path, requested_path: str) -> Path:
    candidate: Path | None = None
    try:
        candidate = Path(requested_path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        return candidate
    finally:
        del workspace, requested_path, candidate


def _contains_parent_reference(requested_path: str) -> bool:
    normalized = ""
    components: list[str] = []
    component = ""
    try:
        normalized = requested_path.replace("\\", "/")
        components = normalized.split("/")
        for component in components:
            if component == "..":
                return True
        return False
    finally:
        del requested_path, normalized, components, component


def _compile_gitwildmatch(pattern: str) -> pathspec.PathSpec | None:
    try:
        try:
            return pathspec.PathSpec.from_lines("gitwildmatch", (pattern,))
        except Exception:
            return None
    finally:
        del pattern


def _path_denied(action_id: str) -> ToolResult:
    return ToolResult.failure(action_id, "PATH_DENIED", "path access is denied")


def _io_failure(action_id: str) -> ToolResult:
    return ToolResult.failure(action_id, "IO_ERROR", "filesystem operation failed")


def _success(action_id: str, output: str, started_ns: int) -> ToolResult:
    duration_ms = 0
    try:
        duration_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
        return ToolResult(
            action_id=action_id,
            success=True,
            stdout_summary=output,
            duration_ms=duration_ms,
        )
    finally:
        del action_id, output, started_ns, duration_ms


def _is_ignored_directory(relative: str, ignored: tuple[str, ...]) -> bool:
    match_relative = ""
    match_ignored: tuple[str, ...] = ()
    casefolded_ignored: list[str] = []
    entry = ""
    parts: tuple[str, ...] = ()
    item = ""
    try:
        match_relative = relative.casefold() if _CASE_INSENSITIVE_PATHS else relative
        if _CASE_INSENSITIVE_PATHS:
            for entry in ignored:
                casefolded_ignored.append(entry.casefold())
            match_ignored = tuple(casefolded_ignored)
        else:
            match_ignored = ignored
        parts = PurePosixPath(match_relative).parts
        if ".git" in parts:
            return True
        for item in match_ignored:
            if match_relative == item or match_relative.startswith(f"{item}/"):
                return True
        return False
    finally:
        del (
            relative,
            ignored,
            match_relative,
            match_ignored,
            casefolded_ignored,
            entry,
            parts,
            item,
        )


def _raise_walk_error(error: OSError) -> None:
    try:
        raise error
    finally:
        del error


def _is_directory_link(path: Path) -> bool:
    junction_check = None
    try:
        junction_check = getattr(path, "is_junction", None)
        return path.is_symlink() or (
            junction_check is not None and bool(junction_check())
        )
    finally:
        del path, junction_check


def _contains_directory_link(
    configured_workspace: Path, workspace: Path, target: Path
) -> bool:
    base: Path | None = None
    relative: Path | None = None
    current: Path | None = None
    components: tuple[str, ...] = ()
    component = ""
    try:
        if target.is_relative_to(configured_workspace):
            base = configured_workspace
        else:
            base = workspace
        relative = target.relative_to(base)
        current = base
        components = relative.parts
        for component in components:
            current /= component
            if _is_directory_link(current):
                return True
        return False
    finally:
        del (
            configured_workspace,
            workspace,
            target,
            base,
            relative,
            current,
            components,
            component,
        )


class ListFilesTool:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        ignored_directories: tuple[str, ...] = (),
    ) -> None:
        try:
            self._boundary = boundary
            self._ignored_directories = _normalize_ignored_directories(
                ignored_directories
            )
            self._configured_workspace = boundary._configured_root
            self._workspace = boundary.resolve(".", AccessKind.LIST)
        finally:
            del self, boundary, ignored_directories

    @property
    def action_type(self) -> type[object]:
        return ListFilesAction

    async def execute(self, action: Action) -> ToolResult:
        action_id = ""
        typed_action: ListFilesAction | None = None
        try:
            if not isinstance(action, ListFilesAction):
                action_id = action.id
                return ToolResult.failure(
                    action_id,
                    "UNSUPPORTED_ACTION",
                    "tool does not support this action",
                )
            typed_action = action
            return await asyncio.to_thread(self._execute_sync, typed_action)
        finally:
            del self, action, action_id, typed_action

    def _execute_sync(self, action: ListFilesAction) -> ToolResult:
        action_id = ""
        limit = 0
        started_ns = 0
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
        truncated = False
        failure_type = ""
        try:
            action_id = action.id
            requested_path = action.path
            pattern = action.pattern
            limit = action.limit
            started_ns = time.monotonic_ns()

            if _contains_parent_reference(requested_path):
                return _path_denied(action_id)
            matcher = _compile_gitwildmatch(pattern)
            if matcher is None:
                return ToolResult.failure(
                    action_id, "INVALID_GLOB", "file pattern is invalid"
                )

            try:
                root = self._boundary.resolve(requested_path, AccessKind.LIST)
                lexical_root = _lexical_path(self._configured_workspace, requested_path)
                if _contains_directory_link(
                    self._configured_workspace, self._workspace, lexical_root
                ):
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
                verified_root = self._boundary.resolve(requested_path, AccessKind.LIST)
                if verified_root != root:
                    return _path_denied(action_id)
                root = verified_root
                relative = _relative_posix(self._workspace, root)
                if _is_ignored_directory(relative, self._ignored_directories):
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
                return _success(action_id, output, started_ns)
            except _PATH_ERRORS:
                failure_type = "path"
            except Exception:
                failure_type = "io"
            if failure_type == "path":
                return _path_denied(action_id)
            return _io_failure(action_id)
        finally:
            del (
                self,
                action,
                action_id,
                limit,
                started_ns,
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
                truncated,
                failure_type,
            )


class ReadFileTool:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        limits: FilesystemLimits | None = None,
    ) -> None:
        try:
            self._boundary = boundary
            self._limits = limits or FilesystemLimits()
            self._configured_workspace = boundary._configured_root
            self._workspace = boundary.resolve(".", AccessKind.READ)
        finally:
            del self, boundary, limits

    @property
    def action_type(self) -> type[object]:
        return ReadFileAction

    async def execute(self, action: Action) -> ToolResult:
        action_id = ""
        typed_action: ReadFileAction | None = None
        try:
            if not isinstance(action, ReadFileAction):
                action_id = action.id
                return ToolResult.failure(
                    action_id,
                    "UNSUPPORTED_ACTION",
                    "tool does not support this action",
                )
            typed_action = action
            return await asyncio.to_thread(self._execute_sync, typed_action)
        finally:
            del self, action, action_id, typed_action

    def _execute_sync(self, action: ReadFileAction) -> ToolResult:
        action_id = ""
        start_line = 0
        end_line = 0
        started_ns = 0
        requested_path = ""
        lexical_target: Path | None = None
        target: Path | None = None
        verified_target: Path | None = None
        stream: object | None = None
        raw = b""
        text = ""
        lines: list[str] = []
        output = ""
        decode_failed = False
        failure_type = ""
        try:
            try:
                action_id = action.id
                requested_path = action.path
                start_line = action.start_line
                end_line = action.end_line
                started_ns = time.monotonic_ns()

                if _contains_parent_reference(requested_path):
                    return _path_denied(action_id)
                target = self._boundary.resolve(requested_path, AccessKind.READ)
                lexical_target = _lexical_path(
                    self._configured_workspace, requested_path
                )
                if _contains_directory_link(
                    self._configured_workspace,
                    self._workspace,
                    lexical_target,
                ):
                    return _path_denied(action_id)
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
                if _contains_directory_link(
                    self._configured_workspace,
                    self._workspace,
                    lexical_target,
                ):
                    return _path_denied(action_id)
                target = verified_target

                try:
                    with target.open("rb") as stream:
                        raw = stream.read()
                    text = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    decode_failed = True
                if decode_failed:
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
                failure_type = "path"
            except Exception:
                failure_type = "io"
            if failure_type == "path":
                return _path_denied(action_id)
            return _io_failure(action_id)
        finally:
            del (
                self,
                action,
                action_id,
                start_line,
                end_line,
                started_ns,
                requested_path,
                lexical_target,
                target,
                verified_target,
                stream,
                raw,
                text,
                lines,
                output,
                decode_failed,
                failure_type,
            )


class SearchTextTool:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        limits: FilesystemLimits | None = None,
        ignored_directories: tuple[str, ...] = (),
    ) -> None:
        try:
            self._boundary = boundary
            self._limits = limits or FilesystemLimits()
            self._ignored_directories = _normalize_ignored_directories(
                ignored_directories
            )
            self._configured_workspace = boundary._configured_root
            self._workspace = boundary.resolve(".", AccessKind.SEARCH)
        finally:
            del self, boundary, limits, ignored_directories

    @property
    def action_type(self) -> type[object]:
        return SearchTextAction

    async def execute(self, action: Action) -> ToolResult:
        action_id = ""
        typed_action: SearchTextAction | None = None
        try:
            if not isinstance(action, SearchTextAction):
                action_id = action.id
                return ToolResult.failure(
                    action_id,
                    "UNSUPPORTED_ACTION",
                    "tool does not support this action",
                )
            typed_action = action
            return await asyncio.to_thread(self._execute_sync, typed_action)
        finally:
            del self, action, action_id, typed_action

    def _execute_sync(self, action: SearchTextAction) -> ToolResult:
        action_id = ""
        started_ns = 0
        requested_path = ""
        pattern = ""
        file_glob = ""
        max_results = 0
        matcher: pathspec.PathSpec | None = None
        lexical_root: Path | None = None
        root: Path | None = None
        verified_root: Path | None = None
        direct_file = False
        root_relative = ""
        current = ""
        directories: list[str] = []
        filenames: list[str] = []
        current_path: Path | None = None
        retained_directories: list[str] = []
        directory = ""
        filename = ""
        candidate: Path | None = None
        relative = ""
        candidates: list[str] = []
        scanned = 0
        candidate_denied = False
        lexical_target: Path | None = None
        target: Path | None = None
        verified_target: Path | None = None
        stream: object | None = None
        raw = b""
        text = ""
        lines: list[str] = []
        line_number = 0
        line = ""
        record = ""
        proposed = ""
        results: list[str] = []
        output = ""
        decode_failed = False
        truncated = False
        failure_type = ""
        try:
            try:
                action_id = action.id
                started_ns = time.monotonic_ns()
                requested_path = action.path
                pattern = action.pattern
                file_glob = action.file_glob
                max_results = action.max_results

                if _contains_parent_reference(requested_path):
                    return _path_denied(action_id)
                matcher = _compile_gitwildmatch(file_glob)
                if matcher is None:
                    return ToolResult.failure(
                        action_id, "INVALID_GLOB", "file pattern is invalid"
                    )

                root = self._boundary.resolve(requested_path, AccessKind.SEARCH)
                lexical_root = _lexical_path(self._configured_workspace, requested_path)
                if _contains_directory_link(
                    self._configured_workspace,
                    self._workspace,
                    lexical_root,
                ):
                    return _path_denied(action_id)
                if not root.exists():
                    return ToolResult.failure(
                        action_id, "NOT_FOUND", "requested path does not exist"
                    )
                if not root.is_file() and not root.is_dir():
                    return ToolResult.failure(
                        action_id,
                        "NOT_FILE",
                        "requested path is not a file or directory",
                    )
                verified_root = self._boundary.resolve(
                    requested_path, AccessKind.SEARCH
                )
                if verified_root != root:
                    return _path_denied(action_id)
                if _contains_directory_link(
                    self._configured_workspace,
                    self._workspace,
                    lexical_root,
                ):
                    return _path_denied(action_id)
                root = verified_root
                root_relative = _relative_posix(self._workspace, root)
                if _is_ignored_directory(root_relative, self._ignored_directories):
                    return _path_denied(action_id)

                direct_file = root.is_file()
                if direct_file:
                    if matcher.match_file(root_relative):
                        candidates.append(root_relative)
                else:
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
                                self._boundary.resolve(relative, AccessKind.SEARCH)
                            except _PATH_ERRORS:
                                continue
                            retained_directories.append(directory)
                        directories[:] = retained_directories

                        for filename in sorted(filenames):
                            candidate = current_path / filename
                            relative = _relative_posix(self._workspace, candidate)
                            if matcher.match_file(relative):
                                candidates.append(relative)

                candidates.sort()
                for relative in candidates:
                    if scanned >= self._limits.max_search_files:
                        truncated = True
                        break
                    scanned += 1
                    candidate_denied = False
                    try:
                        target = self._boundary.resolve(relative, AccessKind.READ)
                    except _PATH_ERRORS:
                        candidate_denied = True
                    if candidate_denied:
                        if direct_file:
                            failure_type = "path"
                            break
                        continue
                    assert target is not None

                    lexical_target = _lexical_path(self._configured_workspace, relative)
                    if _contains_directory_link(
                        self._configured_workspace,
                        self._workspace,
                        lexical_target,
                    ):
                        if direct_file:
                            failure_type = "path"
                            break
                        continue
                    if not target.exists():
                        if direct_file:
                            return ToolResult.failure(
                                action_id,
                                "NOT_FOUND",
                                "requested path does not exist",
                            )
                        continue
                    if not target.is_file():
                        if direct_file:
                            return ToolResult.failure(
                                action_id,
                                "NOT_FILE",
                                "requested path is not a file",
                            )
                        continue

                    candidate_denied = False
                    try:
                        verified_target = self._boundary.resolve(
                            relative, AccessKind.READ
                        )
                    except _PATH_ERRORS:
                        candidate_denied = True
                    if candidate_denied or verified_target != target:
                        if direct_file:
                            failure_type = "path"
                            break
                        continue
                    assert verified_target is not None
                    if _contains_directory_link(
                        self._configured_workspace,
                        self._workspace,
                        lexical_target,
                    ):
                        if direct_file:
                            failure_type = "path"
                            break
                        continue
                    target = verified_target

                    decode_failed = False
                    try:
                        with target.open("rb") as stream:
                            raw = stream.read()
                        text = raw.decode("utf-8", errors="strict")
                    except UnicodeDecodeError:
                        decode_failed = True
                    if decode_failed:
                        if direct_file:
                            return ToolResult.failure(
                                action_id,
                                "BINARY_FILE",
                                "file is not valid UTF-8 text",
                            )
                        raw = b""
                        text = ""
                        continue

                    lines = text.splitlines()
                    for line_number, line in enumerate(lines, start=1):
                        if pattern not in line:
                            continue
                        if len(results) >= max_results:
                            truncated = True
                            break
                        record = f"{relative}:{line_number}:{line}"
                        proposed = "\n".join((*results, record))
                        if (
                            len(proposed.encode("utf-8"))
                            > self._limits.max_search_output_bytes
                        ):
                            truncated = True
                            break
                        results.append(record)
                    raw = b""
                    text = ""
                    lines = []
                    line = ""
                    record = ""
                    proposed = ""
                    if truncated:
                        break

                if failure_type == "path":
                    return _path_denied(action_id)

                if truncated:
                    while results:
                        output = f"{'\n'.join(results)}\n[truncated]"
                        if (
                            len(output.encode("utf-8"))
                            <= self._limits.max_search_output_bytes
                        ):
                            break
                        results.pop()
                    output = (
                        f"{'\n'.join(results)}\n[truncated]"
                        if results
                        else "[truncated]"
                    )
                else:
                    output = "\n".join(results)
                return _success(action_id, output, started_ns)
            except _PATH_ERRORS:
                failure_type = "path"
            except Exception:
                failure_type = "io"
            if failure_type == "path":
                return _path_denied(action_id)
            return _io_failure(action_id)
        finally:
            del (
                self,
                action,
                action_id,
                started_ns,
                requested_path,
                pattern,
                file_glob,
                max_results,
                matcher,
                lexical_root,
                root,
                verified_root,
                direct_file,
                root_relative,
                current,
                directories,
                filenames,
                current_path,
                retained_directories,
                directory,
                filename,
                candidate,
                relative,
                candidates,
                scanned,
                candidate_denied,
                lexical_target,
                target,
                verified_target,
                stream,
                raw,
                text,
                lines,
                line_number,
                line,
                record,
                proposed,
                results,
                output,
                decode_failed,
                truncated,
                failure_type,
            )
