# AGENT_LOG

本文件只记录课程要求的关键过程证据：task 编号与时间戳、Superpowers 技能、关键 prompt/context、subagent 产出或 commit、人工修改及原因、经验总结。普通命令与无关环境信息不记录。

正式记录从 `PLAN.md` 任务执行阶段开始。

## T01 — Package Foundation and Typed Domain Model

- 时间：2026-07-14 14:35–17:18 +08:00
- 分支 / MR：`codex/t01-domain-foundation` / [GitLab !1](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/1)
- Superpowers：`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`systematic-debugging`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：实现代理仅获得 T01 task brief、worktree 路径、基线 `1cabadb`、允许文件、严格 RED→GREEN、报告路径，以及“全部提交使用中文 Conventional Commits”的全局约束；评审代理获得同一 brief、实现报告与完整 `BASE..HEAD` 只读差异范围。
- 实现代理产出：`755a001 feat(domain): 添加 SafeFix 类型化领域模型`。生产代码前，指定测试因 `ModuleNotFoundError: safefix.domain` 精确 RED；初始 GREEN 为 2 passed，补齐领域契约后为 51 passed。
- 第一次独立合并评审：Critical 0、Important 1、Minor 1。Important 指出 `deadline_at`、`RunSnapshot.created_at/updated_at` 接受 naive datetime，违反 UTC 契约并会阻塞后续时间比较；Minor 指出 action 默认值和主要边界缺少回归保护。
- 评审修复：先运行评审测试得到 `25 passed, 6 failed`；6 个失败精确证明 naive datetime 未拒绝且非 UTC aware 值未规范化。修复提交为 `eb8f057 fix(domain): 强化 UTC 时间与动作边界验证`；目标 GREEN 为 `31 passed, 51 deselected`，完整测试为 82 passed。
- 第二次独立合并评审：`APPROVED`，Critical 0、Important 0、Minor 0；确认上次 UTC 与动作边界问题均关闭，未发现 T02–T17 接口阻塞。
- 根代理新鲜验证：Python 3.12.3；82 passed；Ruff check/format、mypy、pip check、`git diff --check` 全部 exit 0；过程文档提交前的实现差异仅含 `pyproject.toml`、`src/safefix/__init__.py`、`src/safefix/domain.py`、`tests/unit/test_domain.py`。
- 合并结果：MR !1 合并到 `main`，合并提交 `22067fd`；在主工作区重新创建 Python 3.12.3 `.venv` 后复跑为 82 passed，Ruff check/format、mypy、pip check 全部 exit 0。
- 人工裁决：批准 Hatchling src-layout 映射 `packages = ["src/safefix"]`；确认 `StopDecision.code: RunStatus`、`RunSnapshot.stop_reason: str | None`；仅对 SPEC 明确要求 UTC 的 3 个字段增加约束，避免扩大范围。学生随后将仓库目录改名为 `safefix-harness`，editable `.pth` 变为纯 ASCII，原 Windows GBK/中文路径冲突消失。
- 经验：构建清单必须显式声明发行名到 src 包的映射；“UTC 时间戳”和精确默认值/边界必须进入可执行测试，单靠字段列表不足以防止冷启动代理产生可运行但不兼容后续任务的模型。

## T02 — Strict YAML Configuration

- 时间：2026-07-15 15:45–17:44 +08:00
- 分支 / MR：`codex/t02-configuration` / [GitLab !2](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/2)
- Superpowers：`using-superpowers`、`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`dispatching-parallel-agents`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：实现代理 Wegener 仅获得 T02 task brief、工作树、基线 `95104ab`、三个允许文件、严格 RED→GREEN、中文 Conventional Commit 与报告契约；规格评审 Volta 和代码质量评审 Planck 分别获得同一 brief、实现报告和冻结的完整 `BASE..HEAD` 差异包，并保持只读。
- 恢复与实现：续接时三个目标文件已有未提交 WIP，且原代理和原始 RED 输出不可恢复；实现代理没有伪造历史，只证明基线不存在 `safefix.config`，并对恢复后发现的 `args` 精确类型、非法 UTF-8 转换和静态类型问题记录真实 RED→GREEN。实现提交为 `d238023 feat(config): 添加严格声明式配置`，初次全套验证为 128 passed。
- 第一轮独立评审：代码质量评审为 0/0/0；规格评审发现 Critical 1：`raise ConfigError(...) from exc` 会让完整 traceback 通过 Pydantic/YAML 原始异常链泄露秘密或源文本。
- 人工裁决与第一轮修复：学生确认全局“秘密不进入日志”约束优先于 brief 的 `raise ... from exc` 示例。修复先得到两项 traceback 测试失败，再于异常上下文外抛出纯净 `ConfigError`，提交 `f6998f5 fix(config): 阻断配置异常链泄密`；复审确认 cause/context 问题关闭，但代码质量评审进一步发现 Important 1：启用 `capture_locals` 时 traceback frame 的 `raw` 仍持有完整配置。
- 第二轮修复：先新增 capture-locals 回归测试并得到 1 failed，再在 non-mapping 与 Pydantic 两个错误出口抛错前删除 `raw`，提交 `c335360 fix(config): 清理异常帧敏感配置`；测试同时验证 sentinel 不泄露、唯一 loader frame 不含 `raw`，并排除测试自身局部变量假阳性。
- 最终独立复审：规格与代码质量评审均 `APPROVED`，Critical 0、Important 0、Minor 0；确认精确 schema、错误安全边界、示例配置、三文件范围以及 message/cause/context/frame-locals 四层非披露契约均满足。
- 根代理新鲜验证：异常安全聚焦测试 3 passed；完整测试 131 passed；Ruff check/format、mypy、pip check、`git diff --check` 全部 exit 0；工作树在过程文档修改前干净。
- 合并结果：MR !2 合并到 `main`，合并提交 `bf675b0`；主工作区快进后复跑异常安全测试为 3 passed、完整测试为 131 passed，Ruff check/format、mypy、pip check、`git diff --check` 全部 exit 0；已清理 T02 本地工作树与本地分支。
- 过程证据边界：T02 原始 WIP 前是否实际运行 pytest RED 无法证明，`PLAN.md` 对应 Gate checkbox 保持未勾选；该缺口不影响最终代码评审结论，但保留为课程过程可追溯性风险。
- 经验：安全错误不能只净化 `str(error)`；还必须审计异常 cause/context、默认 traceback 和 capture-locals frame。恢复任务时应先落盘 RED/GREEN 证据与实现报告，避免正确工作因会话中断失去过程证明。

