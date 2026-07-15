from __future__ import annotations

import os
from enum import Enum, auto
from pathlib import Path, PureWindowsPath

import pathspec

from safefix.domain import AccessKind


class PathOutsideWorkspace(ValueError):
    """Raised when a requested path is outside the configured workspace."""


class SensitivePathDenied(ValueError):
    """Raised when a requested path matches a sensitive-path rule."""


class SymlinkEscapeDenied(PathOutsideWorkspace):
    """Raised when an in-workspace path resolves outside through a symlink."""


class _PathFailure(Enum):
    OUTSIDE = auto()
    UNRESOLVABLE = auto()
    SYMLINK_ESCAPE = auto()
    SENSITIVE = auto()


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
        if PureWindowsPath(component).is_reserved():
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

    def _resolve_candidate(
        self, candidate: str, access: AccessKind
    ) -> Path | _PathFailure:
        if _is_unsafe_windows_candidate(candidate):
            return _PathFailure.OUTSIDE

        lexical = Path(candidate)
        if not lexical.is_absolute():
            lexical = self._configured_root / lexical

        if not (
            _is_inside(self._configured_root, lexical)
            or _is_inside(self._root, lexical)
        ):
            return _PathFailure.OUTSIDE

        try:
            resolved = lexical.resolve(strict=False)
        except (OSError, RuntimeError):
            return _PathFailure.UNRESOLVABLE

        if not _is_inside(self._root, resolved):
            return _PathFailure.SYMLINK_ESCAPE

        relative = Path(os.path.relpath(resolved, self._root)).as_posix()
        match_path = relative
        if resolved.is_dir() or access in {AccessKind.LIST, AccessKind.SEARCH}:
            match_path = f"{relative}/"
        if self._case_insensitive:
            match_path = match_path.casefold()
        if self._sensitive.match_file(match_path):
            return _PathFailure.SENSITIVE

        return resolved

    def resolve(self, candidate: str, access: AccessKind) -> Path:
        """Return a canonical path if it remains inside policy boundaries."""
        outcome = self._resolve_candidate(candidate, access)
        del candidate

        if isinstance(outcome, Path):
            return outcome
        if outcome is _PathFailure.OUTSIDE:
            raise PathOutsideWorkspace("path is outside the workspace")
        if outcome is _PathFailure.UNRESOLVABLE:
            raise PathOutsideWorkspace("path cannot be resolved safely")
        if outcome is _PathFailure.SYMLINK_ESCAPE:
            raise SymlinkEscapeDenied("path escapes the workspace through a symlink")
        if outcome is _PathFailure.SENSITIVE:
            raise SensitivePathDenied("sensitive path access is denied")
        raise AssertionError("unknown path resolution outcome")
