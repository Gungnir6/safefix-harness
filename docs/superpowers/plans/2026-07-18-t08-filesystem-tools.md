# T08 Bounded Filesystem Tools and Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现四个只在 T04 工作区边界内运行的异步文件工具，以及按结构化 Action 精确分发的工具注册表。

**Architecture:** `ToolRegistry` 只按 Action 类分发到单一职责 `Tool`；四个文件工具共享 `WorkspaceBoundary`、`FilesystemLimits`、忽略目录规范化和安全结果构造器。文件枚举与读取在线程中执行，补丁使用规范路径分片锁、二次摘要验证、同目录临时文件和 `os.replace()`，所有普通失败转换为稳定、无数据的 `ToolResult`。

**Tech Stack:** Python 3.12、asyncio、pathlib/os/tempfile/hashlib/hmac、pathspec、Pydantic 领域模型、pytest/pytest-asyncio、Ruff、mypy。

## Global Constraints

- 只允许修改 `src/safefix/tools/__init__.py`、`src/safefix/tools/base.py`、`src/safefix/tools/filesystem.py`、`src/safefix/tools/registry.py`、`tests/unit/test_filesystem_tools.py`、`tests/unit/test_tool_registry.py`，以及 T08 设计/计划/过程文档。
- 所有生产行为必须严格 RED→GREEN；没有先看到预期失败，不得写对应生产代码。
- 所有公开文件操作必须先调用 `WorkspaceBoundary.resolve()`；枚举根目录允许不代表后代自动允许。
- `.git` 在任意层级永久忽略；其他忽略目录来自构造器，不读取 `.gitignore`，不修改 `SafeFixSettings`。
- 默认 `max_read_bytes=65_536`、`max_search_files=1_000`、`max_search_output_bytes=65_536`，所有限制必须大于零。
- `read_file`、`search_text`、`apply_patch` 只接受严格 UTF-8；搜索文本按字面匹配，文件模式使用 GitWildMatch。
- 普通工具错误返回 `ToolResult`；公开错误消息、cause/context 和 T08 traceback locals 不得包含路径、模式、搜索文本、补丁文本、文件内容、绝对路径、敏感规则或底层异常。
- `KeyboardInterrupt`、`SystemExit` 原样传播，但必须先释放锁、关闭文件、删除临时文件并清理 T08 自身 frame 的敏感 locals。
- 不执行 LLM、PolicyEngine、ApprovalStateMachine、AuditStore、进程或 validator；T08 注册表不是授权边界。
- 所有 Git 提交使用中文 Conventional Commits；不得暂存主工作区三份未跟踪课程文档。

---

### Task 1: 异步 Tool 协议与精确类型注册表

**Files:**
- Create: `src/safefix/tools/__init__.py`
- Create: `src/safefix/tools/base.py`
- Create: `src/safefix/tools/registry.py`
- Create: `tests/unit/test_tool_registry.py`

**Interfaces:**
- Consumes: `Action`、七个具体 Action 类、`ToolResult`。
- Produces: `Tool.action_type`、`Tool.execute(action) -> ToolResult`、`ToolRegistry.register(tool)`、`ToolRegistry.dispatch(action) -> ToolResult`。

- [ ] **Step 1: 写注册表精确分发的失败测试**

创建 `tests/unit/test_tool_registry.py`，先定义只用于测试的工具：

```python
from __future__ import annotations

import traceback
from dataclasses import dataclass, field

import pytest

from safefix.domain import (
    Action,
    FinishAction,
    ReadFileAction,
    ToolResult,
)
from safefix.tools.registry import ToolRegistry


@dataclass
class RecordingReadTool:
    calls: list[Action] = field(default_factory=list)

    @property
    def action_type(self) -> type[object]:
        return ReadFileAction

    async def execute(self, action: Action) -> ToolResult:
        self.calls.append(action)
        return ToolResult(action_id=action.id, success=True, stdout_summary="ok")


@pytest.mark.asyncio
async def test_registry_dispatches_exact_action_class_once() -> None:
    tool = RecordingReadTool()
    registry = ToolRegistry((tool,))
    action = ReadFileAction(id="a1", reason="inspect", path="app.py")

    result = await registry.dispatch(action)

    assert result == ToolResult(action_id="a1", success=True, stdout_summary="ok")
    assert tool.calls == [action]


@pytest.mark.asyncio
async def test_registry_returns_failure_when_tool_is_missing() -> None:
    action = FinishAction(id="a2", reason="done", summary="complete")

    result = await ToolRegistry().dispatch(action)

    assert result == ToolResult.failure(
        "a2", "TOOL_NOT_FOUND", "no tool is registered for this action"
    )
```

