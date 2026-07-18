from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

from safefix.domain import ListFilesAction, ReadFileAction, ToolResult
from safefix.governance.paths import WorkspaceBoundary
from safefix.tools.filesystem import FilesystemLimits, ListFilesTool, ReadFileTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def boundary(workspace: Path) -> WorkspaceBoundary:
    return WorkspaceBoundary(workspace, (".env", "**/*.pem", "**/.ssh/**"))


def _filesystem_sync_frame_locals(error: BaseException) -> dict[str, Any]:
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if (
            frame.f_code.co_name == "_execute_sync"
            and frame.f_globals.get("__name__") == "safefix.tools.filesystem"
        ):
            return dict(frame.f_locals)
        traceback = traceback.tb_next
    raise AssertionError("filesystem sync traceback frame was not found")


def test_filesystem_limits_are_positive() -> None:
    with pytest.raises(ValueError, match="^filesystem limits must be positive$"):
        FilesystemLimits(max_read_bytes=0)


def test_default_filesystem_limits_are_locked() -> None:
    assert FilesystemLimits() == FilesystemLimits(
        max_read_bytes=65_536,
        max_search_files=1_000,
        max_search_output_bytes=65_536,
    )


@pytest.mark.parametrize(
    "ignored", [("",), ("../cache",), ("C:/cache",), (r"a\b",)]
)
def test_ignored_directories_require_safe_relative_posix_paths(
    boundary: WorkspaceBoundary, ignored: tuple[str, ...]
) -> None:
    with pytest.raises(
        ValueError,
        match="^ignored directories must be safe relative POSIX paths$",
    ):
        ListFilesTool(boundary, ignored_directories=ignored)


