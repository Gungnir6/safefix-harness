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