- [ ] **Step 2: 写非法输入、重复注册和非披露失败测试**

在同一文件追加：

```python
def test_registry_rejects_duplicate_action_type() -> None:
    registry = ToolRegistry((RecordingReadTool(),))

    with pytest.raises(ValueError, match="^tool is already registered for action type$"):
        registry.register(RecordingReadTool())


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["read_file", {"type": "read_file"}, object()])
async def test_registry_rejects_untyped_inputs(raw: object) -> None:
    with pytest.raises(TypeError, match="^dispatch requires a structured Action$"):
        await ToolRegistry().dispatch(raw)


@pytest.mark.asyncio
async def test_registry_traceback_locals_do_not_retain_raw_input() -> None:
    sentinel = "TOP-SECRET-RAW-ACTION"
    try:
        await ToolRegistry().dispatch(sentinel)
    except TypeError as exc:
        frames = [
            frame
            for frame in traceback.extract_tb(exc.__traceback__)
            if frame.filename.replace("\\", "/").endswith("safefix/tools/registry.py")
        ]
        local_values: list[str] = []
        tb = exc.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_filename.replace("\\", "/").endswith(
                "safefix/tools/registry.py"
            ):
                local_values.extend(repr(value) for value in tb.tb_frame.f_locals.values())
            tb = tb.tb_next
        assert frames
        assert all(sentinel not in value for value in local_values)
        assert exc.__cause__ is None
        assert exc.__context__ is None
    else:
        pytest.fail("TypeError was not raised")
```

测试不得用 `cast()` 把 raw 输入伪装成 `Action`；`dispatch(object)` 是有意的安全边界。

- [ ] **Step 3: 运行 RED 并确认失败原因**

Run:

```powershell
python -m pytest tests/unit/test_tool_registry.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'safefix.tools'`。如果失败来自测试拼写或 fixture，先修正测试并重跑，直到只因生产模块不存在而 RED。

- [ ] **Step 4: 实现 Tool 协议**

创建 `src/safefix/tools/base.py`：

```python
from __future__ import annotations

from typing import Protocol

from safefix.domain import Action, ToolResult


class Tool(Protocol):
    @property
    def action_type(self) -> type[object]:
        raise NotImplementedError

    async def execute(self, action: Action) -> ToolResult:
        raise NotImplementedError
```

协议不接受原始 dict/string，不增加通用 payload，不让工具调用策略或 LLM。

- [ ] **Step 5: 实现安全注册表**

创建 `src/safefix/tools/registry.py`。定义七个领域 Action 类组成的 `_ACTION_TYPES`；`register()` 验证 `tool.action_type` 必须是其中一个精确类，并拒绝重复键。`dispatch()` 使用无数据 outcome helper：先把输入转换为 `(tool, typed_action, action_id, error_code)`，删除公开 frame 中的原始参数，再在安全 frame 外抛固定 `TypeError` 或构造 `TOOL_NOT_FOUND`。

核心结构必须等价于：

