from __future__ import annotations

import os
from pathlib import Path
import traceback as traceback_module
from typing import Any, SupportsIndex

import pytest

import safefix.tools.filesystem as filesystem_module
from safefix.domain import ListFilesAction, ReadFileAction, ToolResult
from safefix.governance.paths import WorkspaceBoundary
from safefix.tools.filesystem import FilesystemLimits, ListFilesTool, ReadFileTool


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
            ignored_directories=(
                InterruptingDirectory("PRIVATE-IGNORED-SENTINEL"),
            ),
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

    def interrupting_relative_to(
        path: Path, *other: Any, **kwargs: Any
    ) -> Path:
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
        ListFilesAction(
            id="list-race", reason="inspect", path="victim", limit=100
        )
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
        def failure(
            cls, action_id: str, error_type: str, message: str
        ) -> Any:
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

    def interrupting_relative_to(
        path: Path, *other: Any, **kwargs: Any
    ) -> Path:
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

    monkeypatch.setattr(
        filesystem_module, "PurePosixPath", interrupting_posix_path
    )
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
        def failure(
            cls, action_id: str, error_type: str, message: str
        ) -> Any:
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
    monkeypatch.setattr(
        filesystem_module, "_io_failure", interrupting_io_failure
    )
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
        def failure(
            cls, action_id: str, error_type: str, message: str
        ) -> Any:
            raise interrupt

    monkeypatch.setattr(
        filesystem_module, "ToolResult", InterruptingToolResult
    )
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
