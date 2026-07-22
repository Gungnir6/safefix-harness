# T08 受限工作区文件工具与注册表设计

## 1. 目标与范围

T08 实现四个异步文件工具和一个按领域 Action 类型分发的工具注册表，使后续 AgentLoop 只能通过 T04 `WorkspaceBoundary` 在工作区内执行有界、可审计的文件读取与修改。

本任务新增：

- `src/safefix/tools/__init__.py`
- `src/safefix/tools/base.py`
- `src/safefix/tools/filesystem.py`
- `src/safefix/tools/registry.py`
- `tests/unit/test_filesystem_tools.py`
- `tests/unit/test_tool_registry.py`

现有 Action、`ToolResult`、`WorkspaceBoundary` 和配置模型保持不变。T08 不执行进程、不调用 LLM、不进行策略决策、不写审计事件，也不修改 T04 的路径匹配语义。T09 负责进程和 validator 工具，T12 负责在调用注册表前完成策略与审批编排。

## 2. 已批准的核心决策

### 2.1 单一职责工具

文件工具拆分为四个类：

- `ListFilesTool`
- `ReadFileTool`
- `SearchTextTool`
- `ApplyPatchTool`

每个类只处理一种结构化 Action，但都实现 `base.py` 的异步 `Tool` 协议。它们共享同一个 `WorkspaceBoundary` 和不可变 `FilesystemLimits`，不在工具内部重新解析字典或字符串动作。

### 2.2 构造期配置

`FilesystemLimits` 是冻结、带 slots 的 dataclass，默认值为：

- `max_read_bytes=65_536`
- `max_search_files=1_000`
- `max_search_output_bytes=65_536`

`max_read_bytes` 和 `max_search_files` 必须大于零；`max_search_output_bytes` 必须至少为 11 字节。11 字节是固定 `[truncated]` 标记的 UTF-8 长度，因此是同时保证固定截断标记和搜索输出硬预算所需的必要下界。调用方可在满足这些下界时注入更小的测试值或后续配置值；本任务不扩大 `SafeFixSettings` schema。

每个文件工具构造器还接收 `ignored_directories: tuple[str, ...] = (".git",)`。`.git` 无论调用方是否传入都永久忽略；其他条目是工作区相对 POSIX 目录路径，匹配该目录及其后代。条目必须是非空、非绝对、无 `..` 的规范相对路径。忽略目录只控制递归枚举，不替代敏感路径规则。

### 2.3 文本与模式语义

`read_file`、`search_text` 和 `apply_patch` 只接受严格 UTF-8。无效 UTF-8 统一返回 `BINARY_FILE`，不得用替换字符静默改写内容。

`ListFilesAction.pattern` 和 `SearchTextAction.file_glob` 使用 pathspec GitWildMatch。无法编译的模式返回 `INVALID_GLOB`。`SearchTextAction.pattern` 始终按字面文本匹配，不作为正则表达式执行。

## 3. 公共接口

### 3.1 Tool 协议

`base.py` 定义异步 `Tool` 协议。工具暴露其唯一 Action 类，并为领域 Action 返回 `ToolResult`：

```python
class Tool(Protocol):
    @property
    def action_type(self) -> type[object]: ...

    async def execute(self, action: Action) -> ToolResult: ...
```

具体工具在运行时确认 Action 类型；直接把错误的领域 Action 传给单一工具时，返回 `UNSUPPORTED_ACTION`。普通工具失败不抛异常，而是使用 `ToolResult.failure(...)`。

### 3.2 ToolRegistry

```python
class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None: ...
    def register(self, tool: Tool) -> None: ...
    async def dispatch(self, action: object) -> ToolResult: ...
```

注册表以精确 Action 类为键：

- 重复注册同一 Action 类时抛出固定消息的 `ValueError`；
- 合法领域 Action 没有对应工具时返回 `TOOL_NOT_FOUND`；
- 原始字符串、字典或其他对象抛出固定消息的 `TypeError`；
- 分发只调用匹配工具一次，不按 `type` 字符串或继承关系猜测。

注册表的公开异常不得包含原始输入、Action 内容、工具对象 repr 或内部映射内容。