```python
_ACTION_TYPES = (
    ListFilesAction,
    ReadFileAction,
    SearchTextAction,
    ApplyPatchAction,
    RunValidationAction,
    RunProcessAction,
    FinishAction,
)


@dataclass(frozen=True, slots=True)
class _DispatchOutcome:
    kind: Literal["dispatch", "missing", "invalid"]
    tool: Tool | None = None
    action: Action | None = None
    action_id: str | None = None


def _capture_dispatch(
    tools: Mapping[type[object], Tool], raw: object
) -> _DispatchOutcome:
    if not isinstance(raw, _ACTION_TYPES):
        del raw
        return _DispatchOutcome("invalid")
    tool = tools.get(type(raw))
    if tool is None:
        action_id = raw.id
        del raw
        return _DispatchOutcome("missing", action_id=action_id)
    return _DispatchOutcome("dispatch", tool=tool, action=raw)


def _raise_invalid_dispatch() -> NoReturn:
    raise TypeError("dispatch requires a structured Action")


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[type[object], Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        action_type = tool.action_type
        if action_type not in _ACTION_TYPES:
            raise TypeError("tool action_type must be a structured Action class")
        if action_type in self._tools:
            raise ValueError("tool is already registered for action type")
        self._tools[action_type] = tool

    async def dispatch(self, action: object) -> ToolResult:
        outcome = _capture_dispatch(self._tools, action)
        del action
        if outcome.kind == "invalid":
            del outcome
            _raise_invalid_dispatch()
        if outcome.kind == "missing":
            action_id = outcome.action_id
            del outcome
            assert action_id is not None
            return ToolResult.failure(
                action_id,
                "TOOL_NOT_FOUND",
                "no tool is registered for this action",
            )
        tool = outcome.tool
        typed_action = outcome.action
        del outcome
        assert tool is not None
        assert typed_action is not None
        try:
            return await tool.execute(typed_action)
        finally:
            del tool, typed_action
```

同时导入 `dataclass`、`Iterable`、`Mapping`、`Literal` 和 `NoReturn`。helper outcome 只携带合法 Action 或固定错误码，不携带非法 raw 对象。`_raise_invalid_dispatch()` 在异常上下文外调用，保证 cause/context 为空。

- [ ] **Step 6: 导出公共接口并运行 GREEN**

创建 `src/safefix/tools/__init__.py`，只导出当前已存在的：

```python
from safefix.tools.base import Tool
from safefix.tools.registry import ToolRegistry

__all__ = ["Tool", "ToolRegistry"]
```

Run:

```powershell
python -m pytest tests/unit/test_tool_registry.py -q -p no:cacheprovider
python -m ruff check --no-cache src/safefix/tools tests/unit/test_tool_registry.py
python -m mypy --no-incremental src/safefix/tools tests/unit/test_tool_registry.py
```

Expected: 注册表测试全部 PASS；Ruff 和 mypy exit 0。

- [ ] **Step 7: 提交 Task 1**

```powershell
git add -- src/safefix/tools/__init__.py src/safefix/tools/base.py src/safefix/tools/registry.py tests/unit/test_tool_registry.py
git commit -m "feat(tools): 添加类型化工具注册表"
```

---

### Task 2: 共享限制、受限列表与行读取

**Files:**
- Create: `src/safefix/tools/filesystem.py`
- Modify: `src/safefix/tools/__init__.py`
- Create: `tests/unit/test_filesystem_tools.py`

**Interfaces:**
- Consumes: `ListFilesAction`、`ReadFileAction`、`AccessKind`、`ToolResult`、`WorkspaceBoundary`、T04 路径错误。
- Produces: `FilesystemLimits`、`ListFilesTool`、`ReadFileTool`；共享安全路径/错误/计时/UTF-8 helper 供 Tasks 3–4 使用。

- [ ] **Step 1: 写限制与 fixture 的失败测试**

创建 `tests/unit/test_filesystem_tools.py`：

```python
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
```

- [ ] **Step 2: 写列表行为的失败测试**

追加真实文件系统测试，至少包含以下完整断言：

```python
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
```

追加以下确定性边界测试：

```python
@pytest.mark.asyncio
async def test_list_files_reports_missing_and_non_directory_roots(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "plain.txt").write_text("x", encoding="utf-8")
    tool = ListFilesTool(boundary)

    missing = await tool.execute(
        ListFilesAction(id="list-missing", reason="inspect", path="missing")
    )
    non_directory = await tool.execute(
        ListFilesAction(id="list-file", reason="inspect", path="plain.txt")
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
        ListFilesAction(id="list-glob", reason="inspect", pattern="!")
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
        ListFilesAction(id="list-unicode", reason="inspect", pattern="**/*.py")
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
        ListFilesAction(id="list-link", reason="inspect")
    )

    assert result.success is True
    assert "escaped.txt" not in result.stdout_summary


@pytest.mark.asyncio
async def test_list_files_rejects_workspace_escape(
    boundary: WorkspaceBoundary,
) -> None:
    result = await ListFilesTool(boundary).execute(
        ListFilesAction(id="list-outside", reason="inspect", path="../outside")
    )
    assert result == ToolResult.failure(
        "list-outside", "PATH_DENIED", "path access is denied"
    )
```

