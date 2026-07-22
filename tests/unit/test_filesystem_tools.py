from __future__ import annotations

import asyncio
import os
import socket
import stat
import tempfile
import threading
from hashlib import sha256
from pathlib import Path
import traceback as traceback_module
from typing import Any, SupportsIndex

import pytest
from pydantic import ValidationError

import safefix.tools.filesystem as filesystem_module
from safefix.domain import (
    AccessKind,
    ApplyPatchAction,
    ListFilesAction,
    ReadFileAction,
    SearchTextAction,
    ToolResult,
)
from safefix.governance.paths import WorkspaceBoundary
from safefix.tools.filesystem import (
    ApplyPatchTool,
    FilesystemLimits,
    ListFilesTool,
    ReadFileTool,
    SearchTextTool,
)


@pytest.mark.asyncio
async def test_patch_rejects_stale_expected_hash(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    action = ApplyPatchAction(
        id="patch-1",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(b"different").hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result == ToolResult.failure(
        "patch-1", "STALE_FILE", "file changed since it was read"
    )
    assert target.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.asyncio
async def test_patch_rejects_replacement_count_mismatch(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\nvalue = 1\n"
    target.write_bytes(original)
    action = ApplyPatchAction(
        id="patch-2",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result.error_type == "PATCH_MISMATCH"
    assert target.read_bytes() == original


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def boundary(workspace: Path) -> WorkspaceBoundary:
    return WorkspaceBoundary(workspace, (".env", "**/*.pem", "**/.ssh/**"))


def _filesystem_frame_locals(
    error: BaseException,
) -> list[tuple[str, dict[str, Any]]]:
    traceback = error.__traceback__
    frames: list[tuple[str, dict[str, Any]]] = []
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == "safefix.tools.filesystem":
            frames.append((frame.f_code.co_name, dict(frame.f_locals)))
        traceback = traceback.tb_next
    assert frames, "filesystem traceback frame was not found"
    return frames


def _assert_filesystem_frames_are_clean(
    error: BaseException,
    sensitive_names: set[str],
    sensitive_fragments: tuple[str, ...],
) -> None:
    for _, frame_locals in _filesystem_frame_locals(error):
        assert sensitive_names.isdisjoint(frame_locals)
        rendered = repr(frame_locals)
        for fragment in sensitive_fragments:
            assert fragment not in rendered


def _assert_public_execute_interrupt_is_clean(
    propagated: BaseException,
    expected: BaseException,
    sensitive_fragments: tuple[str, ...],
) -> None:
    assert propagated is expected
    assert propagated.args == ("EXPECTED-INTERRUPT",)
    assert propagated.__cause__ is None
    assert propagated.__context__ is None
    rendered = "".join(traceback_module.format_exception(propagated))
    for fragment in sensitive_fragments:
        assert fragment not in rendered
    for frame_name, frame_locals in _filesystem_frame_locals(propagated):
        assert frame_locals == {}, f"{frame_name} retained {frame_locals!r}"


def test_filesystem_limits_are_positive() -> None:
    with pytest.raises(ValueError, match="^filesystem limits must be positive$"):
        FilesystemLimits(max_read_bytes=0)


@pytest.mark.parametrize("output_limit", range(1, 11))
def test_filesystem_limits_require_space_for_search_truncation_marker(
    output_limit: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="^search output limit must fit the truncation marker$",
    ):
        FilesystemLimits(max_search_output_bytes=output_limit)


def test_default_filesystem_limits_are_locked() -> None:
    assert FilesystemLimits() == FilesystemLimits(
        max_read_bytes=65_536,
        max_search_files=1_000,
        max_search_output_bytes=65_536,
    )


def test_read_tool_constructor_clears_original_limits_traceback(
    boundary: WorkspaceBoundary,
) -> None:
    class InterruptingLimits:
        def __bool__(self) -> bool:
            raise SystemExit("PRIVATE-LIMITS-INTERRUPT")

        def __repr__(self) -> str:
            return "PRIVATE-LIMITS-SENTINEL"

    limits: Any = InterruptingLimits()
    with pytest.raises(SystemExit) as error_info:
        ReadFileTool(boundary, limits=limits)

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"self", "boundary", "limits"},
        ("PRIVATE-LIMITS-SENTINEL",),
    )


@pytest.mark.parametrize("ignored", [("",), ("../cache",), ("C:/cache",), (r"a\b",)])
def test_ignored_directories_require_safe_relative_posix_paths(
    boundary: WorkspaceBoundary, ignored: tuple[str, ...]
) -> None:
    with pytest.raises(
        ValueError,
        match="^ignored directories must be safe relative POSIX paths$",
    ):
        ListFilesTool(boundary, ignored_directories=ignored)


def test_list_tool_constructor_clears_original_ignored_traceback(
    boundary: WorkspaceBoundary,
) -> None:
    class InterruptingDirectory(str):
        def split(
            self,
            separator: str | None = None,
            maxsplit: SupportsIndex = -1,
        ) -> list[str]:
            raise KeyboardInterrupt("PRIVATE-IGNORED-INTERRUPT")

    with pytest.raises(KeyboardInterrupt) as error_info:
        ListFilesTool(
            boundary,
            ignored_directories=(InterruptingDirectory("PRIVATE-IGNORED-SENTINEL"),),
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"self", "boundary", "ignored_directories", "directories"},
        ("PRIVATE-IGNORED-SENTINEL",),
    )


def test_list_tool_constructor_clears_validation_error_traceback(
    boundary: WorkspaceBoundary,
) -> None:
    with pytest.raises(ValueError) as error_info:
        ListFilesTool(
            boundary,
            ignored_directories=("../PRIVATE-VALIDATION-SENTINEL",),
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"self", "boundary", "ignored_directories", "directories"},
        ("PRIVATE-VALIDATION-SENTINEL",),
    )


def test_ignored_directory_normalization_clears_generated_traceback_frames(
    boundary: WorkspaceBoundary,
) -> None:
    class InterruptingComponent(str):
        def __hash__(self) -> int:
            raise KeyboardInterrupt("PRIVATE-INTERRUPT")

    class InterruptingDirectory(str):
        def split(
            self,
            separator: str | None = None,
            maxsplit: SupportsIndex = -1,
        ) -> list[str]:
            return [InterruptingComponent("PRIVATE-COMPONENT")]

    with pytest.raises(KeyboardInterrupt) as error_info:
        ListFilesTool(
            boundary,
            ignored_directories=(InterruptingDirectory("safe"),),
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"directories", "directory", "components", "component", "path"},
        ("PRIVATE-COMPONENT",),
    )


def test_ignored_directory_matching_clears_generated_traceback_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptingIgnored(str):
        def casefold(self) -> str:
            raise SystemExit("PRIVATE-INTERRUPT")

    monkeypatch.setattr(filesystem_module, "_CASE_INSENSITIVE_PATHS", True)
    with pytest.raises(SystemExit) as error_info:
        filesystem_module._is_ignored_directory(
            ".", (InterruptingIgnored("PRIVATE-IGNORED"),)
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"relative", "ignored", "entry", "match_relative", "match_ignored"},
        ("PRIVATE-IGNORED",),
    )


@pytest.mark.asyncio
async def test_list_files_is_sorted_bounded_and_marks_truncation(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "z.py").write_text("z", encoding="utf-8")
    (workspace / "a.py").write_text("a", encoding="utf-8")
    (workspace / "m.txt").write_text("m", encoding="utf-8")
    tool = ListFilesTool(boundary)
    action = ListFilesAction(id="list-1", reason="inspect", pattern="**/*.py", limit=1)

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

    result = await ListFilesTool(boundary, ignored_directories=ignored).execute(
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

    result = await ListFilesTool(boundary, ignored_directories=ignored).execute(
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
        ListFilesAction(id="list-missing", reason="inspect", path="missing", limit=100)
    )
    non_directory = await tool.execute(
        ListFilesAction(id="list-file", reason="inspect", path="plain.txt", limit=100)
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
    (workspace / "子目录" / ".git" / "secret.py").write_text("hidden", encoding="utf-8")

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
async def test_list_files_skips_file_symlinks(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real.txt"
    target.write_text("visible", encoding="utf-8")
    link = workspace / "alias.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-file-link", reason="inspect", limit=100)
    )

    assert result.success is True
    assert result.stdout_summary == "real.txt"


@pytest.mark.asyncio
async def test_list_files_skips_fifo(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    fifo = workspace / "pipe"
    try:
        os.mkfifo(fifo)
    except OSError:
        pytest.skip("FIFOs are unavailable")
    (workspace / "regular.txt").write_text("visible", encoding="utf-8")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-fifo", reason="inspect", limit=100)
    )

    assert result.success is True
    assert result.stdout_summary == "regular.txt"


@pytest.mark.asyncio
async def test_list_files_skips_unix_socket(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("Unix sockets are unavailable")
    socket_path = workspace / "service.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
    except OSError:
        listener.close()
        pytest.skip("Unix filesystem sockets are unavailable")
    try:
        (workspace / "regular.txt").write_text("visible", encoding="utf-8")

        result = await ListFilesTool(boundary).execute(
            ListFilesAction(id="list-socket", reason="inspect", limit=100)
        )
    finally:
        listener.close()

    assert result.success is True
    assert result.stdout_summary == "regular.txt"


@pytest.mark.asyncio
async def test_list_files_rechecks_lexical_file_type_after_resolution(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement = workspace / "replacement.txt"
    replacement.write_text("replacement", encoding="utf-8")
    victim = workspace / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    real_stat = Path.stat
    no_follow_checks = 0

    def swapping_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        nonlocal no_follow_checks
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if path == victim and not follow_symlinks:
            no_follow_checks += 1
            if no_follow_checks == 1:
                victim.unlink()
                victim.symlink_to(replacement)
        return result

    monkeypatch.setattr(Path, "stat", swapping_stat)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-file-type-race", reason="inspect", limit=100)
    )

    assert result.success is True
    assert result.stdout_summary == "replacement.txt"
    assert no_follow_checks == 2


@pytest.mark.asyncio
async def test_list_files_silently_skips_no_follow_stat_oserror(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden = workspace / "hidden.txt"
    hidden.write_text("hidden", encoding="utf-8")
    (workspace / "visible.txt").write_text("visible", encoding="utf-8")
    real_stat = Path.stat

    def faulting_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == hidden and not follow_symlinks:
            raise OSError("PRIVATE-LSTAT-ERROR")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", faulting_stat)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-lstat-oserror", reason="inspect", limit=100)
    )

    assert result.success is True
    assert result.stdout_summary == "visible.txt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt("EXPECTED-INTERRUPT"), SystemExit("EXPECTED-INTERRUPT")],
)
async def test_list_files_no_follow_stat_process_control_is_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt: BaseException,
) -> None:
    workspace = tmp_path / "PRIVATE-LSTAT-ROOT-SENTINEL"
    workspace.mkdir()
    target = workspace / "PRIVATE-LSTAT-FILE-SENTINEL.txt"
    target.write_text("private", encoding="utf-8")
    tool = ListFilesTool(WorkspaceBoundary(workspace, ()))
    real_stat = Path.stat

    def interrupting_stat(
        path: Path, *, follow_symlinks: bool = True
    ) -> os.stat_result:
        if path == target and not follow_symlinks:
            raise interrupt
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", interrupting_stat)
    with pytest.raises(type(interrupt)) as error_info:
        await tool.execute(
            ListFilesAction(id="PRIVATE-LSTAT-ID-SENTINEL", reason="inspect", limit=100)
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "PRIVATE-LSTAT-ROOT-SENTINEL",
            "PRIVATE-LSTAT-FILE-SENTINEL",
            "PRIVATE-LSTAT-ID-SENTINEL",
        ),
    )


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