@pytest.mark.asyncio
async def test_list_files_is_sorted_bounded_and_marks_truncation(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "z.py").write_text("z", encoding="utf-8")
    (workspace / "a.py").write_text("a", encoding="utf-8")
    (workspace / "m.txt").write_text("m", encoding="utf-8")
    tool = ListFilesTool(boundary)
    action = ListFilesAction(
        id="list-1", reason="inspect", pattern="**/*.py", limit=1
    )

    result = await tool.execute(action)

    assert result.success is True
    assert result.stdout_summary == "a.py\n[truncated]"
    assert result.changed_files == ()
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_list_files_skips_git_configured_and_sensitive_descendants(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    for relative in (".git/config", "build/out.txt", ".env", "ok.txt"):
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative, encoding="utf-8")
    tool = ListFilesTool(boundary, ignored_directories=("build",))

    result = await tool.execute(
        ListFilesAction(id="list-2", reason="inspect", pattern="**/*", limit=20)
    )

    assert result.success is True
    assert result.stdout_summary == "ok.txt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_path", "ignored"), [(".git", ()), ("build", ("build",))]
)
async def test_list_files_rejects_direct_ignored_roots(
    workspace: Path,
    boundary: WorkspaceBoundary,
    requested_path: str,
    ignored: tuple[str, ...],
) -> None:
    root = workspace / requested_path
    root.mkdir()
    (root / "secret.txt").write_text("secret", encoding="utf-8")

    result = await ListFilesTool(
        boundary, ignored_directories=ignored
    ).execute(
        ListFilesAction(
            id="list-ignored-root",
            reason="inspect",
            path=requested_path,
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-ignored-root", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.path.normcase("A") != os.path.normcase("a"),
    reason="case-insensitive path semantics are required",
)
@pytest.mark.parametrize(
    ("requested_path", "ignored"), [(".GIT", ()), ("BUILD", ("build",))]
)
async def test_list_files_rejects_case_variant_ignored_roots(
    workspace: Path,
    boundary: WorkspaceBoundary,
    requested_path: str,
    ignored: tuple[str, ...],
) -> None:
    root = workspace / requested_path
    root.mkdir()
    (root / "secret.txt").write_text("secret", encoding="utf-8")

    result = await ListFilesTool(
        boundary, ignored_directories=ignored
    ).execute(
        ListFilesAction(
            id="list-ignored-case",
            reason="inspect",
            path=requested_path,
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-ignored-case", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_direct_sensitive_path_is_denied(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / ".ssh").mkdir()
    tool = ListFilesTool(boundary)

    result = await tool.execute(
        ListFilesAction(id="list-3", reason="inspect", path=".ssh", limit=20)
    )

    assert result == ToolResult.failure(
        "list-3", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_reports_missing_and_non_directory_roots(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "plain.txt").write_text("x", encoding="utf-8")
    tool = ListFilesTool(boundary)

    missing = await tool.execute(
        ListFilesAction(
            id="list-missing", reason="inspect", path="missing", limit=100
        )
    )
    non_directory = await tool.execute(
        ListFilesAction(
            id="list-file", reason="inspect", path="plain.txt", limit=100
        )
    )

    assert missing == ToolResult.failure(
        "list-missing", "NOT_FOUND", "requested path does not exist"
    )
    assert non_directory == ToolResult.failure(
        "list-file", "NOT_DIRECTORY", "requested path is not a directory"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_invalid_gitwildmatch(
    boundary: WorkspaceBoundary,
) -> None:
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-glob", reason="inspect", pattern="!", limit=100)
    )
    assert result == ToolResult.failure(
        "list-glob", "INVALID_GLOB", "file pattern is invalid"
    )


@pytest.mark.asyncio
async def test_list_files_handles_unicode_and_nested_git(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "子目录").mkdir()
    (workspace / "子目录" / "文件.py").write_text("x", encoding="utf-8")
    (workspace / "子目录" / ".git").mkdir()
    (workspace / "子目录" / ".git" / "secret.py").write_text(
        "hidden", encoding="utf-8"
    )

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-unicode", reason="inspect", pattern="**/*.py", limit=100
        )
    )

    assert result.success is True
    assert result.stdout_summary == "子目录/文件.py"


@pytest.mark.asyncio
async def test_list_files_does_not_follow_directory_symlink(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    outside = workspace.parent / f"{workspace.name}-outside"
    outside.mkdir()
    (outside / "escaped.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-link", reason="inspect", limit=100)
    )

    assert result.success is True
    assert "escaped.txt" not in result.stdout_summary


@pytest.mark.asyncio
async def test_list_files_rejects_inside_directory_symlink_root(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real"
    target.mkdir()
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-inside-link",
            reason="inspect",
            path="linked",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-inside-link", "PATH_DENIED", "path access is denied"
    )


def _create_windows_junction_or_skip(junction: Path, target: Path) -> None:
    if os.name != "nt" or not hasattr(Path, "is_junction"):
        pytest.skip("junction detection is unavailable")
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        pytest.skip("junction creation is unavailable")


@pytest.mark.asyncio
async def test_list_files_rejects_junction_root(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real"
    target.mkdir()
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    junction = workspace / "junction"
    _create_windows_junction_or_skip(junction, target)
    try:
        result = await ListFilesTool(boundary).execute(
            ListFilesAction(
                id="list-junction-root",
                reason="inspect",
                path="junction",
                limit=100,
            )
        )
    finally:
        junction.rmdir()

    assert result == ToolResult.failure(
        "list-junction-root", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_skips_junction_descendants(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real"
    target.mkdir()
    (target / "ok.txt").write_text("ok", encoding="utf-8")
    junction = workspace / "junction"
    _create_windows_junction_or_skip(junction, target)
    try:
        result = await ListFilesTool(boundary).execute(
            ListFilesAction(id="list-junction", reason="inspect", limit=100)
        )
    finally:
        junction.rmdir()

    assert result.success is True
    assert result.stdout_summary == "real/ok.txt"


@pytest.mark.asyncio
async def test_list_files_rejects_workspace_escape(
    boundary: WorkspaceBoundary,
) -> None:
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-outside", reason="inspect", path="../outside", limit=100
        )
    )
    assert result == ToolResult.failure(
        "list-outside", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rechecks_root_after_directory_validation(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = workspace / "victim"
    root.mkdir()
    outside = workspace.parent / f"{workspace.name}-outside-list"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    probe = workspace / "probe-list"
    try:
        probe.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    probe.unlink()
    real_is_dir = Path.is_dir

    def swapping_is_dir(path: Path) -> bool:
        outcome = real_is_dir(path)
        if path == root:
            path.rmdir()
            path.symlink_to(outside, target_is_directory=True)
        return outcome

    monkeypatch.setattr(Path, "is_dir", swapping_is_dir)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-race", reason="inspect", path="victim", limit=100
        )
    )

    assert result == ToolResult.failure(
        "list-race", "PATH_DENIED", "path access is denied"
    )


def test_list_files_clears_sensitive_traceback_locals(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (workspace / "private-name.txt").write_text("secret", encoding="utf-8")
    tool = ListFilesTool(boundary)
    real_resolve = boundary.resolve

    def interrupting_resolve(candidate: str, access: Any) -> Path:
        if candidate.endswith("private-name.txt"):
            raise SystemExit("PRIVATE-INTERRUPT")
        return real_resolve(candidate, access)

    monkeypatch.setattr(boundary, "resolve", interrupting_resolve)
    with pytest.raises(SystemExit) as error_info:
        tool._execute_sync(
            ListFilesAction(id="list-interrupt", reason="inspect", limit=100)
        )

    frame_locals = _filesystem_sync_frame_locals(error_info.value)
    sensitive_names = {
        "action",
        "requested_path",
        "pattern",
        "matcher",
        "lexical_root",
        "root",
        "verified_root",
        "current",
        "directories",
        "filenames",
        "current_path",
        "retained_directories",
        "directory",
        "filename",
        "candidate",
        "relative",
        "matches",
        "selected",
        "output",
    }
    assert sensitive_names.isdisjoint(frame_locals)


@pytest.mark.asyncio
async def test_read_file_returns_requested_lines_without_line_numbers(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "app.py").write_bytes(b"one\ntwo\nthree\n")
    tool = ReadFileTool(boundary)
    action = ReadFileAction(
        id="read-1", reason="inspect", path="app.py", start_line=2, end_line=3
    )

    result = await tool.execute(action)

    assert result.success is True
    assert result.stdout_summary == "two\nthree\n"
    assert result.changed_files == ()


@pytest.mark.asyncio
async def test_read_file_rejects_output_above_byte_limit(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "large.txt").write_bytes("ééé\n".encode("utf-8"))
    tool = ReadFileTool(boundary, limits=FilesystemLimits(max_read_bytes=4))

    result = await tool.execute(
        ReadFileAction(
            id="read-2",
            reason="inspect",
            path="large.txt",
            start_line=1,
            end_line=200,
        )
    )

    assert result == ToolResult.failure(
        "read-2", "FILE_TOO_LARGE", "selected file content exceeds the read limit"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_invalid_utf8(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "binary.bin").write_bytes(b"ok\xffbad")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-3",
            reason="inspect",
            path="binary.bin",
            start_line=1,
            end_line=200,
        )
    )

    assert result == ToolResult.failure(
        "read-3", "BINARY_FILE", "file is not valid UTF-8 text"
    )


@pytest.mark.asyncio
async def test_read_file_handles_empty_and_past_eof_ranges(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "empty.txt").write_text("", encoding="utf-8")
    (workspace / "short.txt").write_text("one\ntwo\n", encoding="utf-8")
    tool = ReadFileTool(boundary)

    empty = await tool.execute(
        ReadFileAction(
            id="read-empty",
            reason="inspect",
            path="empty.txt",
            start_line=1,
            end_line=200,
        )
    )
    past_eof = await tool.execute(
        ReadFileAction(
            id="read-eof",
            reason="inspect",
            path="short.txt",
            start_line=3,
            end_line=5,
        )
    )

    assert empty.success is True and empty.stdout_summary == ""
    assert past_eof.success is True and past_eof.stdout_summary == ""


@pytest.mark.asyncio
async def test_read_file_counts_utf8_bytes_not_characters(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "unicode.txt").write_bytes("命\n".encode("utf-8"))
    result = await ReadFileTool(
        boundary, limits=FilesystemLimits(max_read_bytes=4)
    ).execute(
        ReadFileAction(
            id="read-u",
            reason="inspect",
            path="unicode.txt",
            start_line=1,
            end_line=200,
        )
    )
    assert result.success is True
    assert result.stdout_summary == "命\n"


@pytest.mark.asyncio
async def test_read_file_preserves_crlf_and_bare_cr_newlines(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "newlines.txt").write_bytes(b"one\r\ntwo\rthree\n")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-newlines",
            reason="inspect",
            path="newlines.txt",
            start_line=1,
            end_line=3,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "one\r\ntwo\rthree\n"


@pytest.mark.asyncio
async def test_read_file_counts_original_newline_bytes(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "crlf.txt").write_bytes(b"a\r\n")

    result = await ReadFileTool(
        boundary, limits=FilesystemLimits(max_read_bytes=2)
    ).execute(
        ReadFileAction(
            id="read-crlf-limit",
            reason="inspect",
            path="crlf.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-crlf-limit",
        "FILE_TOO_LARGE",
        "selected file content exceeds the read limit",
    )


@pytest.mark.asyncio
async def test_read_file_reports_missing_directory_and_sensitive_paths(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "directory").mkdir()
    (workspace / ".env").write_text("secret", encoding="utf-8")
    tool = ReadFileTool(boundary)

    missing = await tool.execute(
        ReadFileAction(
            id="read-missing",
            reason="inspect",
            path="missing",
            start_line=1,
            end_line=200,
        )
    )
    directory = await tool.execute(
        ReadFileAction(
            id="read-dir",
            reason="inspect",
            path="directory",
            start_line=1,
            end_line=200,
        )
    )
    sensitive = await tool.execute(
        ReadFileAction(
            id="read-secret",
            reason="inspect",
            path=".env",
            start_line=1,
            end_line=200,
        )
    )

    assert missing.error_type == "NOT_FOUND"
    assert directory.error_type == "NOT_FILE"
    assert sensitive == ToolResult.failure(
        "read-secret", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_escaping_file_symlink(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    outside = workspace.parent / f"{workspace.name}-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-link",
            reason="inspect",
            path="link.txt",
            start_line=1,
            end_line=200,
        )
    )

    assert result == ToolResult.failure(
        "read-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rechecks_target_after_file_validation(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "victim.txt"
    target.write_text("safe", encoding="utf-8")
    outside = workspace.parent / f"{workspace.name}-outside-read.txt"
    outside.write_text("OUTSIDE-SECRET", encoding="utf-8")
    probe = workspace / "probe-read.txt"
    try:
        probe.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    probe.unlink()
    real_is_file = Path.is_file

    def swapping_is_file(path: Path) -> bool:
        outcome = real_is_file(path)
        if path == target:
            path.unlink()
            path.symlink_to(outside)
        return outcome

    monkeypatch.setattr(Path, "is_file", swapping_is_file)
    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-race",
            reason="inspect",
            path="victim.txt",
            start_line=1,
            end_line=200,
        )
    )

    assert result == ToolResult.failure(
        "read-race", "PATH_DENIED", "path access is denied"
    )


def test_read_file_clears_sensitive_traceback_locals(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-name.txt"
    target.write_text("PRIVATE-FILE-CONTENT", encoding="utf-8")
    tool = ReadFileTool(boundary)

    def interrupting_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("PRIVATE-INTERRUPT")

    monkeypatch.setattr(Path, "open", interrupting_open)
    with pytest.raises(KeyboardInterrupt) as error_info:
        tool._execute_sync(
            ReadFileAction(
                id="read-interrupt",
                reason="inspect",
                path="private-name.txt",
                start_line=1,
                end_line=200,
            )
        )

    frame_locals = _filesystem_sync_frame_locals(error_info.value)
    sensitive_names = {
        "action",
        "requested_path",
        "target",
        "verified_target",
        "stream",
        "raw",
        "text",
        "lines",
        "output",
    }
    assert sensitive_names.isdisjoint(frame_locals)


@pytest.mark.asyncio
async def test_read_file_maps_oserror_without_disclosing_details(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-name.txt"
    target.write_text("PRIVATE-FILE-CONTENT", encoding="utf-8")
    real_open = Path.open

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == target:
            raise OSError("PRIVATE-LOW-LEVEL-ERROR")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-io",
            reason="inspect",
            path="private-name.txt",
            start_line=1,
            end_line=200,
        )
    )

    assert result == ToolResult.failure(
        "read-io", "IO_ERROR", "filesystem operation failed"
    )
    rendered = repr(result)
    assert "private-name" not in rendered
    assert "PRIVATE-FILE-CONTENT" not in rendered
    assert "PRIVATE-LOW-LEVEL-ERROR" not in rendered