- [ ] **Step 3: 写读取行为的失败测试**

追加：

```python
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
        ReadFileAction(id="read-2", reason="inspect", path="large.txt")
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
        ReadFileAction(id="read-3", reason="inspect", path="binary.bin")
    )

    assert result == ToolResult.failure(
        "read-3", "BINARY_FILE", "file is not valid UTF-8 text"
    )
```

追加读取边界和非披露测试：

```python
@pytest.mark.asyncio
async def test_read_file_handles_empty_and_past_eof_ranges(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "empty.txt").write_text("", encoding="utf-8")
    (workspace / "short.txt").write_text("one\ntwo\n", encoding="utf-8")
    tool = ReadFileTool(boundary)

    empty = await tool.execute(
        ReadFileAction(id="read-empty", reason="inspect", path="empty.txt")
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
    ).execute(ReadFileAction(id="read-u", reason="inspect", path="unicode.txt"))
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
        ReadFileAction(id="read-missing", reason="inspect", path="missing")
    )
    directory = await tool.execute(
        ReadFileAction(id="read-dir", reason="inspect", path="directory")
    )
    sensitive = await tool.execute(
        ReadFileAction(id="read-secret", reason="inspect", path=".env")
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
        ReadFileAction(id="read-link", reason="inspect", path="link.txt")
    )

    assert result == ToolResult.failure(
        "read-link", "PATH_DENIED", "path access is denied"
    )
```

使用真实目标和定向打开故障验证 `IO_ERROR` 非披露：

```python
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
            id="read-io", reason="inspect", path="private-name.txt"
        )
    )

    assert result == ToolResult.failure(
        "read-io", "IO_ERROR", "filesystem operation failed"
    )
    rendered = repr(result)
    assert "private-name" not in rendered
    assert "PRIVATE-FILE-CONTENT" not in rendered
    assert "PRIVATE-LOW-LEVEL-ERROR" not in rendered
```

该测试只替换文件打开边界，不 mock `WorkspaceBoundary`。过程控制异常的 traceback-local 清理由 Task 4 使用真实临时文件覆盖。

- [ ] **Step 4: 运行 RED 并确认只因 filesystem 模块不存在**

Run:

```powershell
python -m pytest tests/unit/test_filesystem_tools.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'safefix.tools.filesystem'`。

- [ ] **Step 5: 实现共享配置与安全 helper**

创建 `src/safefix/tools/filesystem.py`：

```python
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
    ToolResult,
)
from safefix.governance.paths import (
    PathOutsideWorkspace,
    SensitivePathDenied,
    SymlinkEscapeDenied,
    WorkspaceBoundary,
)


@dataclass(frozen=True, slots=True)
class FilesystemLimits:
    max_read_bytes: int = 65_536
    max_search_files: int = 1_000
    max_search_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if min(
            self.max_read_bytes,
            self.max_search_files,
            self.max_search_output_bytes,
        ) <= 0:
            raise ValueError("filesystem limits must be positive")
```

实现 `_normalize_ignored_directories()`，永久加入 `.git`，拒绝空值、绝对路径、反斜杠、`.`/`..` 分量；错误固定为 `ignored directories must be safe relative POSIX paths`。实现共享 `_relative_posix()`、GitWildMatch 编译、固定错误映射、单调计时和工具自身 frame 清理。不得用 `str(exc)`、`repr(action)` 或候选路径生成公开结果。

- [ ] **Step 6: 实现 ListFilesTool 与 ReadFileTool**

两个类都提供：

```python
@property
def action_type(self) -> type[object]:
    return ListFilesAction  # ReadFileTool 返回 ReadFileAction

async def execute(self, action: Action) -> ToolResult:
    if not isinstance(action, ListFilesAction):
        return ToolResult.failure(
            action.id, "UNSUPPORTED_ACTION", "tool does not support this action"
        )
    return await asyncio.to_thread(self._execute_sync, action)
```

实际实现必须清理 `action`、路径和文件内容引用，且普通异常在同步 helper 内转换为固定结果。列表使用确定性排序，遇到敏感/越界后代静默跳过，但直接根路径拒绝必须返回 `PATH_DENIED`。读取保持所选行原始换行，严格 UTF-8，先计算编码字节长度再决定成功或 `FILE_TOO_LARGE`。

