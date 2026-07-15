from __future__ import annotations

import os
from pathlib import Path

import pathspec

from safefix.domain import AccessKind


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


class WorkspaceBoundary:
    """Resolve tool paths within one canonical workspace."""

    def __init__(self, workspace: Path, sensitive_patterns: tuple[str, ...]) -> None:
        self._root = workspace.resolve(strict=False)
        self._case_insensitive = os.path.normcase("A") == os.path.normcase("a")
        patterns = sensitive_patterns
        if self._case_insensitive:
            patterns = tuple(pattern.casefold() for pattern in patterns)
        self._sensitive = pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    def resolve(self, candidate: str, access: AccessKind) -> Path:
        """Return a canonical path if it remains inside policy boundaries."""
        lexical = Path(candidate)
        if not lexical.is_absolute():
            lexical = self._root / lexical

        if not _is_inside(self._root, lexical):
            raise PathOutsideWorkspace("path is outside the workspace")

        resolved = lexical.resolve(strict=False)
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