@pytest.mark.asyncio
async def test_list_files_rejects_junction_root(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    (junction / "secret.txt").write_text("secret", encoding="utf-8")
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-junction-root",
            reason="inspect",
            path="junction",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-junction-root", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_skips_junction_descendants(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "real"
    target.mkdir()
    (target / "ok.txt").write_text("ok", encoding="utf-8")
    junction = workspace / "junction"
    junction.mkdir()
    (junction / "hidden.txt").write_text("hidden", encoding="utf-8")
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-junction", reason="inspect", limit=100)
    )

    assert result.success is True
    assert result.stdout_summary == "real/ok.txt"


@pytest.mark.asyncio
async def test_list_files_rejects_ancestor_directory_symlink(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real"
    nested = target / "subdir"
    nested.mkdir(parents=True)
    (nested / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-ancestor-link",
            reason="inspect",
            path="linked/subdir",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-ancestor-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_simulated_ancestor_junction(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    nested = junction / "subdir"
    nested.mkdir(parents=True)
    (nested / "secret.txt").write_text("secret", encoding="utf-8")
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-ancestor-junction",
            reason="inspect",
            path="junction/subdir",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-ancestor-junction", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_symlink_before_dotdot(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real" / "nested"
    target.mkdir(parents=True)
    (workspace / "real" / "ok.txt").write_text("ok", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-link-dotdot",
            reason="inspect",
            path="linked/..",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-link-dotdot", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_simulated_junction_before_dotdot(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-junction-dotdot",
            reason="inspect",
            path="junction/..",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-junction-dotdot", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_symlink_after_missing_prefix_dotdot(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    nested = workspace / "real" / "nested"
    nested.mkdir(parents=True)
    (nested / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(workspace / "real", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-missing-link",
            reason="inspect",
            path="missing/../linked/nested",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-missing-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_junction_after_missing_prefix_dotdot(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested = workspace / "junction" / "nested"
    nested.mkdir(parents=True)
    (nested / "secret.txt").write_text("secret", encoding="utf-8")
    junction = workspace / "junction"
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-missing-junction",
            reason="inspect",
            path="missing/../junction/nested",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-missing-junction", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_alias_descendant_link_after_missing_dotdot(
    workspace: Path,
) -> None:
    real_workspace = workspace / "real-workspace"
    nested = real_workspace / "target" / "nested"
    nested.mkdir(parents=True)
    (nested / "secret.txt").write_text("secret", encoding="utf-8")
    alias = workspace / "workspace-alias"
    linked = real_workspace / "linked"
    try:
        alias.symlink_to(real_workspace, target_is_directory=True)
        linked.symlink_to(real_workspace / "target", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    alias_boundary = WorkspaceBoundary(alias, ())

    result = await ListFilesTool(alias_boundary).execute(
        ListFilesAction(
            id="list-alias-missing-link",
            reason="inspect",
            path=str(alias / "missing" / ".." / "linked" / "nested"),
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-alias-missing-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path_template",
    ("../{workspace_name}/linked", "missing/../../{workspace_name}/linked"),
)
async def test_list_files_rejects_parent_references_that_reenter_workspace(
    workspace: Path,
    boundary: WorkspaceBoundary,
    path_template: str,
) -> None:
    target = workspace / "real"
    target.mkdir()
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    requested_path = path_template.format(workspace_name=workspace.name)

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-parent-reentry",
            reason="inspect",
            path=requested_path,
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-parent-reentry", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_parent_reentry_to_simulated_junction(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    (junction / "secret.txt").write_text("secret", encoding="utf-8")
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-parent-junction",
            reason="inspect",
            path=f"../{workspace.name}/junction",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-parent-junction", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_parent_reference_under_configured_alias(
    workspace: Path,
) -> None:
    real_workspace = workspace / "real-workspace"
    target = real_workspace / "target"
    target.mkdir(parents=True)
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    alias = workspace / "workspace-alias"
    linked = real_workspace / "linked"
    try:
        alias.symlink_to(real_workspace, target_is_directory=True)
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    alias_boundary = WorkspaceBoundary(alias, ())

    result = await ListFilesTool(alias_boundary).execute(
        ListFilesAction(
            id="list-alias-parent",
            reason="inspect",
            path=str(alias / ".." / alias.name / "linked"),
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-alias-parent", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_rejects_ordinary_parent_reference(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "b"
    target.mkdir()
    (target / "ok.txt").write_text("ok", encoding="utf-8")

    result = await ListFilesTool(boundary).execute(
        ListFilesAction(
            id="list-ordinary-parent",
            reason="inspect",
            path="a/../b",
            limit=100,
        )
    )

    assert result == ToolResult.failure(
        "list-ordinary-parent", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_list_files_supports_configured_workspace_alias_absolute_path(
    workspace: Path,
) -> None:
    real_workspace = workspace / "real-workspace"
    subdir = real_workspace / "subdir"
    subdir.mkdir(parents=True)
    (subdir / "ok.txt").write_text("ok", encoding="utf-8")
    alias = workspace / "workspace-alias"
    try:
        alias.symlink_to(real_workspace, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    alias_boundary = WorkspaceBoundary(alias, ())

    result = await ListFilesTool(alias_boundary).execute(
        ListFilesAction(
            id="list-alias",
            reason="inspect",
            path=str(alias / "subdir"),
            limit=100,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "subdir/ok.txt"


def test_list_files_canonical_branch_does_not_chain_sensitive_context(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_workspace = workspace / "real-workspace"
    target = real_workspace / "subdir"
    target.mkdir(parents=True)
    alias = workspace / "PRIVATE-CONFIGURED-SENTINEL"
    try:
        alias.symlink_to(real_workspace, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    tool = ListFilesTool(WorkspaceBoundary(alias, ()))
    real_relative_to = Path.relative_to
    interrupt = KeyboardInterrupt("EXPECTED-INTERRUPT")

    def interrupting_relative_to(path: Path, *other: Any, **kwargs: Any) -> Path:
        if path == target and other == (real_workspace,):
            raise interrupt
        return real_relative_to(path, *other, **kwargs)

    monkeypatch.setattr(Path, "relative_to", interrupting_relative_to)
    with pytest.raises(KeyboardInterrupt) as error_info:
        tool._execute_sync(
            ListFilesAction(
                id="list-context",
                reason="inspect",
                path=str(target),
                limit=100,
            )
        )

    propagated = error_info.value
    assert propagated is interrupt
    assert propagated.__cause__ is None
    assert propagated.__context__ is None
    rendered = "".join(traceback_module.format_exception(propagated))
    assert "PRIVATE-CONFIGURED-SENTINEL" not in rendered
    _assert_filesystem_frames_are_clean(
        propagated,
        {
            "configured_workspace",
            "workspace",
            "target",
            "base",
            "relative",
            "current",
            "components",
            "component",
        },
        ("PRIVATE-CONFIGURED-SENTINEL",),
    )


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
    real_resolve = boundary.resolve
    real_is_dir = Path.is_dir
    root_resolve_calls = 0
    first_resolve_completed = False
    swapped = False

    def recording_resolve(candidate: str, access: Any) -> Path:
        nonlocal first_resolve_completed, root_resolve_calls
        if candidate == "victim":
            root_resolve_calls += 1
        outcome = real_resolve(candidate, access)
        if candidate == "victim" and root_resolve_calls == 1:
            first_resolve_completed = True
        return outcome

    def swapping_is_dir(path: Path) -> bool:
        nonlocal swapped
        outcome = real_is_dir(path)
        if path == root and first_resolve_completed and not swapped:
            root.rmdir()
            root.symlink_to(outside, target_is_directory=True)
            swapped = True
        return outcome

    monkeypatch.setattr(boundary, "resolve", recording_resolve)
    monkeypatch.setattr(Path, "is_dir", swapping_is_dir)
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-race", reason="inspect", path="victim", limit=100)
    )

    assert result == ToolResult.failure(
        "list-race", "PATH_DENIED", "path access is denied"
    )
    assert root_resolve_calls == 2


@pytest.mark.asyncio
async def test_list_public_execute_clears_all_traceback_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "PRIVATE-LIST-ROOT-SENTINEL"
    workspace.mkdir()
    tool = ListFilesTool(WorkspaceBoundary(workspace, ()))
    interrupt = KeyboardInterrupt("EXPECTED-INTERRUPT")

    async def interrupting_to_thread(function: Any, *args: Any) -> Any:
        raise interrupt

    monkeypatch.setattr(filesystem_module.asyncio, "to_thread", interrupting_to_thread)
    with pytest.raises(KeyboardInterrupt) as error_info:
        await tool.execute(
            ListFilesAction(
                id="PRIVATE-LIST-ID-SENTINEL",
                reason="inspect",
                path="PRIVATE-LIST-PATH-SENTINEL",
                limit=100,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "PRIVATE-LIST-ROOT-SENTINEL",
            "PRIVATE-LIST-ID-SENTINEL",
            "PRIVATE-LIST-PATH-SENTINEL",
        ),
    )


@pytest.mark.asyncio
async def test_list_public_execute_cleans_wrong_action_error_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "PRIVATE-LIST-WRONG-ROOT-SENTINEL"
    workspace.mkdir()
    tool = ListFilesTool(WorkspaceBoundary(workspace, ()))
    interrupt = SystemExit("EXPECTED-INTERRUPT")

    class InterruptingToolResult:
        @classmethod
        def failure(cls, action_id: str, error_type: str, message: str) -> Any:
            raise interrupt

    monkeypatch.setattr(filesystem_module, "ToolResult", InterruptingToolResult)
    with pytest.raises(SystemExit) as error_info:
        await tool.execute(
            ReadFileAction(
                id="PRIVATE-LIST-WRONG-ID-SENTINEL",
                reason="inspect",
                path="PRIVATE-LIST-WRONG-PATH-SENTINEL",
                start_line=1,
                end_line=1,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "PRIVATE-LIST-WRONG-ROOT-SENTINEL",
            "PRIVATE-LIST-WRONG-ID-SENTINEL",
            "PRIVATE-LIST-WRONG-PATH-SENTINEL",
        ),
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
    _assert_filesystem_frames_are_clean(
        error_info.value, sensitive_names, ("private-name",)
    )


def test_list_files_clears_relative_path_helper_traceback(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-relative-name.txt"
    target.write_text("secret", encoding="utf-8")
    tool = ListFilesTool(boundary)
    real_relative_to = Path.relative_to

    def interrupting_relative_to(path: Path, *other: Any, **kwargs: Any) -> Path:
        if path == target:
            raise SystemExit("PRIVATE-INTERRUPT")
        return real_relative_to(path, *other, **kwargs)

    monkeypatch.setattr(Path, "relative_to", interrupting_relative_to)
    with pytest.raises(SystemExit) as error_info:
        tool._execute_sync(
            ListFilesAction(id="list-relative", reason="inspect", limit=100)
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"root", "target"},
        ("private-relative-name",),
    )


def test_list_files_clears_link_helper_tracebacks(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-link-name"
    target.mkdir()
    tool = ListFilesTool(boundary)
    real_is_symlink = Path.is_symlink

    def interrupting_is_symlink(path: Path) -> bool:
        if path == target:
            raise KeyboardInterrupt("PRIVATE-INTERRUPT")
        return real_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", interrupting_is_symlink)
    with pytest.raises(KeyboardInterrupt) as error_info:
        tool._execute_sync(
            ListFilesAction(
                id="list-link-interrupt",
                reason="inspect",
                path="private-link-name",
                limit=100,
            )
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"workspace", "target", "current", "component", "path"},
        ("private-link-name",),
    )


def test_list_files_clears_pattern_helper_traceback(
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupting_from_lines(pattern_factory: str, lines: Any) -> Any:
        raise SystemExit("PRIVATE-INTERRUPT")

    monkeypatch.setattr(
        filesystem_module.pathspec.PathSpec,
        "from_lines",
        interrupting_from_lines,
    )
    with pytest.raises(SystemExit) as error_info:
        ListFilesTool(boundary)._execute_sync(
            ListFilesAction(
                id="list-pattern-interrupt",
                reason="inspect",
                pattern="PRIVATE-PATTERN",
                limit=100,
            )
        )

    _assert_filesystem_frames_are_clean(
        error_info.value, {"pattern"}, ("PRIVATE-PATTERN",)
    )


def test_list_files_clears_lexical_path_helper_traceback(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-lexical-name"
    target.mkdir()
    tool = ListFilesTool(boundary)
    real_is_absolute = Path.is_absolute
    calls = 0

    def interrupting_is_absolute(path: Path) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("PRIVATE-INTERRUPT")
        return real_is_absolute(path)

    monkeypatch.setattr(Path, "is_absolute", interrupting_is_absolute)
    with pytest.raises(KeyboardInterrupt) as error_info:
        tool._execute_sync(
            ListFilesAction(
                id="list-lexical-interrupt",
                reason="inspect",
                path="private-lexical-name",
                limit=100,
            )
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"workspace", "requested_path", "candidate"},
        ("private-lexical-name",),
    )


def test_list_files_clears_ignored_path_helper_traceback(
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = ListFilesTool(boundary)

    def interrupting_posix_path(candidate: str) -> Any:
        raise SystemExit("PRIVATE-INTERRUPT")

    monkeypatch.setattr(filesystem_module, "PurePosixPath", interrupting_posix_path)
    with pytest.raises(SystemExit) as error_info:
        tool._execute_sync(
            ListFilesAction(id="list-ignored-interrupt", reason="inspect", limit=100)
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"relative", "ignored", "match_relative", "match_ignored", "parts"},
        (".git",),
    )


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
async def test_read_file_rejects_inside_directory_symlink_ancestor(
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

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-inside-link",
            reason="inspect",
            path="linked/secret.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-inside-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_simulated_junction_ancestor(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    (junction / "secret.txt").write_text("secret", encoding="utf-8")
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-junction",
            reason="inspect",
            path="junction/secret.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-junction", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_directory_symlink_before_dotdot(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    nested = workspace / "real" / "nested"
    nested.mkdir(parents=True)
    (workspace / "real" / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(nested, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-link-dotdot",
            reason="inspect",
            path="linked/../secret.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-link-dotdot", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_trusts_alias_root_but_rejects_descendant_link(
    workspace: Path,
) -> None:
    real_workspace = workspace / "real-workspace"
    target = real_workspace / "target"
    target.mkdir(parents=True)
    (real_workspace / "ok.txt").write_text("ok", encoding="utf-8")
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    alias = workspace / "workspace-alias"
    linked = real_workspace / "linked"
    try:
        alias.symlink_to(real_workspace, target_is_directory=True)
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    tool = ReadFileTool(WorkspaceBoundary(alias, ()))

    safe = await tool.execute(
        ReadFileAction(
            id="read-alias-safe",
            reason="inspect",
            path=str(alias / "ok.txt"),
            start_line=1,
            end_line=1,
        )
    )
    linked_result = await tool.execute(
        ReadFileAction(
            id="read-alias-linked",
            reason="inspect",
            path=str(alias / "linked" / "secret.txt"),
            start_line=1,
            end_line=1,
        )
    )

    assert safe.success is True and safe.stdout_summary == "ok"
    assert linked_result == ToolResult.failure(
        "read-alias-linked", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rechecks_directory_links_before_open(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    victim = workspace / "victim"
    victim.mkdir()
    (victim / "content.txt").write_text("safe", encoding="utf-8")
    replacement = workspace / "replacement"
    replacement.mkdir()
    (replacement / "content.txt").write_text("secret", encoding="utf-8")
    probe = workspace / "probe-read-directory"
    try:
        probe.symlink_to(replacement, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    probe.unlink()
    real_resolve = boundary.resolve
    resolve_calls = 0

    def swapping_second_resolve(candidate: str, access: Any) -> Path:
        nonlocal resolve_calls
        outcome = real_resolve(candidate, access)
        if candidate == "victim/content.txt":
            resolve_calls += 1
            if resolve_calls == 2:
                (victim / "content.txt").unlink()
                victim.rmdir()
                victim.symlink_to(replacement, target_is_directory=True)
        return outcome

    monkeypatch.setattr(boundary, "resolve", swapping_second_resolve)
    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-directory-race",
            reason="inspect",
            path="victim/content.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-directory-race", "PATH_DENIED", "path access is denied"
    )
    assert resolve_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path_template",
    (
        "../{workspace_name}/linked/secret.txt",
        "missing/../../{workspace_name}/linked/secret.txt",
    ),
)
async def test_read_file_rejects_parent_references_that_reenter_workspace(
    workspace: Path,
    boundary: WorkspaceBoundary,
    path_template: str,
) -> None:
    target = workspace / "real"
    target.mkdir()
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    requested_path = path_template.format(workspace_name=workspace.name)

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-parent-reentry",
            reason="inspect",
            path=requested_path,
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-parent-reentry", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_parent_reentry_to_simulated_junction(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    (junction / "secret.txt").write_text("secret", encoding="utf-8")
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-parent-junction",
            reason="inspect",
            path=f"../{workspace.name}/junction/secret.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-parent-junction", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_parent_reference_under_configured_alias(
    workspace: Path,
) -> None:
    real_workspace = workspace / "real-workspace"
    target = real_workspace / "target"
    target.mkdir(parents=True)
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    alias = workspace / "workspace-alias"
    linked = real_workspace / "linked"
    try:
        alias.symlink_to(real_workspace, target_is_directory=True)
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    tool = ReadFileTool(WorkspaceBoundary(alias, ()))

    result = await tool.execute(
        ReadFileAction(
            id="read-alias-parent",
            reason="inspect",
            path=str(alias / ".." / alias.name / "linked" / "secret.txt"),
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-alias-parent", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_ordinary_parent_reference(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "b"
    target.mkdir()
    (target / "ok.txt").write_text("ok", encoding="utf-8")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-ordinary-parent",
            reason="inspect",
            path="a/../b/ok.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-ordinary-parent", "PATH_DENIED", "path access is denied"
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


@pytest.mark.asyncio
async def test_read_public_execute_clears_all_traceback_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "PRIVATE-READ-ROOT-SENTINEL"
    workspace.mkdir()
    tool = ReadFileTool(WorkspaceBoundary(workspace, ()))
    interrupt = SystemExit("EXPECTED-INTERRUPT")

    async def interrupting_to_thread(function: Any, *args: Any) -> Any:
        raise interrupt

    monkeypatch.setattr(filesystem_module.asyncio, "to_thread", interrupting_to_thread)
    with pytest.raises(SystemExit) as error_info:
        await tool.execute(
            ReadFileAction(
                id="PRIVATE-READ-ID-SENTINEL",
                reason="inspect",
                path="PRIVATE-READ-PATH-SENTINEL",
                start_line=1,
                end_line=1,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "PRIVATE-READ-ROOT-SENTINEL",
            "PRIVATE-READ-ID-SENTINEL",
            "PRIVATE-READ-PATH-SENTINEL",
        ),
    )


@pytest.mark.asyncio
async def test_read_public_execute_cleans_wrong_action_error_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "PRIVATE-READ-WRONG-ROOT-SENTINEL"
    workspace.mkdir()
    tool = ReadFileTool(WorkspaceBoundary(workspace, ()))
    interrupt = KeyboardInterrupt("EXPECTED-INTERRUPT")

    class InterruptingToolResult:
        @classmethod
        def failure(cls, action_id: str, error_type: str, message: str) -> Any:
            raise interrupt

    monkeypatch.setattr(filesystem_module, "ToolResult", InterruptingToolResult)
    with pytest.raises(KeyboardInterrupt) as error_info:
        await tool.execute(
            ListFilesAction(
                id="PRIVATE-READ-WRONG-ID-SENTINEL",
                reason="inspect",
                path="PRIVATE-READ-WRONG-PATH-SENTINEL",
                limit=100,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "PRIVATE-READ-WRONG-ROOT-SENTINEL",
            "PRIVATE-READ-WRONG-ID-SENTINEL",
            "PRIVATE-READ-WRONG-PATH-SENTINEL",
        ),
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
    _assert_filesystem_frames_are_clean(
        error_info.value, sensitive_names, ("private-name", "PRIVATE-FILE-CONTENT")
    )


def test_read_file_clears_success_helper_traceback(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (workspace / "private-output.txt").write_bytes(b"PRIVATE-OUTPUT-CONTENT")
    tool = ReadFileTool(boundary)
    real_monotonic_ns = filesystem_module.time.monotonic_ns
    calls = 0

    def interrupting_monotonic_ns() -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("PRIVATE-INTERRUPT")
        return real_monotonic_ns()

    monkeypatch.setattr(
        filesystem_module.time, "monotonic_ns", interrupting_monotonic_ns
    )
    with pytest.raises(KeyboardInterrupt) as error_info:
        tool._execute_sync(
            ReadFileAction(
                id="read-success-interrupt",
                reason="inspect",
                path="private-output.txt",
                start_line=1,
                end_line=200,
            )
        )

    _assert_filesystem_frames_are_clean(
        error_info.value,
        {"output", "started_ns"},
        ("PRIVATE-OUTPUT-CONTENT",),
    )


def test_read_file_io_mapping_does_not_chain_sensitive_context(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-io.txt"
    target.write_text("content", encoding="utf-8")
    interrupt = SystemExit("EXPECTED-INTERRUPT")

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        raise OSError("PRIVATE-IO-CONTEXT-SENTINEL")

    def interrupting_io_failure(action_id: str) -> ToolResult:
        raise interrupt

    monkeypatch.setattr(Path, "open", failing_open)
    monkeypatch.setattr(filesystem_module, "_io_failure", interrupting_io_failure)
    with pytest.raises(SystemExit) as error_info:
        ReadFileTool(boundary)._execute_sync(
            ReadFileAction(
                id="read-io-context",
                reason="inspect",
                path="private-io.txt",
                start_line=1,
                end_line=200,
            )
        )

    propagated = error_info.value
    assert propagated is interrupt
    assert propagated.__cause__ is None
    assert propagated.__context__ is None
    rendered = "".join(traceback_module.format_exception(propagated))
    assert "PRIVATE-IO-CONTEXT-SENTINEL" not in rendered
    _assert_filesystem_frames_are_clean(
        propagated,
        {"requested_path", "target", "verified_target", "raw", "text"},
        ("PRIVATE-IO-CONTEXT-SENTINEL", "private-io.txt"),
    )


def test_read_file_decode_mapping_does_not_chain_content_context(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (workspace / "binary.bin").write_bytes(b"PRIVATE-DECODE-CONTENT\xff")
    interrupt = KeyboardInterrupt("EXPECTED-INTERRUPT")

    class InterruptingToolResult:
        @classmethod
        def failure(cls, action_id: str, error_type: str, message: str) -> Any:
            raise interrupt

    monkeypatch.setattr(filesystem_module, "ToolResult", InterruptingToolResult)
    with pytest.raises(KeyboardInterrupt) as error_info:
        ReadFileTool(boundary)._execute_sync(
            ReadFileAction(
                id="read-decode-context",
                reason="inspect",
                path="binary.bin",
                start_line=1,
                end_line=200,
            )
        )

    propagated = error_info.value
    assert propagated is interrupt
    assert propagated.__cause__ is None
    assert propagated.__context__ is None
    rendered = "".join(traceback_module.format_exception(propagated))
    assert "PRIVATE-DECODE-CONTENT" not in rendered
    _assert_filesystem_frames_are_clean(
        propagated,
        {"requested_path", "target", "verified_target", "raw", "text"},
        ("PRIVATE-DECODE-CONTENT", "binary.bin"),
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


@pytest.mark.asyncio
async def test_search_text_uses_literal_pattern_and_stable_format(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "b.py").write_text("x = 'a.b'\n", encoding="utf-8")
    (workspace / "a.py").write_text("a.b\naxb\n", encoding="utf-8")
    action = SearchTextAction(
        id="search-1",
        reason="find literal",
        pattern="a.b",
        file_glob="**/*.py",
        max_results=10,
    )

    result = await SearchTextTool(boundary).execute(action)

    assert result.success is True
    assert result.stdout_summary == "a.py:1:a.b\nb.py:1:x = 'a.b'"


@pytest.mark.asyncio
async def test_search_text_marks_result_limit_truncation(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "a.txt").write_text("hit\nhit\n", encoding="utf-8")
    action = SearchTextAction(
        id="search-2", reason="find", pattern="hit", max_results=1
    )

    result = await SearchTextTool(boundary).execute(action)

    assert result.success is True
    assert result.stdout_summary == "a.txt:1:hit\n[truncated]"


@pytest.mark.asyncio
async def test_search_text_output_limit_never_splits_unicode(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "u.txt").write_text("命中内容\n", encoding="utf-8")
    limits = FilesystemLimits(max_search_output_bytes=12)
    action = SearchTextAction(
        id="search-3", reason="find", pattern="命", max_results=10
    )

    result = await SearchTextTool(boundary, limits=limits).execute(action)

    assert result.success is True
    assert result.stdout_summary == "[truncated]"
    result.stdout_summary.encode("utf-8")


@pytest.mark.asyncio
async def test_search_text_output_limit_accepts_exact_truncation_marker_size(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "a.txt").write_text("hit!\n", encoding="utf-8")

    result = await SearchTextTool(
        boundary, limits=FilesystemLimits(max_search_output_bytes=11)
    ).execute(
        SearchTextAction(
            id="search-marker-budget",
            reason="find",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "[truncated]"
    assert len(result.stdout_summary.encode("utf-8")) == 11


@pytest.mark.asyncio
async def test_search_text_removes_results_to_make_room_for_truncation_marker(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "a.txt").write_text("hit\nhit\n", encoding="utf-8")

    result = await SearchTextTool(
        boundary, limits=FilesystemLimits(max_search_output_bytes=22)
    ).execute(
        SearchTextAction(
            id="search-marker-room",
            reason="find",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "[truncated]"
    assert len(result.stdout_summary.encode("utf-8")) <= 22


@pytest.mark.asyncio
async def test_search_text_enforces_file_scan_limit(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "a.txt").write_text("hit\n", encoding="utf-8")
    (workspace / "b.txt").write_text("hit\n", encoding="utf-8")
    limits = FilesystemLimits(max_search_files=1)

    result = await SearchTextTool(boundary, limits=limits).execute(
        SearchTextAction(
            id="search-files", reason="find", pattern="hit", max_results=50
        )
    )

    assert result.success is True
    assert result.stdout_summary == "a.txt:1:hit\n[truncated]"


@pytest.mark.asyncio
async def test_search_text_filters_glob_and_rejects_invalid_glob(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "a.py").write_text("hit\n", encoding="utf-8")
    (workspace / "a.txt").write_text("hit\n", encoding="utf-8")
    tool = SearchTextTool(boundary)

    filtered = await tool.execute(
        SearchTextAction(
            id="search-glob",
            reason="find",
            pattern="hit",
            file_glob="**/*.py",
            max_results=50,
        )
    )
    invalid = await tool.execute(
        SearchTextAction(
            id="search-invalid",
            reason="find",
            pattern="hit",
            file_glob="!",
            max_results=50,
        )
    )

    assert filtered.stdout_summary == "a.py:1:hit"
    assert invalid == ToolResult.failure(
        "search-invalid", "INVALID_GLOB", "file pattern is invalid"
    )


@pytest.mark.asyncio
async def test_search_text_skips_ignored_sensitive_and_binary_descendants(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "build").mkdir()
    (workspace / "build" / "ignored.txt").write_text("hit", encoding="utf-8")
    (workspace / ".env").write_text("hit-secret", encoding="utf-8")
    (workspace / "binary.bin").write_bytes(b"hit\xff")
    (workspace / "ok.txt").write_text("hit\n", encoding="utf-8")

    result = await SearchTextTool(boundary, ignored_directories=("build",)).execute(
        SearchTextAction(id="search-skip", reason="find", pattern="hit", max_results=50)
    )

    assert result.success is True
    assert result.stdout_summary == "ok.txt:1:hit"


@pytest.mark.asyncio
async def test_search_text_direct_binary_and_sensitive_paths_fail(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "binary.bin").write_bytes(b"hit\xff")
    (workspace / ".env").write_text("hit", encoding="utf-8")
    tool = SearchTextTool(boundary)

    binary = await tool.execute(
        SearchTextAction(
            id="search-binary",
            reason="find",
            path="binary.bin",
            pattern="hit",
            max_results=50,
        )
    )
    sensitive = await tool.execute(
        SearchTextAction(
            id="search-secret",
            reason="find",
            path=".env",
            pattern="hit",
            max_results=50,
        )
    )

    assert binary.error_type == "BINARY_FILE"
    assert sensitive.error_type == "PATH_DENIED"


@pytest.mark.asyncio
async def test_search_text_unicode_empty_and_directory_symlink(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "中文.txt").write_text("没有匹配\n", encoding="utf-8")
    outside = workspace.parent / f"{workspace.name}-search-outside"
    outside.mkdir()
    (outside / "escaped.txt").write_text("needle", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-empty", reason="find", pattern="needle", max_results=50
        )
    )

    assert result.success is True
    assert result.stdout_summary == ""


@pytest.mark.asyncio
async def test_search_text_maps_oserror_without_disclosing_details(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-search.txt"
    target.write_text("PRIVATE-SEARCH-CONTENT", encoding="utf-8")
    real_open = Path.open

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == target:
            raise OSError("PRIVATE-SEARCH-ERROR")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-io",
            reason="find",
            path="private-search.txt",
            pattern="PRIVATE-SEARCH-PATTERN",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-io", "IO_ERROR", "filesystem operation failed"
    )
    rendered = repr(result)
    for sentinel in (
        "private-search",
        "PRIVATE-SEARCH-CONTENT",
        "PRIVATE-SEARCH-ERROR",
        "PRIVATE-SEARCH-PATTERN",
    ):
        assert sentinel not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sensitive_name", "valid_name"),
    ((".env", "ok.txt"), ("z.pem", "a.txt")),
)
async def test_search_text_sensitive_candidates_do_not_consume_scan_limit(
    workspace: Path,
    boundary: WorkspaceBoundary,
    sensitive_name: str,
    valid_name: str,
) -> None:
    (workspace / sensitive_name).write_text("hit-secret\n", encoding="utf-8")
    (workspace / valid_name).write_text("hit\n", encoding="utf-8")

    result = await SearchTextTool(
        boundary, limits=FilesystemLimits(max_search_files=1)
    ).execute(
        SearchTextAction(
            id="search-sensitive-quota",
            reason="find",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == f"{valid_name}:1:hit"


@pytest.mark.asyncio
async def test_search_text_disappeared_candidate_does_not_consume_scan_limit(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disappeared = workspace / "a.txt"
    disappeared.write_text("hit\n", encoding="utf-8")
    (workspace / "b.txt").write_text("hit\n", encoding="utf-8")
    real_exists = Path.exists

    def simulated_exists(path: Path) -> bool:
        if path == disappeared:
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", simulated_exists)
    result = await SearchTextTool(
        boundary, limits=FilesystemLimits(max_search_files=1)
    ).execute(
        SearchTextAction(
            id="search-disappeared-quota",
            reason="find",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "b.txt:1:hit"


@pytest.mark.asyncio
async def test_search_text_non_file_candidate_does_not_consume_scan_limit(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_file = workspace / "a.txt"
    non_file.write_text("hit\n", encoding="utf-8")
    (workspace / "b.txt").write_text("hit\n", encoding="utf-8")
    real_is_file = Path.is_file

    def simulated_is_file(path: Path) -> bool:
        if path == non_file:
            return False
        return real_is_file(path)

    monkeypatch.setattr(Path, "is_file", simulated_is_file)
    result = await SearchTextTool(
        boundary, limits=FilesystemLimits(max_search_files=1)
    ).execute(
        SearchTextAction(
            id="search-non-file-quota",
            reason="find",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "b.txt:1:hit"


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_path", ("a/../b", r"a\..\b"))
async def test_search_text_rejects_original_parent_references(
    workspace: Path,
    boundary: WorkspaceBoundary,
    requested_path: str,
) -> None:
    target = workspace / "b"
    target.mkdir()
    (target / "match.txt").write_text("hit\n", encoding="utf-8")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-parent",
            reason="find",
            path=requested_path,
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-parent", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_rejects_direct_junction_root(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    (junction / "match.txt").write_text("hit\n", encoding="utf-8")
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-junction",
            reason="find",
            path="junction",
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-junction", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_rejects_directory_symlink_ancestor(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    nested = workspace / "real" / "nested"
    nested.mkdir(parents=True)
    (nested / "match.txt").write_text("hit\n", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(workspace / "real", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-link-ancestor",
            reason="find",
            path="linked/nested",
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-link-ancestor", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_rejects_link_descendant_under_configured_alias(
    workspace: Path,
) -> None:
    real_workspace = workspace / "real-workspace"
    target = real_workspace / "target"
    target.mkdir(parents=True)
    (target / "match.txt").write_text("hit\n", encoding="utf-8")
    alias = workspace / "workspace-alias"
    linked = real_workspace / "linked"
    try:
        alias.symlink_to(real_workspace, target_is_directory=True)
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await SearchTextTool(WorkspaceBoundary(alias, ())).execute(
        SearchTextAction(
            id="search-alias-link",
            reason="find",
            path=str(alias / "linked"),
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-alias-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_direct_file_glob_mismatch_returns_empty(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "match.txt").write_text("hit\n", encoding="utf-8")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-direct-glob",
            reason="find",
            path="match.txt",
            pattern="hit",
            file_glob="**/*.py",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == ""


@pytest.mark.asyncio
async def test_search_text_missing_root_has_fixed_failure(
    boundary: WorkspaceBoundary,
) -> None:
    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-missing",
            reason="find",
            path="missing",
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-missing", "NOT_FOUND", "requested path does not exist"
    )


@pytest.mark.asyncio
async def test_search_text_directory_root_searches_descendants(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    root = workspace / "source"
    root.mkdir()
    (root / "match.txt").write_text("hit\n", encoding="utf-8")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-directory",
            reason="find",
            path="source",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "source/match.txt:1:hit"


@pytest.mark.asyncio
async def test_search_text_non_file_root_has_fixed_failure(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "special"
    target.write_text("hit\n", encoding="utf-8")
    real_is_file = Path.is_file
    real_is_dir = Path.is_dir

    def simulated_is_file(path: Path) -> bool:
        if path == target:
            return False
        return real_is_file(path)

    def simulated_is_dir(path: Path) -> bool:
        if path == target:
            return False
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_file", simulated_is_file)
    monkeypatch.setattr(Path, "is_dir", simulated_is_dir)
    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-non-file-root",
            reason="find",
            path="special",
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-non-file-root",
        "NOT_FILE",
        "requested path is not a file or directory",
    )


@pytest.mark.asyncio
async def test_search_public_execute_propagates_clean_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "PRIVATE-SEARCH-ROOT-SENTINEL"
    workspace.mkdir()
    tool = SearchTextTool(WorkspaceBoundary(workspace, ()))
    interrupt = KeyboardInterrupt("EXPECTED-INTERRUPT")

    async def interrupting_to_thread(function: Any, *args: Any) -> Any:
        raise interrupt

    monkeypatch.setattr(filesystem_module.asyncio, "to_thread", interrupting_to_thread)
    with pytest.raises(KeyboardInterrupt) as error_info:
        await tool.execute(
            SearchTextAction(
                id="PRIVATE-SEARCH-ID-SENTINEL",
                reason="find",
                path="PRIVATE-SEARCH-PATH-SENTINEL",
                pattern="PRIVATE-SEARCH-PATTERN-SENTINEL",
                file_glob="PRIVATE-SEARCH-GLOB-SENTINEL",
                max_results=50,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "PRIVATE-SEARCH-ROOT-SENTINEL",
            "PRIVATE-SEARCH-ID-SENTINEL",
            "PRIVATE-SEARCH-PATH-SENTINEL",
            "PRIVATE-SEARCH-PATTERN-SENTINEL",
            "PRIVATE-SEARCH-GLOB-SENTINEL",
        ),
    )


def test_search_sync_propagates_clean_system_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "PRIVATE-SEARCH-SYNC-ROOT"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, ())
    tool = SearchTextTool(boundary)
    interrupt = SystemExit("EXPECTED-INTERRUPT")

    def interrupting_resolve(candidate: str, access: Any) -> Path:
        raise interrupt

    monkeypatch.setattr(boundary, "resolve", interrupting_resolve)
    with pytest.raises(SystemExit) as error_info:
        tool._execute_sync(
            SearchTextAction(
                id="PRIVATE-SEARCH-SYNC-ID",
                reason="find",
                path="PRIVATE-SEARCH-SYNC-PATH",
                pattern="PRIVATE-SEARCH-SYNC-PATTERN",
                file_glob="PRIVATE-SEARCH-SYNC-GLOB",
                max_results=50,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "PRIVATE-SEARCH-SYNC-ROOT",
            "PRIVATE-SEARCH-SYNC-ID",
            "PRIVATE-SEARCH-SYNC-PATH",
            "PRIVATE-SEARCH-SYNC-PATTERN",
            "PRIVATE-SEARCH-SYNC-GLOB",
        ),
    )


def test_search_io_mapping_does_not_chain_sensitive_context(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "private-search-io.txt"
    target.write_text("PRIVATE-SEARCH-IO-CONTENT", encoding="utf-8")
    interrupt = SystemExit("EXPECTED-INTERRUPT")

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        raise OSError("PRIVATE-SEARCH-IO-ERROR")

    def interrupting_io_failure(action_id: str) -> ToolResult:
        raise interrupt

    monkeypatch.setattr(Path, "open", failing_open)
    monkeypatch.setattr(filesystem_module, "_io_failure", interrupting_io_failure)
    with pytest.raises(SystemExit) as error_info:
        SearchTextTool(boundary)._execute_sync(
            SearchTextAction(
                id="search-io-context",
                reason="find",
                path="private-search-io.txt",
                pattern="PRIVATE-SEARCH-IO-PATTERN",
                max_results=50,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "private-search-io",
            "PRIVATE-SEARCH-IO-CONTENT",
            "PRIVATE-SEARCH-IO-ERROR",
            "PRIVATE-SEARCH-IO-PATTERN",
        ),
    )


def test_search_decode_mapping_does_not_chain_content_context(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (workspace / "private-search-binary.bin").write_bytes(
        b"PRIVATE-SEARCH-DECODE-CONTENT\xff"
    )
    interrupt = KeyboardInterrupt("EXPECTED-INTERRUPT")

    class InterruptingToolResult:
        @classmethod
        def failure(cls, action_id: str, error_type: str, message: str) -> Any:
            raise interrupt

    monkeypatch.setattr(filesystem_module, "ToolResult", InterruptingToolResult)
    with pytest.raises(KeyboardInterrupt) as error_info:
        SearchTextTool(boundary)._execute_sync(
            SearchTextAction(
                id="search-decode-context",
                reason="find",
                path="private-search-binary.bin",
                pattern="PRIVATE-SEARCH-DECODE-PATTERN",
                max_results=50,
            )
        )

    _assert_public_execute_interrupt_is_clean(
        error_info.value,
        interrupt,
        (
            "private-search-binary",
            "PRIVATE-SEARCH-DECODE-CONTENT",
            "PRIVATE-SEARCH-DECODE-PATTERN",
        ),
    )


@pytest.mark.parametrize("pattern", ("", "\n", " \r\n "))
def test_search_text_model_rejects_empty_or_newline_only_pattern(
    pattern: str,
) -> None:
    with pytest.raises(ValidationError):
        SearchTextAction(
            id="search-empty-pattern",
            reason="find",
            pattern=pattern,
            max_results=50,
        )


@pytest.mark.asyncio
async def test_search_text_treats_embedded_newline_pattern_literally(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "lines.txt").write_text("hit\nnext\n", encoding="utf-8")
    action = SearchTextAction(
        id="search-newline-pattern",
        reason="find",
        pattern="hit\nnext",
        max_results=50,
    )

    result = await SearchTextTool(boundary).execute(action)

    assert action.pattern == "hit\nnext"
    assert result.success is True
    assert result.stdout_summary == ""


@pytest.mark.asyncio
async def test_read_file_allows_safe_direct_file_symlink(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real.txt"
    target.write_bytes(b"safe content\n")
    link = workspace / "alias.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-safe-file-link",
            reason="inspect",
            path="alias.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "safe content\n"


@pytest.mark.asyncio
async def test_search_text_allows_safe_direct_file_symlink_with_canonical_path(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real.txt"
    target.write_text("hit\n", encoding="utf-8")
    link = workspace / "alias.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-safe-file-link",
            reason="find",
            path="alias.txt",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "real.txt:1:hit"


@pytest.mark.asyncio
async def test_read_file_rejects_sensitive_direct_file_symlink(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    sensitive = workspace / ".env"
    sensitive.write_text("secret\n", encoding="utf-8")
    link = workspace / "safe-name.txt"
    try:
        link.symlink_to(sensitive)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-sensitive-file-link",
            reason="inspect",
            path="safe-name.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-sensitive-file-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_rejects_escaping_and_sensitive_file_symlinks(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    outside = workspace.parent / f"{workspace.name}-outside-search-link.txt"
    outside.write_text("hit outside\n", encoding="utf-8")
    sensitive = workspace / ".env"
    sensitive.write_text("hit secret\n", encoding="utf-8")
    escaping_link = workspace / "escaping.txt"
    sensitive_link = workspace / "sensitive.txt"
    try:
        escaping_link.symlink_to(outside)
        sensitive_link.symlink_to(sensitive)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    tool = SearchTextTool(boundary)
    escaping_result = await tool.execute(
        SearchTextAction(
            id="search-escaping-file-link",
            reason="find",
            path="escaping.txt",
            pattern="hit",
            max_results=50,
        )
    )
    sensitive_result = await tool.execute(
        SearchTextAction(
            id="search-sensitive-file-link",
            reason="find",
            path="sensitive.txt",
            pattern="hit",
            max_results=50,
        )
    )

    assert escaping_result == ToolResult.failure(
        "search-escaping-file-link", "PATH_DENIED", "path access is denied"
    )
    assert sensitive_result == ToolResult.failure(
        "search-sensitive-file-link", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_directory_enumeration_skips_file_symlinks(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real.txt"
    target.write_text("hit\n", encoding="utf-8")
    link = workspace / "alias.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-enumerated-file-link",
            reason="find",
            pattern="hit",
            max_results=50,
        )
    )

    assert result.success is True
    assert result.stdout_summary == "real.txt:1:hit"


@pytest.mark.asyncio
async def test_read_file_rejects_symlink_ancestor_before_missing_check(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real"
    target.mkdir()
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-link-missing",
            reason="inspect",
            path="linked/missing.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-link-missing", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_read_file_rejects_junction_ancestor_before_missing_check(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await ReadFileTool(boundary).execute(
        ReadFileAction(
            id="read-junction-missing",
            reason="inspect",
            path="junction/missing.txt",
            start_line=1,
            end_line=1,
        )
    )

    assert result == ToolResult.failure(
        "read-junction-missing", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_rejects_symlink_ancestor_before_missing_check(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real"
    target.mkdir()
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-link-missing",
            reason="find",
            path="linked/missing.txt",
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-link-missing", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_search_text_rejects_junction_ancestor_before_missing_check(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = workspace / "junction"
    junction.mkdir()
    real_is_junction = getattr(Path, "is_junction", None)

    def simulated_is_junction(path: Path) -> bool:
        if path == junction:
            return True
        return bool(real_is_junction is not None and real_is_junction(path))

    monkeypatch.setattr(Path, "is_junction", simulated_is_junction, raising=False)
    result = await SearchTextTool(boundary).execute(
        SearchTextAction(
            id="search-junction-missing",
            reason="find",
            path="junction/missing.txt",
            pattern="hit",
            max_results=50,
        )
    )

    assert result == ToolResult.failure(
        "search-junction-missing", "PATH_DENIED", "path access is denied"
    )


@pytest.mark.asyncio
async def test_patch_atomically_replaces_text_and_reports_relative_file(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "src" / "app.py"
    target.parent.mkdir()
    original = b"value = 1\n"
    target.write_bytes(original)
    original_mode = stat.S_IMODE(target.stat().st_mode)
    action = ApplyPatchAction(
        id="patch-3",
        reason="fix",
        path="src/app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result.success is True
    assert result.changed_files == ("src/app.py",)
    assert result.stdout_summary == ""
    assert target.read_bytes() == b"value = 2\n"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == original_mode


@pytest.mark.asyncio
async def test_patch_replace_failure_keeps_original_and_removes_temp_files(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    target.write_bytes(original)

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("PRIVATE-LOW-LEVEL-ERROR")

    monkeypatch.setattr(os, "replace", fail_replace)
    action = ApplyPatchAction(
        id="patch-4",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result == ToolResult.failure(
        "patch-4", "IO_ERROR", "filesystem operation failed"
    )
    assert target.read_bytes() == original
    assert [path for path in workspace.iterdir() if path.name != "app.py"] == []
    assert "PRIVATE-LOW-LEVEL-ERROR" not in result.stderr_summary


@pytest.mark.asyncio
async def test_patch_close_error_does_not_override_process_control(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    target.write_bytes(original)
    interrupt = KeyboardInterrupt()
    real_fdopen = os.fdopen

    class InterruptingStream:
        def __init__(self, descriptor: int) -> None:
            self._stream = real_fdopen(descriptor, "wb")

        def __enter__(self) -> InterruptingStream:
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def close(self) -> None:
            self._stream.close()
            raise OSError("PRIVATE-CLOSE-ERROR")

        def write(self, data: bytes) -> int:
            raise interrupt

        def flush(self) -> None:
            self._stream.flush()

        def fileno(self) -> int:
            return self._stream.fileno()

    def interrupting_fdopen(descriptor: int, mode: str) -> InterruptingStream:
        assert mode == "wb"
        return InterruptingStream(descriptor)

    monkeypatch.setattr(os, "fdopen", interrupting_fdopen)
    action = ApplyPatchAction(
        id="patch-close-interrupt",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    with pytest.raises(KeyboardInterrupt) as captured:
        await ApplyPatchTool(boundary).execute(action)

    assert captured.value is interrupt
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert target.read_bytes() == original
    assert not list(workspace.glob(".safefix-*.tmp"))


@pytest.mark.asyncio
async def test_patch_prepares_result_before_replace_commit(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    target.write_bytes(original)

    def fail_relative(root: Path, canonical_target: Path) -> str:
        raise OSError("PRIVATE-RELATIVE-ERROR")

    monkeypatch.setattr(filesystem_module, "_relative_posix", fail_relative)
    action = ApplyPatchAction(
        id="patch-precommit",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result == ToolResult.failure(
        "patch-precommit", "IO_ERROR", "filesystem operation failed"
    )
    assert target.read_bytes() == original
    assert not list(workspace.glob(".safefix-*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_type", (OSError, KeyboardInterrupt, SystemExit))
async def test_patch_owns_raw_temp_name_immediately_after_mkstemp(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
) -> None:
    target = workspace / "PRIVATE-REGISTER-PATH.py"
    original = b"PRIVATE-REGISTER-OLD\n"
    target.write_bytes(original)
    injected = exception_type()
    real_mkstemp = tempfile.mkstemp
    descriptors: list[int] = []
    names: list[str] = []

    def tracking_mkstemp(*, prefix: str, suffix: str, dir: Path) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=dir)
        descriptors.append(descriptor)
        names.append(name)
        return descriptor, name

    def fail_registration(name: str) -> None:
        raise injected

    monkeypatch.setattr(tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(
        filesystem_module,
        "_require_temporary_name",
        fail_registration,
        raising=False,
    )
    action = ApplyPatchAction(
        id="PRIVATE-REGISTER-ID",
        reason="fix",
        path="PRIVATE-REGISTER-PATH.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="PRIVATE-REGISTER-OLD",
        new_text="PRIVATE-REGISTER-NEW",
        expected_replacements=1,
    )

    if exception_type is OSError:
        result = await ApplyPatchTool(boundary).execute(action)
        assert result == ToolResult.failure(
            "PRIVATE-REGISTER-ID", "IO_ERROR", "filesystem operation failed"
        )
    else:
        with pytest.raises(exception_type) as captured:
            await ApplyPatchTool(boundary).execute(action)
        assert captured.value is injected
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        rendered = "".join(traceback_module.format_exception(captured.value))
        for sentinel in (
            "PRIVATE-REGISTER-ID",
            "PRIVATE-REGISTER-PATH",
            "PRIVATE-REGISTER-OLD",
            "PRIVATE-REGISTER-NEW",
        ):
            assert sentinel not in rendered
        for _, frame_locals in _filesystem_frame_locals(captured.value):
            local_rendering = repr(frame_locals)
            for sentinel in (
                "PRIVATE-REGISTER-ID",
                "PRIVATE-REGISTER-PATH",
                "PRIVATE-REGISTER-OLD",
                "PRIVATE-REGISTER-NEW",
            ):
                assert sentinel not in local_rendering

    assert len(descriptors) == 1
    assert len(names) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not Path(names[0]).exists()
    assert target.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("use_file_alias", (False, True))
async def test_patch_canonical_lock_serializes_overlapping_workers(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
    use_file_alias: bool,
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    target.write_bytes(original)
    second_path = "app.py"
    if use_file_alias:
        alias = workspace / "alias.py"
        try:
            alias.symlink_to(target)
        except OSError:
            pytest.skip("file symlinks are unavailable")
        second_path = "alias.py"

    rendezvous = threading.Barrier(2)
    observation_guard = threading.Lock()
    both_attempting = threading.Event()
    release_critical = threading.Event()
    attempts = 0
    active = 0
    max_active = 0
    real_patch_lock = filesystem_module._patch_lock

    class ObservedLock:
        def __init__(self, wrapped: Any) -> None:
            self._wrapped = wrapped

        def __enter__(self) -> ObservedLock:
            nonlocal attempts, active, max_active
            with observation_guard:
                attempts += 1
                if attempts == 2:
                    both_attempting.set()
            self._wrapped.acquire()
            with observation_guard:
                active += 1
                max_active = max(max_active, active)
            assert release_critical.wait(2)
            return self

        def __exit__(self, *args: object) -> None:
            nonlocal active
            with observation_guard:
                active -= 1
            self._wrapped.release()

    def synchronized_patch_lock(canonical_target: Path) -> ObservedLock:
        rendezvous.wait(timeout=2)
        return ObservedLock(real_patch_lock(canonical_target))

    monkeypatch.setattr(filesystem_module, "_patch_lock", synchronized_patch_lock)

    def action(action_id: str, path: str, replacement: str) -> ApplyPatchAction:
        return ApplyPatchAction(
            id=action_id,
            reason="fix",
            path=path,
            expected_sha256=sha256(original).hexdigest(),
            old_text="value = 1",
            new_text=replacement,
            expected_replacements=1,
        )

    pending = asyncio.gather(
        ApplyPatchTool(boundary).execute(action("patch-first", "app.py", "value = 2")),
        ApplyPatchTool(boundary).execute(
            action("patch-second", second_path, "value = 3")
        ),
    )
    assert await asyncio.to_thread(both_attempting.wait, 2)
    release_critical.set()
    first, second = await asyncio.wait_for(pending, timeout=3)

    assert max_active == 1
    assert active == 0
    assert sorted(result.error_type or "SUCCESS" for result in (first, second)) == [
        "STALE_FILE",
        "SUCCESS",
    ]
    assert target.read_bytes() in {b"value = 2\n", b"value = 3\n"}
    assert not list(workspace.glob(".safefix-*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_type", (KeyboardInterrupt, SystemExit))
async def test_patch_releases_canonical_lock_after_process_control(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    target.write_bytes(original)
    injected = exception_type()
    real_read_bytes = Path.read_bytes
    should_interrupt = True

    def interrupt_first_locked_read(path: Path) -> bytes:
        nonlocal should_interrupt
        if path == target and should_interrupt:
            should_interrupt = False
            raise injected
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", interrupt_first_locked_read)
    action = ApplyPatchAction(
        id="patch-lock-release",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    with pytest.raises(exception_type) as captured:
        await asyncio.wait_for(ApplyPatchTool(boundary).execute(action), timeout=2)
    assert captured.value is injected

    result = await asyncio.wait_for(ApplyPatchTool(boundary).execute(action), timeout=2)

    assert result.success is True
    assert target.read_bytes() == b"value = 2\n"
    assert not list(workspace.glob(".safefix-*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_type", (OSError, KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize(
    "stage",
    (
        "fdopen",
        "write",
        "flush",
        "fsync",
        "close",
        "chmod",
        "second_resolve",
        "ancestor_recheck",
        "second_read",
        "second_hash",
        "replace",
        "cleanup_fd_close",
        "cleanup_stream_close",
        "cleanup_unlink",
    ),
)
async def test_patch_failure_injection_matrix_cleans_every_resource(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    exception_type: type[BaseException],
) -> None:
    target = workspace / "PRIVATE-MATRIX-PATH.py"
    original = b"PRIVATE-MATRIX-OLD\n"
    target.write_bytes(original)
    tool = ApplyPatchTool(boundary)
    injected = exception_type()
    real_mkstemp = tempfile.mkstemp
    real_fdopen = os.fdopen
    real_close = os.close
    real_fsync = os.fsync
    real_chmod = os.chmod
    real_unlink = os.unlink
    real_read_bytes = Path.read_bytes
    real_resolve = boundary.resolve
    real_link_check = filesystem_module._contains_directory_link
    real_sha256 = filesystem_module.sha256
    descriptors: list[int] = []
    temporary_names: list[str] = []
    resolve_calls = 0
    link_checks = 0
    reads = 0
    hashes = 0

    def tracking_mkstemp(*, prefix: str, suffix: str, dir: Path) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=dir)
        descriptors.append(descriptor)
        temporary_names.append(name)
        return descriptor, name

    class FaultingStream:
        def __init__(self, descriptor: int) -> None:
            self._stream = real_fdopen(descriptor, "wb")

        def write(self, data: bytes) -> int:
            if stage in {"write", "cleanup_stream_close"}:
                raise injected
            return self._stream.write(data)

        def flush(self) -> None:
            if stage == "flush":
                raise injected
            self._stream.flush()

        def fileno(self) -> int:
            return self._stream.fileno()

        def close(self) -> None:
            self._stream.close()
            if stage == "close":
                raise injected
            if stage == "cleanup_stream_close":
                raise OSError()

    def faulting_fdopen(descriptor: int, mode: str) -> FaultingStream:
        assert mode == "wb"
        if stage in {"fdopen", "cleanup_fd_close"}:
            raise injected
        return FaultingStream(descriptor)

    def faulting_close(descriptor: int) -> None:
        real_close(descriptor)
        if stage == "cleanup_fd_close" and descriptor in descriptors:
            raise OSError()

    def faulting_fsync(descriptor: int) -> None:
        if stage == "fsync":
            raise injected
        real_fsync(descriptor)

    def faulting_chmod(path: str | bytes | os.PathLike[str], mode: int) -> None:
        if stage == "chmod" and os.fspath(path) in temporary_names:
            raise injected
        real_chmod(path, mode)

    def faulting_resolve(candidate: str, access: Any) -> Path:
        nonlocal resolve_calls
        if candidate == "PRIVATE-MATRIX-PATH.py":
            resolve_calls += 1
            if stage == "second_resolve" and resolve_calls == 4:
                raise injected
        return real_resolve(candidate, access)

    def faulting_link_check(*args: Any, **kwargs: Any) -> bool:
        nonlocal link_checks
        link_checks += 1
        if stage == "ancestor_recheck" and link_checks == 3:
            raise injected
        return real_link_check(*args, **kwargs)

    def faulting_read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path == target:
            reads += 1
            if stage == "second_read" and reads == 2:
                raise injected
        return real_read_bytes(path)

    def faulting_sha256(data: bytes = b"") -> Any:
        nonlocal hashes
        if data == original:
            hashes += 1
            if stage == "second_hash" and hashes == 2:
                raise injected
        return real_sha256(data)

    def faulting_replace(source: object, destination: object) -> None:
        raise injected

    def faulting_unlink(path: str | bytes | os.PathLike[str]) -> None:
        real_unlink(path)
        if stage == "cleanup_unlink" and os.fspath(path) in temporary_names:
            raise OSError()

    monkeypatch.setattr(tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(os, "fdopen", faulting_fdopen)
    monkeypatch.setattr(os, "close", faulting_close)
    monkeypatch.setattr(os, "fsync", faulting_fsync)
    monkeypatch.setattr(os, "chmod", faulting_chmod)
    monkeypatch.setattr(boundary, "resolve", faulting_resolve)
    monkeypatch.setattr(
        filesystem_module, "_contains_directory_link", faulting_link_check
    )
    monkeypatch.setattr(Path, "read_bytes", faulting_read_bytes)
    monkeypatch.setattr(filesystem_module, "sha256", faulting_sha256)
    if stage in {"replace", "cleanup_unlink"}:
        monkeypatch.setattr(os, "replace", faulting_replace)
    if stage == "cleanup_unlink":
        monkeypatch.setattr(os, "unlink", faulting_unlink)

    action = ApplyPatchAction(
        id="PRIVATE-MATRIX-ID",
        reason="fix",
        path="PRIVATE-MATRIX-PATH.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="PRIVATE-MATRIX-OLD",
        new_text="PRIVATE-MATRIX-NEW",
        expected_replacements=1,
    )

    if exception_type is OSError:
        result = await tool.execute(action)
        assert result == ToolResult.failure(
            "PRIVATE-MATRIX-ID", "IO_ERROR", "filesystem operation failed"
        )
        for sentinel in (
            "PRIVATE-MATRIX-PATH",
            "PRIVATE-MATRIX-OLD",
            "PRIVATE-MATRIX-NEW",
        ):
            assert sentinel not in result.stderr_summary
    else:
        with pytest.raises(exception_type) as captured:
            await tool.execute(action)
        assert captured.value is injected
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        rendered = "".join(traceback_module.format_exception(captured.value))
        for sentinel in (
            "PRIVATE-MATRIX-ID",
            "PRIVATE-MATRIX-PATH",
            "PRIVATE-MATRIX-OLD",
            "PRIVATE-MATRIX-NEW",
        ):
            assert sentinel not in rendered
        for _, frame_locals in _filesystem_frame_locals(captured.value):
            local_rendering = repr(frame_locals)
            for sentinel in (
                "PRIVATE-MATRIX-ID",
                "PRIVATE-MATRIX-PATH",
                "PRIVATE-MATRIX-OLD",
                "PRIVATE-MATRIX-NEW",
            ):
                assert sentinel not in local_rendering

    assert len(descriptors) == 1
    assert len(temporary_names) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not Path(temporary_names[0]).exists()
    assert real_read_bytes(target) == original
    canonical_target = real_resolve("PRIVATE-MATRIX-PATH.py", AccessKind.WRITE)
    canonical_lock = filesystem_module._patch_lock(canonical_target)
    assert canonical_lock.acquire(blocking=False)
    canonical_lock.release()


@pytest.mark.asyncio
async def test_two_tool_instances_patch_same_file_at_most_once(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    target.write_bytes(original)

    def action(action_id: str, new_text: str) -> ApplyPatchAction:
        return ApplyPatchAction(
            id=action_id,
            reason="fix",
            path="app.py",
            expected_sha256=sha256(original).hexdigest(),
            old_text="value = 1",
            new_text=new_text,
            expected_replacements=1,
        )

    first, second = await asyncio.gather(
        ApplyPatchTool(boundary).execute(action("patch-a", "value = 2")),
        ApplyPatchTool(boundary).execute(action("patch-b", "value = 3")),
    )

    assert sorted(result.error_type or "SUCCESS" for result in (first, second)) == [
        "STALE_FILE",
        "SUCCESS",
    ]
    assert target.read_bytes() in {b"value = 2\n", b"value = 3\n"}
    assert not list(workspace.glob(".safefix-*.tmp"))


@pytest.mark.asyncio
async def test_patch_rechecks_digest_before_replace(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    external = b"external edit\n"
    target.write_bytes(original)
    real_read_bytes = Path.read_bytes
    reads = 0

    def racing_read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path == target:
            reads += 1
            if reads == 2:
                target.write_bytes(external)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", racing_read_bytes)
    action = ApplyPatchAction(
        id="patch-race",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result.error_type == "STALE_FILE"
    assert target.read_bytes() == external
    assert not list(workspace.glob(".safefix-*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit()])
async def test_patch_propagates_process_control_and_cleans_temp(
    workspace: Path,
    boundary: WorkspaceBoundary,
    monkeypatch: pytest.MonkeyPatch,
    interrupt: BaseException,
) -> None:
    target = workspace / "app.py"
    original = b"value = 1\n"
    target.write_bytes(original)

    def interrupted_replace(source: object, destination: object) -> None:
        raise interrupt

    monkeypatch.setattr(os, "replace", interrupted_replace)
    action = ApplyPatchAction(
        id="patch-interrupt",
        reason="fix",
        path="app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=1,
    )

    with pytest.raises(type(interrupt)) as captured:
        await ApplyPatchTool(boundary).execute(action)

    assert captured.value is interrupt
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert target.read_bytes() == original
    assert not list(workspace.glob(".safefix-*.tmp"))
    rendered = "".join(traceback_module.format_exception(captured.value))
    for sentinel in ("value = 1", "value = 2", repr(original)):
        assert sentinel not in rendered
    tb = captured.value.__traceback__
    component_locals: list[str] = []
    sensitive_names = {
        "action",
        "lock_target",
        "requested_path",
        "old_text",
        "new_text",
        "lexical_target",
        "target",
        "verified_target",
        "current_bytes",
        "replacement",
        "temporary_name",
        "temporary",
        "latest_target",
        "latest_bytes",
    }
    while tb is not None:
        if tb.tb_frame.f_code.co_filename.replace("\\", "/").endswith(
            "safefix/tools/filesystem.py"
        ):
            assert sensitive_names.isdisjoint(tb.tb_frame.f_locals)
            component_locals.extend(
                repr(value) for value in tb.tb_frame.f_locals.values()
            )
        tb = tb.tb_next
    assert component_locals
    for sentinel in ("value = 1", "value = 2", repr(original)):
        assert all(sentinel not in value for value in component_locals)


@pytest.mark.asyncio
async def test_patch_reports_missing_directory_sensitive_and_binary_targets(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "directory").mkdir()
    (workspace / ".env").write_text("secret", encoding="utf-8")
    binary = workspace / "binary.bin"
    binary_bytes = b"text\xff"
    binary.write_bytes(binary_bytes)
    tool = ApplyPatchTool(boundary)

    missing = await tool.execute(
        ApplyPatchAction(
            id="patch-missing",
            reason="fix",
            path="missing",
            expected_sha256=sha256(b"").hexdigest(),
            old_text="x",
            new_text="y",
            expected_replacements=1,
        )
    )
    directory = await tool.execute(
        ApplyPatchAction(
            id="patch-dir",
            reason="fix",
            path="directory",
            expected_sha256=sha256(b"").hexdigest(),
            old_text="x",
            new_text="y",
            expected_replacements=1,
        )
    )
    sensitive = await tool.execute(
        ApplyPatchAction(
            id="patch-secret",
            reason="fix",
            path=".env",
            expected_sha256=sha256(b"secret").hexdigest(),
            old_text="secret",
            new_text="public",
            expected_replacements=1,
        )
    )
    binary_result = await tool.execute(
        ApplyPatchAction(
            id="patch-binary",
            reason="fix",
            path="binary.bin",
            expected_sha256=sha256(binary_bytes).hexdigest(),
            old_text="text",
            new_text="other",
            expected_replacements=1,
        )
    )

    assert missing.error_type == "NOT_FOUND"
    assert directory.error_type == "NOT_FILE"
    assert sensitive.error_type == "PATH_DENIED"
    assert binary_result.error_type == "BINARY_FILE"
    assert (workspace / ".env").read_text(encoding="utf-8") == "secret"
    assert binary.read_bytes() == binary_bytes
    assert not list(workspace.glob(".safefix-*.tmp"))


@pytest.mark.asyncio
async def test_patch_rejects_wrong_structured_action(
    boundary: WorkspaceBoundary,
) -> None:
    wrong = ReadFileAction(
        id="wrong", reason="inspect", path="app.py", start_line=1, end_line=200
    )
    result = await ApplyPatchTool(boundary).execute(wrong)
    assert result == ToolResult.failure(
        "wrong", "UNSUPPORTED_ACTION", "tool does not support this action"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_path", ("../app.py", "folder/../app.py"))
async def test_patch_rejects_every_parent_reference(
    workspace: Path, boundary: WorkspaceBoundary, requested_path: str
) -> None:
    original = b"value = 1\n"
    (workspace / "app.py").write_bytes(original)

    result = await ApplyPatchTool(boundary).execute(
        ApplyPatchAction(
            id="patch-parent",
            reason="fix",
            path=requested_path,
            expected_sha256=sha256(original).hexdigest(),
            old_text="value = 1",
            new_text="value = 2",
            expected_replacements=1,
        )
    )

    assert result == ToolResult.failure(
        "patch-parent", "PATH_DENIED", "path access is denied"
    )
    assert (workspace / "app.py").read_bytes() == original


@pytest.mark.asyncio
async def test_patch_rejects_directory_symlink_ancestor(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real" / "app.py"
    target.parent.mkdir()
    original = b"value = 1\n"
    target.write_bytes(original)
    link = workspace / "linked"
    try:
        link.symlink_to(target.parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = await ApplyPatchTool(boundary).execute(
        ApplyPatchAction(
            id="patch-link-ancestor",
            reason="fix",
            path="linked/app.py",
            expected_sha256=sha256(original).hexdigest(),
            old_text="value = 1",
            new_text="value = 2",
            expected_replacements=1,
        )
    )

    assert result == ToolResult.failure(
        "patch-link-ancestor", "PATH_DENIED", "path access is denied"
    )
    assert target.read_bytes() == original


@pytest.mark.asyncio
async def test_patch_allows_safe_file_symlink_and_reports_canonical_path(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "real.py"
    original = b"value = 1\n"
    target.write_bytes(original)
    link = workspace / "alias.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    result = await ApplyPatchTool(boundary).execute(
        ApplyPatchAction(
            id="patch-safe-file-link",
            reason="fix",
            path="alias.py",
            expected_sha256=sha256(original).hexdigest(),
            old_text="value = 1",
            new_text="value = 2",
            expected_replacements=1,
        )
    )

    assert result.success is True
    assert result.changed_files == ("real.py",)
    assert link.is_symlink()
    assert target.read_bytes() == b"value = 2\n"


@pytest.mark.asyncio
async def test_patch_rejects_sensitive_and_escaping_file_symlinks(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    sensitive = workspace / ".env"
    sensitive.write_bytes(b"secret")
    outside = workspace.parent / f"{workspace.name}-outside-patch-link.txt"
    outside.write_bytes(b"outside")
    sensitive_link = workspace / "sensitive.txt"
    escaping_link = workspace / "escaping.txt"
    try:
        sensitive_link.symlink_to(sensitive)
        escaping_link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    tool = ApplyPatchTool(boundary)

    sensitive_result = await tool.execute(
        ApplyPatchAction(
            id="patch-sensitive-link",
            reason="fix",
            path="sensitive.txt",
            expected_sha256=sha256(b"secret").hexdigest(),
            old_text="secret",
            new_text="public",
            expected_replacements=1,
        )
    )
    escaping_result = await tool.execute(
        ApplyPatchAction(
            id="patch-escaping-link",
            reason="fix",
            path="escaping.txt",
            expected_sha256=sha256(b"outside").hexdigest(),
            old_text="outside",
            new_text="inside",
            expected_replacements=1,
        )
    )

    assert sensitive_result.error_type == "PATH_DENIED"
    assert escaping_result.error_type == "PATH_DENIED"
    assert sensitive.read_bytes() == b"secret"
    assert outside.read_bytes() == b"outside"