- [ ] **Step 7: 更新导出并运行 GREEN**

在 `src/safefix/tools/__init__.py` 增加四个新导出：

```python
from safefix.tools.filesystem import FilesystemLimits, ListFilesTool, ReadFileTool
```

Run:

```powershell
python -m pytest tests/unit/test_filesystem_tools.py -q -p no:cacheprovider
python -m pytest tests/unit/test_tool_registry.py -q -p no:cacheprovider
python -m ruff check --no-cache src/safefix/tools tests/unit/test_filesystem_tools.py
python -m mypy --no-incremental src/safefix/tools tests/unit/test_filesystem_tools.py
```

Expected: 全部 PASS，静态检查 exit 0。

- [ ] **Step 8: 提交 Task 2**

```powershell
git add -- src/safefix/tools/__init__.py src/safefix/tools/filesystem.py tests/unit/test_filesystem_tools.py
git commit -m "feat(tools): 添加受限文件列表与读取"
```

---

### Task 3: 有界字面文本搜索

**Files:**
- Modify: `src/safefix/tools/filesystem.py`
- Modify: `src/safefix/tools/__init__.py`
- Modify: `tests/unit/test_filesystem_tools.py`

**Interfaces:**
- Consumes: Task 2 遍历/路径/UTF-8/截断 helper、`SearchTextAction`。
- Produces: `SearchTextTool`，结果格式 `relative/path.py:12:matching line`。

- [ ] **Step 1: 写字面搜索与格式的失败测试**

追加：

```python
from safefix.domain import SearchTextAction
from safefix.tools.filesystem import SearchTextTool


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
```

- [ ] **Step 2: 写三重限制与 UTF-8 边界失败测试**

追加并分别断言：

```python
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
```

追加搜索边界测试：

```python
@pytest.mark.asyncio
async def test_search_text_enforces_file_scan_limit(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    (workspace / "a.txt").write_text("hit\n", encoding="utf-8")
    (workspace / "b.txt").write_text("hit\n", encoding="utf-8")
    limits = FilesystemLimits(max_search_files=1)

    result = await SearchTextTool(boundary, limits=limits).execute(
        SearchTextAction(id="search-files", reason="find", pattern="hit")
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
        )
    )
    invalid = await tool.execute(
        SearchTextAction(
            id="search-invalid", reason="find", pattern="hit", file_glob="!"
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

    result = await SearchTextTool(
        boundary, ignored_directories=("build",)
    ).execute(SearchTextAction(id="search-skip", reason="find", pattern="hit"))

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
            id="search-binary", reason="find", path="binary.bin", pattern="hit"
        )
    )
    sensitive = await tool.execute(
        SearchTextAction(
            id="search-secret", reason="find", path=".env", pattern="hit"
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
        SearchTextAction(id="search-empty", reason="find", pattern="needle")
    )

    assert result.success is True
    assert result.stdout_summary == ""
```

为搜索增加定向打开故障测试：

```python
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
```

该测试不得 mock `WorkspaceBoundary` 或目录枚举。

- [ ] **Step 3: 运行指定 RED**

Run:

```powershell
python -m pytest tests/unit/test_filesystem_tools.py -q -p no:cacheprovider -k "search_text"
```

Expected: collection/import fails because `SearchTextTool` 尚未定义，或新增搜索测试以 `AttributeError/ImportError` 精确 RED；现有列表/读取测试保持 GREEN。

- [ ] **Step 4: 实现 SearchTextTool**

在 `filesystem.py` 增加 `SearchTextAction` 导入和 `SearchTextTool`。算法顺序固定：

1. `SEARCH` 解析请求根；
2. 编译 `file_glob`；
3. 确定性枚举候选普通文件，目录符号链接不下钻；
4. 每个候选以 `READ` 再解析并应用敏感边界；
5. 最多扫描 `max_search_files` 个通过 glob 的文件；
6. 严格 UTF-8 逐行字面匹配；
7. 按路径和行号稳定输出；
8. 在添加整条结果前检查 UTF-8 字节预算，任何限制触发只添加固定 `[truncated]`。

直接文件请求仍应用 `file_glob`；不匹配时成功返回空输出。直接文件无效 UTF-8 返回 `BINARY_FILE`，目录枚举中的无效 UTF-8 文件静默跳过。不得把 pattern 传给 `re`。

