from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

import pathspec

from safefix.domain import AccessKind


_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class PathOutsideWorkspace(ValueError):
    """Raised when a requested path is outside the configured workspace."""


class SensitivePathDenied(ValueError):
    """Raised when a requested path matches a sensitive-path rule."""


class SymlinkEscapeDenied(PathOutsideWorkspace):
    """Raised when an in-workspace path resolves outside through a symlink."""


def _is_inside(root: Path, target: Path) -> bool:
    root_key = os.path.normcase(os.path.abspath(root))
    target_key = os.path.normcase(os.path.abspath(target))
    try:
        return os.path.commonpath((root_key, target_key)) == root_key
    except ValueError:
        return False


def _is_unsafe_windows_candidate(candidate: str) -> bool:
    if os.name != "nt":
        return False

    normalized = candidate.replace("/", "\\")
    if normalized.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return True

    path = PureWindowsPath(candidate)
    for component in path.parts:
        if component == path.anchor:
            continue
        if ":" in component:
            return True
        basename = component.rstrip(" .").split(".", maxsplit=1)[0].rstrip(" ")
        if basename.upper() in _WINDOWS_RESERVED_NAMES:
            return True
    return False


class WorkspaceBoundary:
    """Resolve tool paths within one canonical workspace."""

    def __init__(self, workspace: Path, sensitive_patterns: tuple[str, ...]) -> None:
        self._configured_root = Path(os.path.abspath(workspace))
        self._root = workspace.resolve(strict=False)
        self._case_insensitive = os.path.normcase("A") == os.path.normcase("a")
        patterns = sensitive_patterns
        if self._case_insensitive:
            patterns = tuple(pattern.casefold() for pattern in patterns)
        self._sensitive = pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    def resolve(self, candidate: str, access: AccessKind) -> Path:
        """Return a canonical path if it remains inside policy boundaries."""
        if _is_unsafe_windows_candidate(candidate):
            del candidate
            raise PathOutsideWorkspace("path is outside the workspace")

        lexical = Path(candidate)
        if not lexical.is_absolute():
            lexical = self._configured_root / lexical

        if not (
            _is_inside(self._configured_root, lexical)
            or _is_inside(self._root, lexical)
        ):
            raise PathOutsideWorkspace("path is outside the workspace")

        resolution_failed = False
        try:
            resolved = lexical.resolve(strict=False)
        except (OSError, RuntimeError):
            resolution_failed = True
        if resolution_failed:
            del candidate, lexical
            raise PathOutsideWorkspace("path cannot be resolved safely")

        if not _is_inside(self._root, resolved):
            raise SymlinkEscapeDenied("path escapes the workspace through a symlink")

        relative = Path(os.path.relpath(resolved, self._root)).as_posix()
        match_path = relative
        if resolved.is_dir() or access in {AccessKind.LIST, AccessKind.SEARCH}:
            match_path = f"{relative}/"
        if self._case_insensitive:
            match_path = match_path.casefold()
        if self._sensitive.match_file(match_path):
            raise SensitivePathDenied("sensitive path access is denied")

        return resolved