## 4. 文件工具行为

### 4.1 共同规则

所有公开方法是异步接口。有界同步文件操作通过 `asyncio.to_thread()` 执行，避免阻塞 T12 的事件循环。耗时使用单调时钟计算并写入非负 `duration_ms`。

每次操作先解析请求根路径，并在遍历时对每个后代再次调用 `WorkspaceBoundary.resolve()`：目录使用 `LIST` 或 `SEARCH`，实际读取的文件使用 `READ`，补丁目标使用 `WRITE`。根目录允许不代表后代自动允许。

递归遍历不跟随目录符号链接。直接请求的文件符号链接只有在 T04 解析后仍位于工作区内且不敏感时才允许。输出路径一律使用工作区相对 POSIX 形式，按 Unicode 码点稳定排序，不输出绝对路径。

直接请求越界、符号链接逃逸或敏感路径时返回 `PATH_DENIED`。枚举根目录时遇到被拒绝的后代则静默跳过，避免泄露其存在和名称。

### 4.2 ListFilesTool

`ListFilesTool` 只输出普通文件，不输出目录。它跳过任意层级的 `.git` 目录以及构造器配置的忽略目录，在其余目录中按 GitWildMatch 过滤工作区相对路径。

结果最多包含 `ListFilesAction.limit` 个路径，以换行连接到 `stdout_summary`。如果仍有更多匹配项，在末尾添加固定 `[truncated]` 行；截断标记不计入 Action 的文件数量限制。

请求路径不存在返回 `NOT_FOUND`，不是目录返回 `NOT_DIRECTORY`。

### 4.3 ReadFileTool

`ReadFileTool` 读取包含首尾端点的 `start_line..end_line`，保持选中行原有换行符，不添加行号或绝对路径。超出文件末尾不是错误；空选择返回空字符串。

选中输出编码后超过 `max_read_bytes` 时返回 `FILE_TOO_LARGE`，不返回部分内容。请求路径不存在返回 `NOT_FOUND`，不是普通文件返回 `NOT_FILE`，选中内容无法严格解码为 UTF-8 返回 `BINARY_FILE`。

### 4.4 SearchTextTool

`SearchTextTool` 只扫描普通 UTF-8 文件，跳过忽略目录、敏感文件、被拒绝的符号链接和无效 UTF-8 文件。二进制后代不会使整个目录搜索失败；直接对一个二进制文件搜索时返回 `BINARY_FILE`。

候选文件先按 GitWildMatch `file_glob` 过滤，再逐行进行字面子串匹配。每个结果格式固定为：

```text
relative/path.py:12:matching line
```

单行移除结尾换行符但保留其他字符。结果首先受 `SearchTextAction.max_results` 限制，同时受 `max_search_files` 和 `max_search_output_bytes` 限制。任一限制触发时停止扫描，并在不拆分 UTF-8 字符的前提下添加固定 `[truncated]` 行。若一条完整结果本身无法放入输出限制，只返回截断标记。

请求路径不存在返回 `NOT_FOUND`；既不是普通文件也不是目录返回 `NOT_FILE`。

### 4.5 ApplyPatchTool

补丁操作执行以下顺序：

1. 以 `WRITE` 权限解析目标并确认其为普通文件；
2. 读取原始字节并使用 `hmac.compare_digest` 比较当前 SHA-256 与 `expected_sha256`；
3. 严格 UTF-8 解码，并验证 `old_text` 出现次数精确等于 `expected_replacements`；
4. 生成替换后的完整 UTF-8 字节；
5. 在同目录创建唯一临时文件，复制原文件权限，写入、flush 并 `fsync`；
6. 替换前重新解析目标并复核原始字节摘要；
7. 使用 `os.replace()` 原子替换目标，并清理临时文件。

摘要不符返回 `STALE_FILE`，替换次数不符返回 `PATCH_MISMATCH`，无效 UTF-8 返回 `BINARY_FILE`。任何失败不得修改原文件。成功结果的 `changed_files` 只包含一个工作区相对 POSIX 路径，输出摘要不包含文件内容。

