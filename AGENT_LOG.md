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

## T05 — Three-Level Policy Engine

- 时间：2026-07-16–2026-07-17 15:46 +08:00
- 分支 / MR：`codex/t05-policy-engine` / [GitLab !5](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/5)
- Superpowers：`using-superpowers`、`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`dispatching-parallel-agents`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：全新实现代理仅获得 T05 task brief、隔离工作树、基线 `6959692`、两个允许文件、严格 RED→GREEN、中文 Conventional Commits 和报告契约；续修代理只接收冻结审查问题与可复现输入。规格、安全、代码质量和整分支审查代理均只读同一版本的冻结 `BASE..HEAD` 差异包。
- 初始实现：生产模块创建前，聚焦测试因 `ModuleNotFoundError: safefix.governance.policy` 精确 RED；`44c625e feat(governance): 添加确定性动作策略分类` 建立三态决定、稳定 rule ID、文件边界映射、validator/配置程序与永久拒绝/审批顺序，首轮策略 GREEN 为 45 passed、全量为 241 passed。
- 安全评审修复：多轮独立审查用真实决策探针证明 basename 授权冒充、解释器与 shell 内嵌执行、根/系统路径别名、Git alias/分页/写出选项、包管理器变体、Windows 8.3 路径、凭据计算属性和 shell 引号状态等降级路径。修复提交依次为 `1a0d503 fix(governance): 收紧策略绕过防护`、`2204d34 fix(governance): 封堵嵌套命令策略绕过`、`3a640c8 fix(governance): 完善命令参数边界`、`224d696 fix(governance): 修正引号与Git分页边界`、`0da385a fix(governance): 修正Git可选分页参数语义`。
- 合并门禁修复：整分支审查发现配置允许的文件搬运可复制敏感源、表面只读 Git 仍可由仓库/全局配置触发外部执行；`6a6ff31 fix(governance): 收紧文件搬运与Git读取` 复用 `WorkspaceBoundary` 的敏感模式永久拒绝敏感搬运，普通搬运与所有非 validator Git 调用保守审批。
- 最终独立复审：规范、安全和整分支审查均 `APPROVED`，Critical 0、Important 0、Minor 0；确认永久拒绝优先级、精确 validator、安全授权身份、不透明执行默认审批、敏感文件搬运、Git 副作用和稳定脱敏决定均满足 T05。
- 根代理新鲜验证：Python 3.12.3；377 passed；Ruff check/format、mypy（`src` + T05 测试）、`git diff --check` 全部 exit 0；过程文档修改前工作树干净，最终实现差异仅含 `src/safefix/governance/policy.py` 与 `tests/unit/test_policy.py`。
- 合并结果：MR !5 合并到 `main`，合并提交 `ff7c17f`；主工作区快进后确认分支头 `63ac64a` 已进入主线，复跑完整测试为 377 passed，Ruff check/format、mypy、pip check 全部 exit 0。
- 人工裁决：`denied_programs` 永久拒绝；`allowed_programs` 只表示配置身份，不能越过更早风险；授权匹配与风险 basename 分离；无法可靠解析的 shell、解释器、未知 Git 和文件搬运至少审批；精确 validator 仅在永久危险检查后允许。未进行学生手工代码修改，所有生产与测试变更均由受约束子代理完成并经主代理验证。
- 经验：策略引擎不能依靠不断扩张的危险字符串黑名单；自动允许面必须由精确身份和明确安全执行形态定义，所有不透明行为保守审批。路径规范化、嵌套命令最高严重度聚合、敏感源搬运和 Git 外部扩展入口必须作为同一策略边界测试，而不是分别依赖后续执行器补救。

## T06 — Redacted Hash-Chained Audit Store

- 时间：2026-07-17 16:43–19:02 +08:00
- 分支 / MR：`codex/t06-audit-store` / [GitLab !6](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/6)
- Superpowers：`using-superpowers`、`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`dispatching-parallel-agents`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：全新实现代理仅获得 T06 task brief、隔离工作树、基线 `6679dbd`、两个允许文件、严格 RED→GREEN、SQLite 真实存储、秘密非披露、稳定异常、中文 Conventional Commits 与报告契约；规格、安全和整分支审查代理均只读相应版本的冻结差异包。实现同时兼容调用方现有 `sqlite3.Connection` 与零参 factory，不关闭调用方连接；普通 SHA-256 哈希链是计划锁定算法，不擅自替换为 HMAC。
- 初始实现：生产模块创建前，指定篡改测试因 `ModuleNotFoundError: safefix.governance.audit` 精确 RED；`65f54d0 feat(audit): 添加脱敏防篡改审计日志` 建立递归 payload 脱敏、canonical JSON、UTC 时间、逐 run sequence、SHA-256 previous-hash 链、SQLite schema、append/list/verify 和稳定 `AuditUnavailable`。初始聚焦 29 passed、全量 406 passed。
- 第一轮独立审查与修复：规格审查发现坏链仍可继续 append；安全审查发现 metadata secret 可落库、list 不验证完整链、共享连接并发 SAVEPOINT 可让成功事件消失、SQLite 动态类型可被 `int()`/`str()` 掩盖，以及占位符可能重新包含 configured secret。根代理定向探针逐项复现后，`c1e04c4 fix(audit): 收紧审计链与并发安全` 引入共享严格链 snapshot、metadata fail-closed、原始 SQLite 类型校验、按 connection identity 的 striped `RLock`、唯一 SAVEPOINT、外层事务 sentinel 与真实并发测试；全量增至 440 passed。
- 第二轮安全审查与修复：复审发现读取恶意 canonical payload 时未最终扫描 configured secret，并用自定义 `sqlite3.Connection.execute()` 回调复现同线程重入 append 内层成功后被外层回滚。`9067a95 fix(audit): 阻止重入写入与读取泄密` 增加读取端最终秘密检查和 thread-local connection-id 重入守卫，覆盖同 store、同连接双 store 及 `BaseException` 后守卫恢复；全量增至 445 passed。
- 整分支宽审与修复：宽审用真实 `AFTER INSERT DELETE/UPDATE` trigger 证明 append 可在新行已消失或被改写时返回成功，并指出 SAVEPOINT 后 `KeyboardInterrupt/SystemExit` 会绕过清理。`12ce19b fix(audit): 校验写入后置条件并清理中断` 在 RELEASE 前重读完整链并严格比对候选事件，以异常类型无关的 finally 清理 SAVEPOINT，同时让进程控制异常原样传播；全量增至 451 passed。
- 最终独立复审：规格、安全和整分支审查均批准，Critical 0、Important 0、Minor 2。保留 Minor 为 list/verify 的只读重入范围尚未统一拒绝，以及 append 为保证坏链 fail-closed 每次重验完整 run，长链累计 O(N²)；两项不破坏当前安全契约，留待出现容量需求或统一重入策略时处理。
- 根代理新鲜验证：Python 3.12.3；T06 聚焦 74 passed；完整测试 451 passed；Ruff check/format、mypy（`src` + T06 测试）、`git diff --check 6679dbd..12ce19b` 全部 exit 0；过程文档修改前工作树干净，最终实现差异仅含 `src/safefix/governance/audit.py` 与 `tests/unit/test_audit.py`。
- 合并结果：MR !6 合并到 `main`，合并提交 `8699d37`；主工作区快进后确认 `12ce19b` 已进入主线，复跑 T06 聚焦为 74 passed、完整测试为 451 passed，Ruff check/format、mypy、pip check、`git diff --check` 全部 exit 0。
- 人工裁决：所有外部 metadata/payload 只要可能携带 configured secret 就稳定 fail closed；调用方事务所有权通过内部 SAVEPOINT 保留，T07 仍必须把审计 append 失败视为拒绝条件。未进行学生手工代码修改，所有生产与测试变更均由受约束子代理完成并经根代理验证。
- 经验：防篡改审计不能只在读取时提供可选 verify；append 自身必须验证旧链和 INSERT 后置条件。SQLite trigger、动态类型、共享连接并发、同线程重入和进程控制中断都会制造“API 返回成功但审计记录不存在”的路径，必须用真实 SQLite 行为而非 mock 调用次数锁定。