## T04 — Canonical Workspace and Sensitive-Path Boundary

- 时间：2026-07-15 17:50–19:27 +08:00
- 分支 / MR：`codex/t04-path-boundary` / [GitLab !3](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/3)
- Superpowers：`using-superpowers`、`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`systematic-debugging`、`dispatching-parallel-agents`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：实现代理 Wegener 仅获得 T04 task brief、工作树、基线 `a3a6241`、三个允许文件、严格 RED→GREEN、中文 Conventional Commit 与报告契约；规格评审 Volta 和质量评审 Planck 分别获得同一 brief、实现报告和冻结的完整 `BASE..HEAD` 差异包，并保持只读。
- 实现过程：生产代码前，目标测试因 `ModuleNotFoundError: safefix.governance` 精确 RED；首个实现提交为 `91dbd88 feat(governance): 实现规范化工作区边界`，建立规范化工作区、词法与真实路径双重边界、GitWildMatch 敏感路径检查和三类公开拒绝异常。
- 第一轮独立评审：规格评审 0/0/0；质量评审提出 5 个 Important 与 1 个 Minor。工作区符号链接绝对别名、Windows 设备路径/ADS 和底层解析异常路径泄露三项成立并修复；根目录 LIST/SEARCH 的递归后代逐项检查裁定为 T08 控制器职责；文件系统大小写策略按 brief 明确要求的 `normcase` 保持不扩展。修复提交为 `94c0edd fix(governance): 强化 Windows 路径与异常边界`。
- 后续评审与修复：复审发现 `COM¹`/`LPT³` 等上标数字设备别名以及直接拒绝路径在 `capture_locals` 下仍可能暴露候选值，提交 `49895de fix(governance): 完善设备名与拒绝路径脱敏`，改用标准库保留名判断并统一为无路径内部失败码；再次复审发现内嵌 NUL 会由 `Path.resolve` 抛出未映射 `ValueError`，提交 `325aa1b fix(governance): 统一空字符路径失败映射`。
- 最终独立复审：规格与质量评审均批准合并，Critical 0、Important 0、Minor 1；确认 NUL、上标设备别名、cause/context、默认 traceback、`capture_locals` 和符号链接边界问题均关闭。保留的 Minor 是 Hypothesis 属性测试部分复用了生产比较思路且把拒绝都视为通过；确定性接受测试能防止“全部拒绝”，故不阻塞本任务。
- 根代理新鲜验证：异常脱敏专项 7 passed（33 deselected）；完整测试 171 passed；Ruff check/format、mypy、pip check、`git diff --check a3a6241..325aa1b` 全部 exit 0；过程文档修改前工作树干净。
- 合并结果：MR !3 合并到 `main`，合并提交 `10cd964`；主工作区快进后确认分支头 `dad25df` 已进入主线，复跑异常脱敏专项为 7 passed（33 deselected）、完整测试为 171 passed，Ruff check/format、mypy、pip check 全部 exit 0。
- 人工裁决：敏感路径对所有 `AccessKind` 一律拒绝；LIST/SEARCH 根目录本身允许，T08 必须对枚举出的每个后代再次调用边界；Windows 大小写比较严格遵循计划指定的 `normcase`，不引入超出任务范围的卷能力探测。
- 经验：Windows 路径安全测试必须覆盖设备命名空间、ADS、保留设备名及上标数字别名；错误脱敏最好让内部失败只携带枚举码，并在候选字符串离开公开异常帧后再映射为稳定异常类型。

## T03 — LLM Protocol, Scripted Mock and Action Parser

- 时间：2026-07-15 19:35–22:23 +08:00
- 分支 / MR：`codex/t03-llm-parser` / [GitLab !4](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/4)
- Superpowers：`using-superpowers`、`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`dispatching-parallel-agents`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：全新实现代理仅获得 T03 brief、隔离工作树、基线 `310f1ec`、六文件范围、严格 RED→GREEN、中文 Conventional Commit 和报告契约；规格与质量代理分别只读同一版冻结 `BASE..HEAD` 差异。延续 T02 人工裁决：全局秘密非披露优先于 brief 的 `raise ... from exc` 示例，T03 负责自身公开异常与 parser frame，调用者 locals/日志层由 T12/T13 继续落实。
- 初始实现：两份测试先因 `ModuleNotFoundError: safefix.llm` 与 `safefix.action_parser` 精确 RED；`762a073 feat(llm): 添加可注入模型接口与严格动作解析` 建立异步 LLM 协议、不可变 scripted mock、typed parser 与安全字段反馈，初始聚焦 9 passed、完整 180 passed。
- 第一轮评审与修复：评审复现深层 JSON `RecursionError` 和动态 extra-field location 两条泄密路径，并发现缺少 `INVALID_ACTION`、重复键与 `NaN/Infinity` 可绕过严格 JSON。`b322870 fix(llm): 强化动作解析安全边界` 分别以真实 RED 修复，聚焦增至 18、完整 189。
- 第二轮评审与修复：质量评审发现宽泛 `Exception` 会把内部 adapter 错误伪装成模型错误、全调用栈 capture-locals 结论超出组件边界，以及 5,000 校验错误把约 29k 输入放大为约 204k feedback。`fea9a51 fix(llm): 区分内部故障并限制解析反馈` 引入独立内部故障类型、将保证收窄到 parser frames，并把反馈限制为前 8 项加固定截断；聚焦 20、完整 191。
- 第三轮评审与修复：Python 3.12 的 5,000 位整数 decoder `ValueError`、adapter `RecursionError` 和 formatter 自身故障暴露了跨阶段误分类。`1e585fe fix(llm): 按解析阶段隔离异常分类` 用无数据 failure code 分离 decoder、adapter、formatter 边界；聚焦 23、完整 194。
- 中断路径修复：质量评审继续用 `KeyboardInterrupt` 验证“不吞 BaseException 但必须清理 locals”。`2be70e4 fix(llm): 清理中断路径敏感局部变量` 清理 parser/decoder/adapter helper；`c38f3bc fix(llm): 清理反馈格式化中断局部变量` 补齐 `_validation_feedback` 与 location helper，并保持同一中断对象原样传播。最终聚焦 25、完整 196。
- 最终独立复审：规格与质量评审均批准，Critical 0、Important 0、Minor 2；确认严格单 JSON object、typed Action、`INVALID_ACTION`、重复键/非标准常量拒绝、动态 location 净化、8 项反馈上限、阶段化输入/内部错误分类，以及普通异常和中断路径 parser-frame 非披露均满足。
- 根代理新鲜验证：T03 聚焦 25 passed；安全边界专项 16 passed（6 deselected）；完整测试 196 passed；Ruff check/format、mypy、pip check、`git diff --check 310f1ec..c38f3bc` 全部 exit 0；过程文档修改前工作树干净且差异仅含 brief 六文件。
- 合并结果：MR !4 合并到 `main`，合并提交 `8b34b10`；主工作区快进后确认分支头 `abc0fac` 已进入主线，复跑 T03 聚焦为 25 passed、安全边界专项为 16 passed（6 deselected）、完整测试为 196 passed，Ruff check/format、mypy、pip check 全部 exit 0。
- 延期 Minor：`ScriptedMockLLM` 接受裸 `str` 时会按字符形成脚本；当前 `ModelResponse` 已冻结但测试只显式修改 `ModelMessage`。两项不影响生产契约与合并，记录供后续测试清理。
- 后续约束：T12 AgentLoop、T13 provider 与日志/错误采集层不得用未脱敏的 `capture_locals` 记录调用者 frame；应禁用该选项或在持久化/上报前递归脱敏。
- 经验：不可信解析边界应按处理阶段而非异常类型分类；安全异常既要区分可重试输入错误与内部故障，也要覆盖 formatter 和 `BaseException` 原样传播时的 `finally` 局部变量清理。
