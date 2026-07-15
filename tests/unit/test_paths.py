import os
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from safefix.domain import AccessKind
from safefix.governance.paths import (
    PathOutsideWorkspace,
    SensitivePathDenied,
    SymlinkEscapeDenied,
    WorkspaceBoundary,
)


def test_boundary_rejects_parent_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, (".env", "**/*.pem"))

    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve("../outside.txt", AccessKind.READ)


def test_boundary_denies_sensitive_file_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".env").write_text("placeholder", encoding="utf-8")
    boundary = WorkspaceBoundary(workspace, (".env",))

    with pytest.raises(SensitivePathDenied):
        boundary.resolve(".env", AccessKind.READ)


def test_boundary_rejects_absolute_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, ())

    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve(str(tmp_path / "outside.txt"), AccessKind.WRITE)


def test_boundary_returns_canonical_path(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, ())

    resolved = boundary.resolve("nested/../file.txt", AccessKind.WRITE)

    assert resolved == (workspace / "file.txt").resolve(strict=False)


def test_boundary_rejects_lexically_inside_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {type(error).__name__}")
    boundary = WorkspaceBoundary(workspace, ())

    with pytest.raises(SymlinkEscapeDenied):
        boundary.resolve("linked/new.txt", AccessKind.WRITE)


@pytest.mark.parametrize("access", list(AccessKind))
@pytest.mark.parametrize("candidate", [".env", "keys/key.pem", ".ssh/id_key"])
def test_boundary_denies_gitwildmatch_sensitive_paths_for_every_access(
    tmp_path: Path, candidate: str, access: AccessKind
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, (".env", "**/*.pem", "**/.ssh/**"))

    with pytest.raises(SensitivePathDenied):
        boundary.resolve(candidate, access)


def test_boundary_denies_listing_sensitive_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    sensitive_directory = workspace / ".ssh"
    sensitive_directory.mkdir(parents=True)
    boundary = WorkspaceBoundary(workspace, ("**/.ssh/**",))

    with pytest.raises(SensitivePathDenied):
        boundary.resolve(".ssh", AccessKind.LIST)


@pytest.mark.skipif(os.name != "nt", reason="Windows drive semantics")
def test_boundary_rejects_absolute_path_on_different_windows_drive(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    other_drive = "Z:" if workspace.drive.casefold() != "z:" else "Y:"
    boundary = WorkspaceBoundary(workspace, ())

    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve(f"{other_drive}\\outside.txt", AccessKind.READ)


@pytest.mark.skipif(os.name != "nt", reason="Windows case semantics")
def test_boundary_denies_nonexistent_case_alias_of_sensitive_windows_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, (".env",))

    with pytest.raises(SensitivePathDenied):
        boundary.resolve(".ENV", AccessKind.WRITE)


_SEGMENTS = st.sampled_from((".", "..", "safe", "资料", "café"))
_SEPARATORS = st.sampled_from(tuple({os.sep, os.altsep or os.sep}))


@given(parts=st.lists(_SEGMENTS, min_size=1, max_size=6), separator=_SEPARATORS)
def test_every_accepted_generated_path_is_canonically_inside_workspace(
    tmp_path_factory: pytest.TempPathFactory,
    parts: list[str],
    separator: str,
) -> None:
    workspace = tmp_path_factory.mktemp("property-workspace")
    boundary = WorkspaceBoundary(workspace, ())
    candidate = separator.join(parts)

    try:
        resolved = boundary.resolve(candidate, AccessKind.WRITE)
    except PathOutsideWorkspace:
        return

    root_key = os.path.normcase(os.path.abspath(workspace.resolve(strict=False)))
    resolved_key = os.path.normcase(os.path.abspath(resolved))
    assert os.path.commonpath((root_key, resolved_key)) == root_key