- [ ] **Step 5: 更新导出并运行 GREEN**

在 `__init__.py` 导出 `SearchTextTool`。

Run:

```powershell
python -m pytest tests/unit/test_filesystem_tools.py -q -p no:cacheprovider
python -m ruff check --no-cache src/safefix/tools tests/unit/test_filesystem_tools.py
python -m ruff format --check --no-cache src/safefix/tools tests/unit/test_filesystem_tools.py
python -m mypy --no-incremental src/safefix/tools tests/unit/test_filesystem_tools.py
```

Expected: 全部 PASS，静态检查 exit 0。

- [ ] **Step 6: 提交 Task 3**

```powershell
git add -- src/safefix/tools/__init__.py src/safefix/tools/filesystem.py tests/unit/test_filesystem_tools.py
git commit -m "feat(tools): 添加有界字面文本搜索"
```

---

### Task 4: 冻结摘要、原子替换与并发补丁

**Files:**
- Modify: `src/safefix/tools/filesystem.py`
- Modify: `src/safefix/tools/__init__.py`
- Modify: `tests/unit/test_filesystem_tools.py`

**Interfaces:**
- Consumes: `ApplyPatchAction`、Task 2 安全 helper、`WorkspaceBoundary(WRITE)`。
- Produces: `ApplyPatchTool`，成功时 `changed_files=(relative_posix_path,)`。

- [ ] **Step 1: 写陈旧摘要与替换次数的失败测试**

追加：

```python
from hashlib import sha256

from safefix.domain import ApplyPatchAction
from safefix.tools.filesystem import ApplyPatchTool


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
```

- [ ] **Step 2: 写成功、权限与原子故障失败测试**

追加测试：

```python
@pytest.mark.asyncio
async def test_patch_atomically_replaces_text_and_reports_relative_file(
    workspace: Path, boundary: WorkspaceBoundary
) -> None:
    target = workspace / "src" / "app.py"
    target.parent.mkdir()
    original = b"value = 1\n"
    target.write_bytes(original)
    action = ApplyPatchAction(
        id="patch-3",
        reason="fix",
        path="src/app.py",
        expected_sha256=sha256(original).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result.success is True
    assert result.changed_files == ("src/app.py",)
    assert result.stdout_summary == ""
    assert target.read_bytes() == b"value = 2\n"


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
    )

    result = await ApplyPatchTool(boundary).execute(action)

    assert result == ToolResult.failure(
        "patch-4", "IO_ERROR", "filesystem operation failed"
    )
    assert target.read_bytes() == original
    assert [path for path in workspace.iterdir() if path.name != "app.py"] == []
    assert "PRIVATE-LOW-LEVEL-ERROR" not in result.stderr_summary
```

在 POSIX 可用时断言替换前后 `stat.S_IMODE(target.stat().st_mode)` 不变；Windows 不依赖不可移植的 mode 位。

- [ ] **Step 3: 写二次摘要、并发和过程控制异常失败测试**

在测试文件顶部增加 `import asyncio` 和 `import os`。

追加并发、二次摘要和过程控制异常测试：

```python
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
    )

    with pytest.raises(type(interrupt)) as captured:
        await ApplyPatchTool(boundary).execute(action)

    assert captured.value is interrupt
    assert target.read_bytes() == original
    assert not list(workspace.glob(".safefix-*.tmp"))
    tb = captured.value.__traceback__
    component_locals: list[str] = []
    while tb is not None:
        if tb.tb_frame.f_code.co_filename.replace("\\", "/").endswith(
            "safefix/tools/filesystem.py"
        ):
            component_locals.extend(
                repr(value) for value in tb.tb_frame.f_locals.values()
            )
        tb = tb.tb_next
    assert component_locals
    for sentinel in ("value = 1", "value = 2", repr(original)):
        assert all(sentinel not in value for value in component_locals)
```

过程控制测试只检查 `safefix/tools/filesystem.py` frame；测试自身 closure 中的 interrupt 不计入组件非披露范围。追加普通边界测试：

```python
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
    wrong = ReadFileAction(id="wrong", reason="inspect", path="app.py")
    result = await ApplyPatchTool(boundary).execute(wrong)
    assert result == ToolResult.failure(
        "wrong", "UNSUPPORTED_ACTION", "tool does not support this action"
    )
```