## T07 — Persistent HITL Approval State Machine

- 时间：2026-07-17–2026-07-18 +08:00
- 分支 / MR：`t07-approval-state-machine` / [GitLab !7](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/7)。
- Superpowers：`using-superpowers`、`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`systematic-debugging`、`dispatching-parallel-agents`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：实现代理仅获得 T07 task brief、隔离工作树、基线 `5ff7a83`、两个允许的生产/测试文件、冻结动作、单次 token、SQLite 持久化、T06 审计失败必须阻止状态转换、秘密非披露、中文 Conventional Commits 与报告契约；规格、安全和整分支审查代理均只读相应版本的冻结 `BASE..HEAD` 差异包。
- 初始实现与审查修复：`a953456 feat(approval): 添加持久化审批请求` 建立只保存 SHA-256 token digest 的持久化请求；后续提交 `06995fd`、`4be5161`、`bb86c20`、`da73da4`、`503abb2`、`f5e45d8` 依次收紧公开错误边界、SQLite trigger/SAVEPOINT 竞态、进程控制异常清理、冻结动作单次批准、终态持久化和跨 store 并发事务语义。
- 秘密与异常边界修复：独立审查发现公开 request/approve/reject/get/cancel 的 traceback locals 可能保留 token、action 或标识符；`95173fc fix(approval): 隔离敏感错误帧` 以无敏感数据的 outcome 在安全帧外映射稳定公开异常。整分支审查继续发现构造器 frame 可能保留 configured secrets，且坏审计 schema 的 T06 `AuditUnavailable` 会越过 T07 边界；`a92e61b fix(approval): 隔离构造期秘密与错误` 将构造期初始化也纳入同一非披露和稳定异常契约。
- 最终独立复审：规格、安全和整分支审查均批准合并，Critical 0、Important 0、Minor 1；确认冻结 action JSON 与 `action_digest` 双重校验、`hmac.compare_digest`、`PENDING -> APPROVED|REJECTED|EXPIRED|CANCELLED` 单向终态、审批与审计同一逻辑操作、失败回滚、SQLite 动态类型/trigger 后置条件、重入/并发和 traceback-local 非披露均满足 T07。
- 根代理新鲜验证：最终 HEAD `a92e61b`；T07 聚焦 82 passed；完整测试 533 passed；Ruff check、Ruff format、mypy（13 source files）、pip check、`git diff --check` 全部 exit 0；过程文档修改前工作树干净，最终实现差异仅新增 T07 设计/计划、`src/safefix/governance/approvals.py` 与 `tests/unit/test_approvals.py`。
- 合并结果：MR !7 合并到 `main`，合并提交 `d19aa5a`；主工作区 fast-forward 后确认分支头 `ae96a21` 已进入主线，复跑 T07 聚焦为 82 passed、完整测试为 533 passed，Ruff check/format、mypy、pip check 全部 exit 0。
- 延期 Minor：当前 CPython 环境中，两份文件连接的并发测试会映射到同一 striped lock，已验证生产 Python 锁序列化和最多一次成功，但没有直接覆盖不同 stripe 下的 SQLite 文件锁竞争。避免为测试引入白盒锁控制、多进程不稳定性或平台相关假设，留待并发基建统一时补充。
- 人工裁决：严格保留调用方共享连接和事务所有权，内部使用 SAVEPOINT；只持久化 token 的 SHA-256 digest；拒绝不保存自由文本，用户可见反馈由 T12 生成；cancel 是可信控制面操作；审批公开 API 的普通异常、构造异常和 `capture_locals` 均不得披露 token、action、configured secrets 或数据库内部细节。
- 经验：审批安全不是一次 `UPDATE`；必须同时冻结授权对象、保证 token 单次消费、把审计成功纳入提交条件，并在 INSERT/UPDATE trigger、共享连接、跨 store 重入、进程控制异常和异常帧 locals 下证明“失败即无状态变化”。构造器也属于公开安全边界，不能只审计业务方法。

## T08 — Bounded Filesystem Tools and Registry

- 时间：2026-07-18–2026-07-22 +08:00
- 分支 / MR：`t08-filesystem-tools` / [GitLab !8](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/8)；合并提交 `a1a5923`。
- Superpowers：`using-superpowers`、`using-git-worktrees`、`brainstorming`、`writing-plans`、`subagent-driven-development`、`test-driven-development`、`systematic-debugging`、`receiving-code-review`、`requesting-code-review`、`verification-before-completion`。
- 关键 prompt/context：四个实现代理分别只获得对应 task brief、隔离 worktree、允许文件、冻结依赖接口、严格 RED→GREEN、中文 Conventional Commits 与报告路径；任务评审和最终整分支评审均只读明确的 `BASE..HEAD` 冻结差异包。全局约束包括 T04 路径边界、固定错误、cause/context/traceback-locals 非披露、过程控制异常原样传播、Windows symlink/junction 语义及禁止引入策略、审批、审计、LLM 或进程行为。
- 设计与计划：`d2e8f3c docs(design): 明确 T08 文件工具设计`、`e3f9892 docs(plan): 细化 T08 文件工具实施`。设计冻结 Tool/Registry、List/Read/Search/Patch、`FilesystemLimits`、GitWildMatch、严格 UTF-8、三重搜索限制、进程内规范路径锁及同目录原子替换；最终澄清固定 `[truncated]` 要求 `max_search_output_bytes >= 11`。
- Task 1：`789cf99 feat(tools): 添加类型化工具注册表`。生产代码前精确 RED 为缺少 `safefix.tools.registry`；实现精确运行时类型注册与 dispatch、缺失工具固定失败及 raw 输入非披露。最终整分支修复 `3003aa2` 又覆盖 constructor/register 重复、非法 `action_type`、property 故障与 KBI/SystemExit 的完整异常链和 frame-locals 清理。
- Task 2：`be27bfa` 起实现受限 List/Read，后续 `a334030`、`1fa87d8`、`193d31d`、`2649f5e`、`b10688c`、`8f3518c` 依次关闭 resolve 竞态、原始 CRLF/字节预算、ignored 根与 Windows 大小写、目录 symlink/junction 祖先、configured/canonical alias、异常 helper/constructor/async frame 泄露和复杂 `..` 绕过。最终统一拒绝任意独立 `..` 分量，并保留设计允许的安全直接文件 symlink。
- Task 3：`41ec080 feat(tools): 添加有界字面文本搜索`；`9094a6f`、`198d549`、`1451043` 修正实际扫描文件计数、固定 marker 预算、搜索专属路径/异常测试、安全直接文件链接与链接祖先判断顺序。字面匹配、确定输出、READ 后代检查、敏感/二进制跳过及文件数/结果数/输出字节三重限制通过任务审查。
- Task 4：`5fe9ed3 feat(tools): 添加安全原子补丁工具`；`3aaa67e` 补全 raw 临时文件名清理、确定性 canonical/alias 并发证明、过程控制后锁复用和 14 阶段 × OSError/KBI/SystemExit 的 42 项故障矩阵。实现共享 64 分片规范路径锁、两次 WRITE resolve/摘要复核、严格 UTF-8、精确替换次数、权限复制、flush/fsync、预构造成功结果及 `os.replace` 提交点。
- 最终整分支审查：首轮提出搜索预算文档冲突、List 特殊文件枚举和 Registry 注册非披露；句柄级 TOCTOU 与 eager-read 内存问题经绑定设计复核降为未来硬化建议。`3003aa2 fix(tools): 完成 T08 最终安全审查` 以真实 file symlink、换型 race、FIFO/socket 平台测试和 Registry 全链测试关闭全部 Important；第二轮整分支审查结论 Ready to merge，Critical 0、Important 0、Minor 2。
- 根代理新鲜验证：最终 HEAD `3003aa2`；T08 聚焦 215 passed、2 skipped；完整测试 748 passed、2 skipped；Ruff check/format、mypy（18 source files）、pip check、`git diff --check` 全部 exit 0。两个 skip 是当前 Windows 不支持创建 FIFO/socket，支持平台会实际执行对应测试。
- 过程与人工裁决：`d3a5365 style(tests): 统一工具注册表格式` 单独修复全树 Ruff 基线门禁。人工裁决采用保守的任意 `..` 拒绝；固定 marker 的 11 字节下界进入设计；直接内部普通文件 symlink 在 resolve 后允许，目录祖先链接始终拒绝；未把未批准的 POSIX `openat`/Windows `CreateFile` 句柄架构加入 T08。未进行学生手工代码修改。
- 延期项：文件工具仍读取 `WorkspaceBoundary._configured_root` 私有字段，待 governance 任务提供只读 accessor；handle-relative I/O 与 eager-read/候选集合内存优化属于未来设计级硬化，不是当前 T08 合并门槛。
- 经验：文件工具的“有界”必须同时明确结果、扫描和固定标记的契约；路径安全不能只检查最终 canonical 路径，还要区分目录祖先链接与允许的直接文件链接。原子补丁的可信度来自可强制交错的并发测试和全过程故障矩阵，而不是一次顺序运行恰好得到预期结果。

## T09 — Structured Process and Validator Runner

- 时间：2026-07-22 +08:00；分支 / MR：`t09-process-runner` / [GitLab !9](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/9)。
- 实现与合并：`5991852 feat(tools): 添加结构化验证进程执行`；合并提交 `0ed3b1e`。实现无 shell 的结构化进程调用、超时与输出限制、validator 查找及稳定错误映射。
- 验证：恢复审计时在原分支新鲜复跑为 755 passed、2 skipped。按学生要求采用快速交付模式，未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用工作树、TDD、验证与分支收尾流程。结构化 argv 和显式 validator 配置是避免 shell 注入及命令漂移的关键边界。

## T10 — Deterministic Feedback and Progress Sensor

- 时间：2026-07-22 +08:00；分支 / MR：`t10-feedback-engine` / [GitLab !10](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/10)。
- 实现与合并：`4dfd319 feat(feedback): 添加验证反馈与停机判断`；合并提交 `95d6efd`。实现验证结果分类、失败指纹、进展判定和确定性停止条件。
- 验证：恢复审计时在原分支新鲜复跑为 769 passed、2 skipped。快速交付模式下未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用工作树、TDD、验证与分支收尾流程。停机依据必须来自预算和客观反馈，而不是模型自我判断。

## T11 — Project Memory and Run Persistence

- 时间：2026-07-22 +08:00；分支 / MR：`t11-persistence` / [GitLab !11](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/11)。
- 实现与合并：`4b691f2 feat(storage): 持久化项目记忆与运行状态`；合并提交 `5cceb3c`。实现有界项目记忆、确定性检索、运行快照、版本冲突和状态转换持久化。
- 验证：恢复审计时在原分支新鲜复跑为 778 passed、2 skipped。快速交付模式下未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用工作树、TDD、验证与分支收尾流程。记忆只保存受控摘要，运行存储通过乐观版本锁避免静默覆盖。

## T12 — Context Builder and Custom AgentLoop

- 时间：2026-07-22 +08:00；分支 / MR：`t12-agent-loop` / [GitLab !12](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/12)。
- 实现与合并：`d8a2f9a feat(agent): 实现 SafeFix 智能体主循环`；合并提交 `4867bc3`。实现有界上下文、显式动作循环、策略/审批暂停恢复、工具派发、反馈修复和预算停机。
- 验证：恢复审计时在原分支新鲜复跑为 784 passed、2 skipped。快速交付模式下未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用工作树、TDD、验证与分支收尾流程。循环必须在每个动作前治理、每个工具结果后记录客观反馈，并让审批成为真实暂停状态。

## T13 — Credential Lifecycle and OpenAI-Compatible Call

- 时间：2026-07-22 +08:00；分支 / MR：`t13-credentials-provider` / [GitLab !13](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/13)。
- 实现与合并：`7f19825 feat(credentials): 安全管理凭据与模型调用`；合并提交 `c0e84c7`。实现 Keyring、显式 secret 文件、风险提示 `.env` 来源及单次 OpenAI-compatible HTTP 调用。
- 验证：8 个聚焦测试通过；合并请求前全量 792 passed、2 skipped，Ruff 通过。快速交付模式下未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用工作树、TDD、验证与分支收尾流程。凭据只在请求边界取出，状态、异常、日志和响应均不得包含明文。

## T14 — Task Service, FastAPI Endpoints and CLI

- 时间：2026-07-22 +08:00；分支 / MR：`t14-api-cli` / [GitLab !14](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/14)。
- 实现与合并：`35daee5 feat(interface): 添加 API 与命令行入口`；合并提交 `a72afc9`。实现共享 TaskService、公开模式输入边界、稳定 API 错误、HttpOnly 审批能力与 CSRF、记忆/凭据端点和五组 CLI 命令。
- 验证：12 个聚焦测试通过；合并请求前全量 800 passed、2 skipped，Ruff 通过。快速交付模式下未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用工作树、TDD、验证与分支收尾流程。HTTP 与 CLI 都应只做输入/输出适配，业务状态统一通过 TaskService。

## T15 — Local and Public WebUI

- 时间：2026-07-22 +08:00；分支 / MR：`t15-web-ui` / [GitLab !15](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/15)；合并提交 `583c15b`。
- 实现：`a6b9384 feat(web): 添加透明可审计的 Web 界面`。采用工业化开发者控制台视觉方向，实现本地/公开任务页、类型化审计时间线、审批/取消控制、设置与记忆页，以及响应式与键盘可访问样式。
- 验证：9 个页面/API 聚焦测试通过；全量 805 passed、2 skipped，Ruff、DOM 注入扫描和静态资源 200 探针通过。快速交付模式下未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用 `frontend-design`、工作树、TDD、验证与分支收尾流程。服务端模板依赖自动转义，动态模型/工具文本只通过 `textContent` 插入。

## T08–T15 Documentation Recovery

- 时间：2026-07-22 +08:00。
- 触发：学生指出多次任务完成后没有同步过程文档。
- 根因：快速交付流程把完成条件错误地收窄为实现、测试、提交、推送和 MR，没有把 `PLAN.md` 勾选/状态账本及 `AGENT_LOG.md` 证据作为提交前门禁；因此同一遗漏从 T09 连续重复到 T15，T08 的 MR/合并状态也未回填。
- 修复：依据 Git 历史、MR 编号和新鲜分支测试补齐 T08–T15；未发生的独立评审明确记录为 quick-delivery caveat，不伪造证据。自 T16 起，提交前固定检查任务步骤、状态账本、实现哈希、MR/合并状态和 Agent Log。

## T16 — Embedded Fixture and Deterministic Mechanism Demos

- 时间：2026-07-22 +08:00；分支 / MR：`t16-mechanism-demos` / [GitLab !17](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/17)，已合并。
- 实现：`0e5afc5 feat(demo): 添加确定性 Harness 机制演示`。新增内置错误 Python fixture，并提供 `python -m safefix.demo guardrail|feedback|approval|all`。
- 机制证据：guardrail 通过真实 `PolicyEngine` 永久拒绝提权破坏动作且工具调用为零；feedback 通过 `ScriptedMockLLM`、`ApplyPatchTool`、`ProcessTool`、`ValidatorRunner` 和 `FeedbackEngine` 产生失败→错误补丁→失败→正确补丁→通过；approval 通过 SQLite 持久化重开 `ApprovalStateMachine`，证明变更动作和 token replay 均被拒，仅冻结动作执行一次。
- 隔离：每个场景复制 `examples/python_bug` 到独立临时目录，结束后删除；嵌入 fixture 字节保持不变；无网络和真实凭据。
- 验证：6 个 T16 聚焦测试通过；`all` 连续三次均精确输出三个 PASS；全量 811 passed、2 skipped；Ruff 和 `git diff --check` 通过。快速交付模式下未执行独立子代理评审；没有学生手工代码修改。
- 技能 / 经验：使用 `using-superpowers`、`brainstorming`、`using-git-worktrees`、`test-driven-development`、`verification-before-completion` 和 `finishing-a-development-branch`。文档更新已作为本任务固定收尾门禁执行。

## T17 — Distribution, CI, README and Public-Demo Release

- 时间：2026-07-22–2026-07-26 +08:00；分支：`t17-distribution-ci-docs`；迁移前 [GitLab !18](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/18)，当前主仓库为 [GitHub](https://github.com/Gungnir6/safefix-harness)，实现已进入 `main`。
- 实现：`3c405c9 build(distribution): 添加可复现分发与持续集成`。新增可复现 wheel/sdist、非 root Python 3.12 Docker 镜像、GitLab/GitHub CI、Render Blueprint、完整 README、`/health`、默认公开 mock Web 服务及打包后的演示 fixture。
- 回归修复：发现 T16 同一秒内连续写入等长源码可能命中旧 `.pyc`；将中间错误补丁改为不同长度，三个场景连续三轮均 PASS。
- 验证：T17 RED 为 6 failed，GREEN 为 6 passed；全量 817 passed、2 skipped；Ruff、mypy、`git diff --check` 通过；wheel 与 sdist 构建成功并核验模板、静态资源和 fixture 均在 wheel 内。
- 外部状态：Render 实际部署仍等待仓库所有者账户授权；没有伪造在线 URL。
- CI 修复：迁移前 GitLab Runner 因 Docker Hub 拉取及管理员 `always` 策略失败，保留配置改走 Dependency Proxy。迁移 GitHub 后，`8f86cb9` 将仅适用于 Windows 的 `.EXE/.CMD` 测试限定到 Windows，`61eb61a` 将 Ruff 固定在已验证的 `<0.16`，`b259161` 将 GHCR 镜像标签改为全小写。
- GitHub 验证：[Actions #30192558314](https://github.com/Gungnir6/safefix-harness/actions/runs/30192558314) 在提交 `b259161` 上全部通过：Linux 801 passed、19 skipped；Ruff 通过；mypy 对 30 个源码文件无问题；Gitleaks 未发现秘密；非 root Docker 镜像构建成功。
- 首次人工体验修复：从 WebUI 启动 `feedback` 场景稳定返回 409 `INVALID_STATE`。根因是异步 FastAPI 路由直接调用内部使用 `asyncio.run()` 的同步演示，形成嵌套事件循环；新增真实 `PublicDemoService` API 回归测试，并通过 `asyncio.to_thread()` 隔离同步演示。修复后真实 HTTP 请求返回 `SUCCESS`，完整测试 819 passed、2 skipped，Ruff 与 mypy 通过。
- 模式：按学生要求采用快速交付，不执行独立子代理评审；文档已在提交与推送阶段同步更新。

## WebUI 中文引导演示 — Task 3

- 时间：2026-07-26 +08:00；分支：`webui-chinese-guided-demo`；隔离工作树：`.worktrees/webui-chinese-guided-demo`。
- 实现：补充六种稳定错误码的中文解释并保留机器码；统一 JavaScript 与服务端状态/事件中文映射；轮询同步更新中文主状态、机器码和时间线；以事件序号去重，并用 `createElement`/`textContent` 构建与服务端同构的事件序号、中文标签、摘要和技术细节；补齐三种凭据来源中文名称及未知来源原值回退。
- TDD 证据：先新增实际页面响应解析与可执行 JavaScript 映射测试。首轮页面 RED 为 4 failed、9 passed，精确暴露缺少 `data-sequence`、机器码 DOM 目标以及 `secret-file`/`env-file` 中文名称；JavaScript 映射 RED 因 `explainError` 尚不存在而失败。完成最小实现后页面回归为 14 passed。
- 文档：README 分别说明 CLI 与 WebUI、三个公开演示场景、`127.0.0.1:8000` 本地启动访问方式，并明确未提供虚构公网地址；PLAN 同步勾选本任务交付项。
- 验证：`python -m pytest tests/web/test_pages.py tests/web/test_api.py -q` 为 20 passed、1 条第三方 `StarletteDeprecationWarning`；`python -m ruff check src/safefix/web tests/web` 为 `All checks passed!`；`rg -n "innerHTML|insertAdjacentHTML|document\.write" src/safefix/web` 无匹配（预期退出码 1）；`git diff --check` 退出码 0。
- 技能 / 约束：使用 `executing-plans`、`test-driven-development`、`verification-before-completion`；严格使用仓库根 `.venv`，未调用系统 `python.exe`，未合并、推送或删除工作树。

## WebUI 中文引导演示 — 最终整分支审查修复

- 时间：2026-07-26 +08:00；分支 / 工作树：`webui-chinese-guided-demo` / `.worktrees/webui-chinese-guided-demo`。
- 实现提交：`b7c09d9dc9e610184283a82c2b0adf10c75fe108`（`fix(web): 对齐演示文案与真实审计语义`）。
- 修复：安全边界卡改为真实的提权破坏命令策略拒绝与零工具调用语义；公开设置页明确使用无需凭据的确定性 Mock 且不查询本地凭据；反馈演示为错误补丁后的再次失败使用独立事件码和准确摘要；服务端与 JavaScript 补齐 `ACTION`、`POLICY_DECISION`、五种实际 `APPROVAL_*`、`MODEL_REQUEST`、`TOOL_RESULT`、`DEMO_EVENT` 中文标签，并让未知类型保留原机器码。
- 同轮修复：公开结果页只显示隔离内置工作区、不泄露内置真实路径；服务端技术详情改为自动转义的合法 JSON；`SUCCESS` 在公开模式显示“演示成功”、本地模式显示“运行成功”，初始渲染和轮询保持一致。
- TDD 证据：复用工作树中已存在且与最终审查 brief 一致的未提交回归测试，先在旧实现上得到 8 failed、23 passed；最小实现后聚焦测试为 31 passed。
- 最终验证：仓库根 `.venv` 下聚焦测试 31 passed；全量 834 passed、2 skipped；Ruff 全通过；mypy 对 30 个源码文件无问题；`git diff --check` 通过。仅有既存的第三方 `StarletteDeprecationWarning`。
- 约束：未使用系统 `python.exe`，未改变审批、策略、工具执行和审计安全语义；未合并、推送或删除工作树。

## 演示结果清晰度 — Task 2

- 时间：2026-07-26 +08:00；分支 / 工作树：`demo-result-clarity` / `.worktrees/demo-result-clarity`。
- 用户反馈：“只能点按钮，看不见失败”。设计决策是在运行头部后直接放置机制结论和关键证据，并把失败、拦截、修正、等待审批、通过映射为带中文文字的审计状态；不增加交互步骤，技术 JSON 继续折叠。
- 实现提交：`9d2390166458e7965978b0f3b7c47137684d0950`（`feat(web): 突出演示失败与机制结论`）。本地模式无静态假结论；服务端与客户端都只接受固定六态，未知状态回退“信息”；动态内容继续使用 `createElement`、`textContent` 和 `dataset`。
- TDD / 验证：页面 RED 为 5 failed、18 passed，GREEN 为 23 passed；Web 完整回归 33 passed；Ruff 全通过；`innerHTML|insertAdjacentHTML|document.write` 扫描无匹配；`git diff --check` 无错误。仅有既有第三方 `StarletteDeprecationWarning`。
- 技能 / 约束：使用 `test-driven-development`、`frontend-design` 与 `verification-before-completion`；始终调用仓库根 `.venv`，未使用系统 `python.exe`。浏览器桌面/窄屏视觉验收由主代理审查后完成；未合并、推送或删除工作树。

## Usable CLI runtime — Task 5

- 时间：2026-07-28 20:40–21:13 +08:00；分支 / 工作树：`usable-cli-runtime` / `.worktrees/usable-cli-runtime`。
- 任务：完成 fresh-install wheel、CI smoke、真实 CLI 中文教程、计划/日志证据和最终本地门禁；不新增产品功能，不宣称未发生的 CI、Release 或部署。
- 技能：`executing-plans`、`using-git-worktrees`、`systematic-debugging`、`test-driven-development`、`verification-before-completion`。工作树检测确认当前为 linked worktree、命名分支、非 submodule/非 detached HEAD。
- Context：实现代理 `/root/cli_task5_delivery`，上游编排代理 `/root`。本子任务没有执行独立整分支评审；whole-branch review 与集成决定保留给上游 Final Review Gate。没有学生手工代码修改。
- 实现：在 GitHub Actions 的现有 pytest/Ruff/mypy 后加入 fresh-wheel venv smoke，保持 Gitleaks 与 Docker job；dev extra 声明 `build>=1.2,<2`；README 改为可直接执行的真实 CLI 教程并明确外部状态；分发测试覆盖 demo validator 的运行时依赖。
- 调试 / TDD：首轮已构建/安装 wheel，fresh venv 的 `pytest_spec=None` 且 `safefix-demo all` feedback 失败，证明 demo validator 的运行时依赖缺口；新增 `test_runtime_dependencies_include_the_demo_validator` 后得到预期 RED，再把原有 `pytest>=8.3,<9` 从仅 dev extra 移到运行时依赖，定向测试 GREEN。该轮 smoke 同时继承了工作树 `PYTHONPATH`，因此入口行为不作为 wheel 来源证据；来源证据由下述审查修复轮替代。
- 构建环境：首次隔离 build 和 fresh pip install 均被网络沙箱阻断；按沙箱升级流程各只重跑一次后成功。记录的是本地构建/安装结果，不等同于 GitHub Actions。
- 手工旅程：显式设置 `PYTHONPATH=<worktree>/src`，使用仓库根 `.venv` 的 `safefix.exe`；以工作树内 `SAFEFIX_DATA_DIR` 执行 `config init`、`config validate` 和 Mock run。第二次捕获仅设置 `PYTHONUTF8=1` 取得可读中文证据，没有改变运行语义。
- 手工结果：exit 0；原 fixture SHA-256 未变；隔离副本修复为 `return left + right`；审计 SQLite 存在；输出包含隔离路径、验证失败、patch、验证通过、修改文件、SUCCESS 和审计路径；capability/CSRF/approval token、traceback、API key 扫描均无匹配。
- 测试：第一轮全量 pytest `942 passed, 2 skipped, 1 warning in 58.59s`；Ruff 全通过；mypy 对 33 个源码文件无问题；三个 demo PASS；diff check exit 0。唯一 warning 是既有第三方 `StarletteDeprecationWarning`。
- Docker：`docker version` 报告 `docker_engine` named pipe 不存在，daemon 未运行；因此 image build/container demo 为真实环境阻塞，没有伪称通过。
- 清理：对 `.smoke-venv`、`dist`、`.manual-data`、`.manual-safefix.yaml` 逐个做绝对路径与工作树严格后代检查后删除。
- 经验：fresh-install smoke 必须真正隔离 dev extra，否则会掩盖“演示使用 pytest、但 wheel 不声明 pytest”的分发缺口；CI 配置存在不代表 CI 已运行，本地 wheel、Docker daemon、Release 和公网部署必须分别记录证据。

## Usable CLI runtime — Task 5 审查修复轮次 1/5

- 时间：2026-07-28 +08:00；审查发现：上一轮 smoke 会话继承 `PYTHONPATH=<worktree>/src`，因此 launcher 成功不能证明实际加载 wheel。
- 证据修复：重新 build/install 前及每组 smoke 前均执行 `Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue` 并断言变量不存在。`.smoke-venv` Python 报告 `safefix.__file__` 为 `.smoke-venv\Lib\site-packages\safefix\__init__.py`，且不在工作树 `src`。
- packaged resources：`importlib.resources` 定位到 site-packages 内 `_fixtures/python_bug` 和 `_fixtures/mock_repair.jsonl`，两者及 `calculator.py` 均存在。
- 入口：三个 Windows launcher 均存在；无 `PYTHONPATH` 的 `safefix --help` exit 0、三个 demo PASS；`safefix-public-demo` 的 console entry 从已安装 wheel metadata 加载为 `safefix.cli:public_demo_main`，采用聚焦 import 检查避免启动长驻服务。
- packaged Mock journey：把已安装资源复制到工作树临时输入目录，使用 `.smoke-venv` launcher 执行 `config init`、`config validate` 和 Mock run。`PYTHONPATHPresent=False`、exit 0、输入副本 SHA-256 未变、隔离结果含 `return left + right`、审计库存在、输出包含失败/patch/通过/SUCCESS，且无 traceback/capability。
- Minor：README 明列 pytest 是运行时依赖，用于内置 feedback/Mock 验收和默认 validator；分发测试改用 `packaging.Requirement` 与 `canonicalize_name` 后精确断言依赖名为 `pytest`，避免 `pytest-fake` 等前缀误报。
- 清理：`.smoke-venv`、`dist`、`.wheel-smoke-input`、`.wheel-smoke-data` 和 `.wheel-smoke-config.yaml` 均在解析为当前工作树严格后代后删除。
