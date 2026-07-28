# Task 3 Report

## 状态

完成。Task 3 的 CLI 参数、配置初始化、真实运行驱动、一次性审批交互、事件展示、稳定摘要、退出码和资源关闭均已实现，并通过任务指定的单元测试、WebUI 回归、Ruff 与 mypy。

## 改动

- `src/safefix/cli.py`
  - 为 `run` 增加安全默认值和 `Path` 类型参数：`--config`、`--data-dir`、`--in-place`、`--mock-script`、`--non-interactive`、`--json`。
  - 默认 `run` 路径构造 `CliRunOptions` 并委派 `run_cli`。
  - 保留显式注入 `task_service` 的原有窄适配器路径。
  - `config init` 使用完整保守模板和排他创建，拒绝覆盖已有文件；`config validate` 安全返回配置错误。
- `src/safefix/cli_runner.py`
  - 新增 `CliRunOptions`、`RunSummary`、`run_cli` 及要求的展示/审批辅助接口。
  - 使用一次 `asyncio.run` 驱动 `TaskService`，记录 run ID，增量读取审计事件，并循环处理审批暂停。
  - 非交互模式始终拒绝审批；审批 capability、CSRF、哈希和敏感事件字段不进入输出。
  - 人类输出提供隔离/原地警告、模型、事件、修改文件、工作区与审计数据库；JSON 模式只输出一份稳定摘要。
  - 配置、凭据/provider、工作区、取消、治理/审计/持久化错误映射到 `2/3/4/6/7`；终态失败映射到 `5`，成功映射到 `0`。
  - 所有已创建运行时均通过统一 `finally` 路径关闭。
- `tests/unit/test_cli.py`
  - 新增 parser 安全默认值、有效配置模板/拒绝覆盖、默认生产 runner 委派测试。
- `tests/unit/test_cli_runner.py`
  - 覆盖审批批准/拒绝、终态退出码、错误边界、模式 banner、事件增量/脱敏/截断、JSON 契约、run ID 元数据和资源关闭。

## RED / GREEN 证据

1. Parser/config RED：
   - 命令：`python -m pytest tests/unit/test_cli.py -q`
   - 结果：`2 failed, 4 passed`
   - 预期失败：`project` 仍为字符串；`config init` 模板缺少 `llm.model` 和 `validators`。
2. Parser/config GREEN：
   - 同一命令结果：`6 passed`；加入默认 runner 委派测试后为 `7 passed`。
3. Runner RED：
   - 命令：`python -m pytest tests/unit/test_cli_runner.py -q`
   - 结果：collection error，`ModuleNotFoundError: No module named 'safefix.cli_runner'`。
4. Runner GREEN：
   - 初次实现结果：`22 passed`。
5. 退出码/存储错误补充 RED：
   - 结果：`2 failed, 22 passed`。
   - 预期失败：审批拒绝仍返回 `5`；运行时 `OSError` 未映射到安全退出码。
6. 最终 GREEN：
   - `tests/unit/test_cli_runner.py`：`24 passed`。

以上所有命令均使用仓库根
`C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe`
并通过 `PYTHONPATH=<worktree>\src` 加载当前 worktree。

## 最终验证

- Pytest：
  - `tests/unit/test_cli.py tests/unit/test_cli_runner.py tests/web/test_api.py tests/web/test_pages.py -q`
  - `65 passed, 1 warning`
  - warning 为既有 `StarletteDeprecationWarning`（FastAPI TestClient 的 httpx 兼容提示）。
- Ruff：
  - `ruff check src/safefix/cli.py src/safefix/cli_runner.py tests/unit/test_cli.py tests/unit/test_cli_runner.py`
  - `All checks passed!`
- mypy：
  - `mypy src/safefix/cli.py src/safefix/cli_runner.py`
  - `Success: no issues found in 2 source files`

## 提交

- 预期提交信息：`feat(cli): 运行真实代理并处理审批`
- 本报告与实现包含在同一提交；确切 SHA 记录在任务完成回执中。

## Concerns

- `load_mock_actions` 在 Task 3 只提供运行驱动所需的基础逐行加载。Task 4 已明确负责 JSON 对象校验、占位符替换、1 MiB/1000 action 上限与空脚本拒绝，本任务未提前扩展这些后续职责。
- 未运行全套测试，按 brief 仅运行 Task 3 指定测试与 WebUI 回归。

## 审查修复轮次 1/5

- 审批一旦被 CLI 拒绝，即使真实运行随后返回 `SUCCESS` 或普通 `BLOCKED`，最终退出码仍为 `6`；治理、审计或持久化错误 `7` 保持更高优先级。
- JSON 交互审批将说明写入 stderr，并使用空 prompt 读取输入，确保 stdout 只有最终 JSON。
- 审批展示分别输出 JSON 编码的 program、args 和 reason，不再拼成 shell 命令；换行与 ANSI 控制字符保持转义。
- 同一事件批次先按 sequence 排序，并以动态 `last_sequence` 去重。
- RED：拒绝后的 `SUCCESS/BLOCKED` 分别错误返回 `0/5`；JSON stdout 被 prompt 污染；审批参数丢边界且控制字符直出；重复 sequence 输出两次。
- GREEN：四组定向测试分别为 `2 passed`、`1 passed`、`2 passed`、`1 passed`。
- 最终门禁：pytest `70 passed, 1 warning`；Ruff `All checks passed!`；mypy `Success: no issues found in 2 source files`。

## 审查修复轮次 2/5

- program/args 使用完整终端安全 JSON 编码展示，不再截断后请求批准；编码后的执行语义超过 4096 字符或结构无效时，CLI 不读取输入，自动 reject 并退出 `6`。
- reason 保留明确截断标记；program、args、reason 中的 Unicode `Cc` 与 `Cf` 字符显式转义，覆盖 U+009B 与 U+202E，同时普通中文保持可读。
- RED：超长危险尾部仍调用输入并进入 approve；U+009B/U+202E 原字符直接进入审批输出。
- GREEN：两组定向测试各 `1 passed`。
- 最终门禁：pytest `72 passed, 1 warning`；Ruff `All checks passed!`；mypy `Success: no issues found in 2 source files`。