- [ ] **Step 4: 运行指定 RED**

Run:

```powershell
python -m pytest tests/unit/test_filesystem_tools.py -q -p no:cacheprovider -k "patch"
```

Expected: import/属性失败，因为 `ApplyPatchTool` 尚未定义；已有列表、读取、搜索测试保持 GREEN。

- [ ] **Step 5: 实现进程内规范路径锁**

在 `filesystem.py` 定义模块级固定数量分片锁；索引只由规范绝对路径的 `os.path.normcase(os.path.abspath(path))` 的稳定摘要决定，不按用户原始字符串决定。锁覆盖完整的同步补丁临界区，并由 `with`/`finally` 保证过程控制异常也释放。

不同 `ApplyPatchTool` 实例必须共享同一锁表。同一文件的别名在 `WorkspaceBoundary.resolve()` 后映射到相同规范路径和锁。

- [ ] **Step 6: 实现安全原子补丁**

使用 `tempfile.mkstemp(prefix=".safefix-", suffix=".tmp", dir=target.parent)` 创建唯一临时文件。实现顺序必须严格遵守设计规格：

```python
current_bytes = target.read_bytes()
current_hash = sha256(current_bytes).hexdigest()
if not hmac.compare_digest(current_hash, action.expected_sha256):
    return ToolResult.failure(
        action.id, "STALE_FILE", "file changed since it was read"
    )
text = current_bytes.decode("utf-8")
if text.count(action.old_text) != action.expected_replacements:
    return ToolResult.failure(
        action.id, "PATCH_MISMATCH", "replacement count differs"
    )
replacement = text.replace(
    action.old_text, action.new_text, action.expected_replacements
).encode("utf-8")
```

写临时文件后 flush+fsync，复制原权限；替换前重新 `boundary.resolve(action.path, AccessKind.WRITE)`，要求仍为相同规范目标，再重新读取并用 `hmac.compare_digest` 比较最初摘要。只有全部成立才 `os.replace()`。

临时路径、文件句柄、Action、原始/替换字节和异常引用必须在所有返回/异常路径清理。不要让清理期 `OSError` 覆盖正在传播的 `KeyboardInterrupt/SystemExit`；普通清理失败在没有活动过程控制异常时映射为 `IO_ERROR`。

- [ ] **Step 7: 更新导出并运行完整 GREEN**

在 `__init__.py` 导出 `ApplyPatchTool`，并保持 `__all__` 与所有公开类型一致。

Run:

```powershell
python -m pytest tests/unit/test_filesystem_tools.py tests/unit/test_tool_registry.py -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider
python -m ruff check --no-cache src tests
python -m ruff format --check --no-cache src tests
python -m mypy --no-incremental src tests/unit/test_filesystem_tools.py tests/unit/test_tool_registry.py
python -m pip check
git diff --check
```

Expected: T08 聚焦测试和全量测试全部 PASS；Ruff、mypy、pip check、diff check 全部 exit 0。

- [ ] **Step 8: 自审范围并提交 Task 4**

确认 `git diff --name-status 7cfef95..HEAD` 只包含 Global Constraints 允许文件；确认所有临时测试文件均由 `tmp_path` 管理，源码没有未完成标记、没有未限定 `except BaseException`、没有 `str(exc)` 进入 ToolResult。

```powershell
git add -- src/safefix/tools/__init__.py src/safefix/tools/filesystem.py tests/unit/test_filesystem_tools.py
git commit -m "feat(tools): 添加安全原子补丁工具"
```

---

## Final Review and Process Evidence

四个 Task 均通过任务级规格/质量审查后：

1. 对分支起点到 HEAD 生成完整 review package；
2. 进行一次整分支规格、安全和代码质量审查；
3. Critical/Important 必须修复并复审，Minor 记录到 durable progress ledger；
4. 根代理从最终 HEAD 新鲜运行 Task 4 Step 7 的全部门禁；
5. 更新 `PLAN.md` T08 checkbox、Task Status Ledger 和 `AGENT_LOG.md`；
6. 过程记录单独提交：`docs(process): 记录 T08 实现与审查`；
7. 使用 `superpowers:finishing-a-development-branch` 呈现四个集成选项，推荐推送 `t08-filesystem-tools` 并创建目标 `main` 的 GitLab MR。