模块维护按规范化目标路径分片的进程内锁，并在 `asyncio.to_thread()` 中覆盖完整的读取、验证和替换临界区。同一进程中，不同工具实例对同一文件的并发补丁最多一个成功；后续补丁必须观察新摘要并返回 `STALE_FILE`。跨进程编辑不承诺操作系统级 compare-and-swap，但替换前的第二次解析与摘要复核会检测已完成的外部修改，`os.replace()` 保证不会暴露半写文件。

## 5. 稳定错误与非披露

T08 使用以下稳定 `error_type`：

- `PATH_DENIED`
- `NOT_FOUND`
- `NOT_FILE`
- `NOT_DIRECTORY`
- `INVALID_GLOB`
- `BINARY_FILE`
- `FILE_TOO_LARGE`
- `STALE_FILE`
- `PATCH_MISMATCH`
- `IO_ERROR`
- `TOOL_NOT_FOUND`
- `UNSUPPORTED_ACTION`

公开错误消息是固定文本，不包含请求路径、Glob、搜索文本、补丁文本、文件内容、绝对路径、底层异常、敏感规则或工具 repr。普通 `OSError`、`UnicodeError`、pathspec 错误和 T04 路径错误都在组件边界内转换为 `ToolResult`。

`KeyboardInterrupt`、`SystemExit` 等过程控制异常原样传播，但在传播前必须关闭文件、释放锁、删除临时文件，并清除 T08 自身 traceback frame 中的 Action、原始输入、路径、搜索文本、补丁文本、文件字节和底层异常引用。T08 只保证自身 frame 的非披露；调用者不得用未脱敏的 `capture_locals` 持久化自己的敏感变量。

## 6. 测试策略

测试使用真实 `tmp_path`、真实 `WorkspaceBoundary` 和真实文件系统行为。只有原子替换故障使用定向 monkeypatch；不以 mock 调用次数代替文件后置条件。

### 6.1 文件工具

- 列表：GitWildMatch、稳定排序、数量限制、Unicode、任意层级 `.git`、自定义忽略目录、敏感后代、工作区外路径和目录符号链接；
- 读取：行范围、空文件、文件末尾、64 KiB 限制、Unicode、无效 UTF-8、敏感路径和文件符号链接；
- 搜索：字面匹配、GitWildMatch、Unicode、文件/结果/输出三重限制、二进制后代跳过、直接二进制失败和固定截断标记；
- 补丁：陈旧摘要、替换次数不符、无效 UTF-8、文件权限保留、成功 changed_files、替换前二次摘要检查、`os.replace` 失败、临时文件清理、过程控制异常和同文件并发最多一次成功。

### 6.2 注册表与安全边界

- 精确 Action 类注册和分发；
- 重复注册、缺失工具、原始字符串和字典拒绝；
- 工具只调用一次且返回值原样转发；
- 公开消息、cause/context 和 T08 traceback locals 不包含路径、动作内容、文件内容或底层异常。

### 6.3 完成门禁

- `python -m pytest tests/unit/test_filesystem_tools.py tests/unit/test_tool_registry.py -q -p no:cacheprovider`
- `python -m pytest -q -p no:cacheprovider`
- `python -m ruff check --no-cache src tests`
- `python -m ruff format --check --no-cache src tests`
- `python -m mypy --no-incremental src tests/unit/test_filesystem_tools.py tests/unit/test_tool_registry.py`
- `python -m pip check`
- `git diff --check`

## 7. 非目标与后续约束

- T08 不读取或修改 `.gitignore`；忽略目录由构造器显式提供。
- T08 不添加 YAML 配置字段；T12 可把未来配置值转换为 `FilesystemLimits` 和 `ignored_directories`。
- T08 不实现删除、移动、整文件任意写入、二进制补丁、正则搜索、进程工具或 validator。
- T09 注册进程工具时必须复用同一 `Tool`/`ToolRegistry` 契约，不得增加字符串命令分发旁路。
- T12 在调用 `ToolRegistry` 前仍必须完成 `PolicyEngine` 和 T07 审批；注册表本身不是授权边界。
