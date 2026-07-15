import os
import traceback
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


_LOOP_MARKER = "private-loop-candidate-marker"
_OS_ERROR_MARKER = "private-os-error-marker"
_DIRECT_REJECTION_MARKERS = {
    "windows_unsafe": "private-windows-unsafe-marker",
    "lexical_outside": "private-lexical-outside-marker",
    "symlink_escape": "private-symlink-escape-marker",
    "sensitive": "private-sensitive-marker",
}
_DIRECT_REJECTION_ERRORS = {
    "windows_unsafe": PathOutsideWorkspace,
    "lexical_outside": PathOutsideWorkspace,
    "symlink_escape": SymlinkEscapeDenied,
    "sensitive": SensitivePathDenied,
}
_DIRECT_REJECTION_MESSAGES = {
    "windows_unsafe": "path is outside the workspace",
    "lexical_outside": "path is outside the workspace",
    "symlink_escape": "path escapes the workspace through a symlink",
    "sensitive": "sensitive path access is denied",
}


def _resolve_loop_candidate(boundary: WorkspaceBoundary) -> Path:
    candidate = f"loop-a/{_LOOP_MARKER}"
    try:
        return boundary.resolve(candidate, AccessKind.READ)
    finally:
        del candidate


def _resolve_os_error_candidate(boundary: WorkspaceBoundary) -> Path:
    candidate = f"nested/{_OS_ERROR_MARKER}"
    try:
        return boundary.resolve(candidate, AccessKind.READ)
    finally:
        del candidate


def _resolve_direct_rejection(boundary: WorkspaceBoundary, case: str) -> Path:
    if case == "windows_unsafe":
        candidate = f"{_DIRECT_REJECTION_MARKERS[case]}:stream"
    elif case == "lexical_outside":
        candidate = f"../{_DIRECT_REJECTION_MARKERS[case]}"
    elif case == "symlink_escape":
        candidate = f"escape-link/{_DIRECT_REJECTION_MARKERS[case]}"
    else:
        candidate = f"{_DIRECT_REJECTION_MARKERS[case]}.pem"
    try:
        return boundary.resolve(candidate, AccessKind.READ)
    finally:
        del candidate


def _assert_boundary_error_is_sanitized(error: BaseException, marker: str) -> None:
    assert marker not in str(error)
    assert marker not in "".join(traceback.format_exception(error))
    traceback_with_locals = traceback.TracebackException.from_exception(
        error, capture_locals=True
    )
    assert marker not in "".join(traceback_with_locals.format())
    assert error.__cause__ is None
    assert error.__context__ is None

    current = error.__traceback__
    while current is not None:
        if current.tb_frame.f_code.co_name == "resolve":
            assert marker not in repr(current.tb_frame.f_locals)
        current = current.tb_next


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


def test_boundary_accepts_absolute_candidate_through_configured_workspace_symlink(
    tmp_path: Path,
) -> None:
    canonical_workspace = tmp_path / "canonical-repo"
    canonical_workspace.mkdir()
    configured_workspace = tmp_path / "repo-link"
    try:
        configured_workspace.symlink_to(canonical_workspace, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {type(error).__name__}")
    boundary = WorkspaceBoundary(configured_workspace, ())

    resolved = boundary.resolve(
        str(configured_workspace / "file.txt"), AccessKind.WRITE
    )

    assert resolved == (canonical_workspace / "file.txt").resolve(strict=False)


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


def test_boundary_sanitizes_symlink_loop_resolution_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    loop_a = workspace / "loop-a"
    loop_b = workspace / "loop-b"
    try:
        loop_a.symlink_to(loop_b, target_is_directory=True)
        loop_b.symlink_to(loop_a, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {type(error).__name__}")
    boundary = WorkspaceBoundary(workspace, ())

    with pytest.raises(PathOutsideWorkspace) as error_info:
        _resolve_loop_candidate(boundary)

    _assert_boundary_error_is_sanitized(error_info.value, _LOOP_MARKER)


def test_boundary_sanitizes_os_error_from_canonical_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, ())

    def fail_resolve(_path: Path, *, strict: bool) -> Path:
        del _path, strict
        raise OSError(_OS_ERROR_MARKER)

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    with pytest.raises(PathOutsideWorkspace) as error_info:
        _resolve_os_error_candidate(boundary)

    _assert_boundary_error_is_sanitized(error_info.value, _OS_ERROR_MARKER)


@pytest.mark.parametrize("case", tuple(_DIRECT_REJECTION_MARKERS))
def test_boundary_sanitizes_direct_rejection(tmp_path: Path, case: str) -> None:
    if case == "windows_unsafe" and os.name != "nt":
        pytest.skip("Windows unsafe-path semantics")

    workspace = tmp_path / "repo"
    workspace.mkdir()
    if case == "symlink_escape":
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            (workspace / "escape-link").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symlink creation is unavailable: {type(error).__name__}")
    patterns = ("**/*.pem",) if case == "sensitive" else ()
    boundary = WorkspaceBoundary(workspace, patterns)

    with pytest.raises(_DIRECT_REJECTION_ERRORS[case]) as error_info:
        _resolve_direct_rejection(boundary, case)

    assert str(error_info.value) == _DIRECT_REJECTION_MESSAGES[case]
    _assert_boundary_error_is_sanitized(
        error_info.value, _DIRECT_REJECTION_MARKERS[case]
    )


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


@pytest.mark.skipif(os.name != "nt", reason="Windows device-name semantics")
@pytest.mark.parametrize("candidate", ["CON", "nested/COM1.log"])
def test_boundary_rejects_windows_reserved_device_basename(
    tmp_path: Path, candidate: str
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, ())

    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve(candidate, AccessKind.WRITE)


@pytest.mark.skipif(os.name != "nt", reason="Windows device-name semantics")
@pytest.mark.parametrize(
    "candidate",
    [
        "COM¹",
        "nested/COM².log",
        "COM³.txt",
        "LPT¹",
        "nested/LPT².log",
        "deep/path/LPT³.txt",
    ],
)
def test_boundary_rejects_windows_superscript_reserved_device_basename(
    tmp_path: Path, candidate: str
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, ())

    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve(candidate, AccessKind.WRITE)


@pytest.mark.skipif(os.name != "nt", reason="Windows ADS semantics")
@pytest.mark.parametrize("candidate", [".env:stream", "nested/file.txt:metadata"])
def test_boundary_rejects_windows_alternate_data_stream(
    tmp_path: Path, candidate: str
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, (".env",))

    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve(candidate, AccessKind.WRITE)


@pytest.mark.skipif(os.name != "nt", reason="Windows device namespace semantics")
def test_boundary_rejects_windows_device_namespace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, ())
    device_candidate = f"\\\\?\\{workspace / 'file.txt'}"

    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve(device_candidate, AccessKind.WRITE)


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
