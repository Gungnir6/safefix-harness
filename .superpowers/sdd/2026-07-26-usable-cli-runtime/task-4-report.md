# Task 4 Report

## 状态

完成。确定性 Mock JSONL 现可通过安装后的 CLI 适配器驱动真实
`AgentLoop`，按顺序执行 list、read、初始验证失败、patch、验证成功和
finish。默认模式保留原项目并修改持久化隔离副本；`--in-place` 仅修改测试
创建的 disposable fixture；`--json` stdout 可直接由 `json.loads` 解析。

## 改动

- `examples/mock_repair.jsonl`
  - 新增六动作发行模板，保留受控 `{CALCULATOR_SHA256}` 占位符。
- `src/safefix/cli_runner.py`
  - `load_mock_actions` 以 bytes 限制 1 MiB，限制最多 1000 个非空动作。
  - 拒绝空脚本、非 UTF-8、非标准/无效 JSON、非对象行和未知占位符。
  - 仅允许 `expected_sha256` 使用固定 fixture token，并只对 workspace 内存在
    的目标文件计算 SHA-256；缺失、目录、绝对/相对越界和 symlink 越界均失败关闭。
- `src/safefix/agent_loop.py`
  - 普通验证和补丁后的配置验证成功只记录反馈并继续决策。
  - 只有 `FinishAction` 的配置验证器结果非空且全部成功才进入 `SUCCESS`；
    finish 验证失败继续作为反馈，审计失败仍立即失败关闭。
  - `FeedbackEngine` 的全局停止规则未修改。
- `pyproject.toml`
  - 将 Mock 模板 force-include 到
    `safefix/_fixtures/mock_repair.jsonl`。
- 测试
  - 新增完整隔离、原地、JSON 和 loader 边界 integration 测试。
  - 补充 AgentLoop 精确完成语义回归，并为直接受影响的既有 runtime 测试脚本
    添加显式 finish。
  - 分发元数据测试验证源模板和 packaged-resource 声明。

## RED / GREEN 证据

所有命令均使用
`C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe`，
并设置 `PYTHONPATH` 为当前 worktree 的 `src`。

1. Task 4 CLI/loader RED：
   - `pytest tests/integration/test_cli_run.py -q`
   - `11 failed`。
   - 三个 CLI 用例因模板不存在安全返回 2；loader 未实现 SHA 替换、空/坏 JSON/
     非对象/未知占位符拒绝、workspace 边界和大小/动作数限制。
2. 分发 RED：
   - `pytest tests/integration/test_distribution_metadata.py -q`
   - `1 failed, 6 passed, 1 warning`，缺少模板和 force-include。
3. 首轮实现后的真实循环 RED：
   - `pytest tests/integration/test_cli_run.py -q`
   - `2 failed, 9 passed`。
   - 审计只消费到 `patch-1`，证明补丁自动验证成功提前终止，未执行
     `validate-2`/`finish-1`；另一个失败是 Windows 文本 fixture 的换行差异。
4. AgentLoop 完成语义 RED：
   - 三个精确测试结果 `2 failed, 1 passed`。
   - 普通验证成功和补丁验证成功均未消费 finish；finish 失败后重试语义原本正确。
5. AgentLoop GREEN：
   - 同三个精确测试 `3 passed`。
   - 完整 `tests/integration/test_agent_loop.py`：`10 passed`。
6. 受影响 runtime RED/GREEN：
   - RED：`2 failed, 14 passed`，两个旧脚本在新契约下耗尽。
   - 为它们添加显式 finish 后 GREEN：`16 passed`。
7. 标准 JSON/占位符补充 RED/GREEN：
   - RED：`2 failed, 5 passed`，尚未拒绝 `NaN` 和小写连字符占位符。
   - GREEN：`7 passed`。
8. Task 4 integration GREEN：
   - `tests/integration/test_cli_run.py tests/integration/test_distribution_metadata.py`
   - `18 passed, 1 warning`。
9. 最终受影响合并回归：
   - Task 4 两文件、AgentLoop integration、runtime unit：`46 passed, 1 warning`。

warning 为既有 `StarletteDeprecationWarning`，来自 FastAPI TestClient/httpx
兼容提示。

## 最终验证

- `safefix-demo.exe all`
  - `guardrail: PASS`
  - `feedback: PASS`
  - `approval: PASS`
- `ruff check examples tests/integration src/safefix`
  - `All checks passed!`
- `mypy src`
  - `Success: no issues found in 33 source files`
- `git diff --check`
  - 通过。

未运行全套测试，遵循 Task 4 brief 和上级明确约束，仅运行 Task 4 与直接受影响
回归。

## 提交

- 提交信息：`test(cli): 验证完整隔离修复流程`
- 本报告随该提交写入；确切 SHA 记录在任务完成回执中。

## Concerns

- 完成语义修复触及 `AgentLoop` 及其直接测试，因为旧实现会在 finish 前提前
  `SUCCESS`，无法满足设计中“验证成功后再 finish”的明确契约；改动限制在该成功
  终止语义，失败、审批、审计和 demo 回归均已定向验证。
- 本任务未构建 wheel；fresh-install wheel 验证属于计划 Task 5。

## 审查修复轮次 1/5

仅处理两个 Important：

1. repair budget 改为动作级门禁。
   - 根因：`FeedbackEngine.should_stop` 没有动作上下文，却在
     `remaining_repairs == 0` 时全局终止，导致单次 patch 成功后无法执行 finish。
   - 修复：全局停止规则继续负责 step、time 和 no-progress；解析到新的
     `ApplyPatchAction` 后，AgentLoop 才检查剩余 repair 额度。零额度 patch 在工具
     调用前以 `BUDGET_EXCEEDED` 停止，read/validation/finish 不受阻止。
   - 测试准备阶段先发现 `repair_rounds=1` 必须配套
     `no_progress_rounds=1`，修正 fixture 后再确认有效 RED。
   - RED：三个精确用例 `3 failed`；GREEN：`3 passed`。
   - 覆盖单次 patch 用尽额度后 finish 成功，以及第二次 patch 已被模型产生但未进入
     ToolRegistry。
2. Mock placeholder 改为递归广义 brace 检测。
   - 根因：旧正则只匹配有限 ASCII 名称，且 `set` 丢失出现次数和字段位置。
   - 修复：递归扫描所有 JSON key/value 字符串中的任意 brace-delimited 片段；
     唯一允许值是顶层 `expected_sha256` 严格等于
     `{CALCULATOR_SHA256}` 且全对象只出现一次。SHA 替换后再次扫描并失败关闭。
   - RED：`5 failed, 7 passed`，四种绕过和重复合法 token 均被测试捕获。
   - GREEN：`12 passed`。

最终受影响回归：

- AgentLoop、feedback、runtime、Task 4 CLI integration 和分发元数据：
  `67 passed, 1 warning`。
- Ruff：`All checks passed!`
- mypy：`Success: no issues found in 33 source files`
- warning 仍为既有 `StarletteDeprecationWarning`。
- 按审查指令未重复三个 demo，未运行全套测试。
