from __future__ import annotations

from pathlib import Path
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
async def test_read_file_returns_requested_lines_without_line_numbers(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
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
    (workspace / "large.txt").write_text("ééé\n", encoding="utf-8")
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
    (workspace / "unicode.txt").write_text("命\n", encoding="utf-8")
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
