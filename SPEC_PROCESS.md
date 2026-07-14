# SPEC_PROCESS

本文档记录使用 Superpowers 生成、验证和修订 `SPEC.md` 与 `PLAN.md` 的全过程。

## 1. Brainstorming 记录

### 关键迭代 1：确定项目方向

- 智能体建议：将宽泛的 Coding Agent Harness 收敛为 `SafeFix Harness`，专门处理现有代码库中的小型缺陷修复。
- 目标用户备选：个人开发者的本地代码库、学生编程作业、团队仓库机器人。
- 学生决定：选择个人开发者的本地代码库场景；用户指定本地项目和修复任务，Agent 仅在受限工作区内操作。
- 核心闭环：读取代码 → 修改文件 → 运行测试 → 接收确定性失败反馈 → 再次修正。
- 重点机制：以代码实现的治理护栏拦截危险命令和越界操作，必要时进入人工审批。
- 方向决定：采纳 `SafeFix Harness`。
- 采纳理由：该范围能完整覆盖 harness 的六个基本维度，同时让治理与反馈机制可以脱离真实 LLM 进行确定性测试和演示。

### 关键迭代 2：确定语言支持边界

- 备选方案：仅支持 Python；机制语言无关但以 Python 项目演示；同时内置 Python、JavaScript、Java 的完整适配。
- 学生决定：采用语言无关的 harness 设计，测试、lint、类型检查命令通过声明式配置提供；首个演示和主要验收使用 Python 项目。
- 采纳理由：避免把核心循环绑定到单一语言，同时控制多语言适配和测试矩阵带来的范围膨胀。
- 交互方案备选：仅 WebUI、本地 WebUI + CLI、上传代码到远程 Web 服务。
- 学生决定：使用本地 WebUI 作为主要界面，并提供 CLI；不采用上传真实代码的远程执行模式。
- 采纳理由：WebUI 用于展示动作、反馈与人工审批，CLI 用于确定性测试和演示；本地运行更符合个人开发者代码隐私和工作区隔离需求。

### 关键迭代 3：区分本地完整版与公网演示版

- 备选方案：公网静态界面；公网接收用户代码和自带 API Key；公网受限演示沙箱。
- 学生决定：公网版仅操作内置示例仓库并使用 mock LLM，展示完整的修复闭环和治理机制；不接收真实代码或 API Key。本地版支持真实项目与真实 LLM。
- 采纳理由：仍能提供可访问、可操作的 WebUI，同时显著降低源代码泄露、任意命令执行、凭据存储和模型费用风险。
- 重点维度备选：治理护栏、反馈闭环、记忆系统。
- 学生决定：以治理护栏为主要贡献，深入实现工作区边界、命令风险分级、确定性拦截、人工审批状态机和审计记录；其余五个维度提供完整的最低可运行实现。
- 工具范围决定：提供列目录、读文件、搜索、补丁修改、运行配置验证命令等受控工具；额外 Shell 命令必须先通过确定性的风险判断与审批流程，不开放无约束 Shell。
- 治理策略决定：采用三级风险模型。工作区内受控操作自动允许；删除、安装依赖、网络、Git 提交和未配置命令必须审批；工作区越界、提权、读取凭据及系统破坏类动作永久禁止。
- 反馈策略决定：验证失败以结构化反馈回灌给 LLM，最多自动修复 3 轮；连续两轮无进展、重复同一动作或达到预算时确定性停机并报告原因。
- 记忆策略决定：使用 SQLite 保存项目约定、人工确认决策、历史任务摘要和常见失败；按项目、类型、关键词和时间检索少量相关记录，不全量载入对话历史。
- LLM 与凭据决定：定义可注入 mock 的 LLM 抽象，并提供 OpenAI-compatible 单次对话实现；真实 Key 通过隐藏输入保存到操作系统 Keyring，支持状态查看、更新和清除，日志不得回显凭据。
- 技术路线备选：Python + FastAPI、TypeScript + Fastify/React、Go + 服务端模板。
- 学生决定：采用 Python + FastAPI + SQLite + pytest，WebUI 使用服务端模板和少量 JavaScript，分发以 Docker 与 Python 包为主。
- 选择理由：该路线便于实现 mock LLM、子进程隔离、Keyring、SQLite、离线测试和 Web 部署，可将主要工程投入集中于治理机制。

## 2. 陌生智能体冷启动验证

### 2.1 设置与客观证据

- 日期：2026-07-14。
- 第二智能体：OpenCode CLI 1.17.20，模型 `njuse/glm-5.2`；主开发智能体为 Codex App，类型与模型均不同。
- 技能：OpenCode 报告已加载 `using-superpowers` 与 `test-driven-development`。
- 上下文隔离：新目录中只有新建的 `.git`、`SPEC.md` 与 `PLAN.md`；未提供 Codex 对话、memory、`SPEC_PROCESS.md` 或 `AGENT_LOG.md`。
- 指令：尝试 T01 后尝试 T04；任何不确定点立即暂停，不得猜测，不得修改 SPEC/PLAN。
- 实际产出：只创建 `tests/unit/test_domain.py`，内容与 PLAN T01 的首个测试一致；未创建生产代码，未修改 SPEC/PLAN，未产生 commit。
- RED 证据：计划命令因环境无 pytest 而报 `No module named pytest`；OpenCode 另用直接 import 得到 `ModuleNotFoundError: No module named 'safefix'`。这证明模块尚不存在，但也暴露出计划缺少环境准备步骤，不能把替代命令视为完整 pytest RED。
- 暂停位置：T01 Step 3。它在发现 `type` 判别值未定义后按要求暂停；T04 因依赖 T01 未正式开始，只进行了规约预检查。

### 2.2 暴露的规约缺陷

1. 七种 Action 的 discriminator、字段、默认值和验证规则不完整。
2. SPEC 使用通用 `payload`，PLAN 使用判别联合，Action 模型互相矛盾。
3. `RunSnapshot`、预算模型和多个基础模型字段未完整定义。
4. `RiskLevel`、`RunStatus`、`FeedbackCategory`、`AccessKind` 等枚举值未固定。
5. `PolicyDecision.risk_level` 与 PLAN 中的 `risk` 命名不一致；Feedback 与 ProgressResult 的职责混合。
6. T04 没有指定敏感 glob 语义和 Windows 路径规范化算法。
7. PLAN 没有在首次 pytest 前创建 Python 3.12 虚拟环境并安装依赖；冷启动提示又禁止了所有网络，使依赖安装不可能。

### 2.3 处理决策

- 采纳：在 SPEC 与 T01 中完整列出七种动作、精确 discriminator、字段约束、所有基础枚举、`BudgetState`、`RunSnapshot`、`ToolResult`、`Feedback`、`ProgressResult` 等接口。
- 采纳：统一 `PolicyDecision.risk_level`，将进展判断独立为 `ProgressResult`。
- 采纳：T04 使用 `AccessKind`；敏感模式固定为 pathspec GitWildMatch；Windows 使用规范绝对路径、`normcase` 与 `commonpath`。
- 采纳：T01 新增不含生产行为的 Step 0，明确用 `py -3.12` 建立 `.venv` 并安装声明依赖；仅依赖安装可联网，核心测试仍离线。
- 不采纳：将 Python 范围放宽到 3.13。Python 3.12 是已批准的目标运行时，冷启动机器也已安装 3.12.3；问题是解释器选择与环境引导缺失，不是目标版本不可用。

### 2.4 关键修订前后对照

| 修订前 | 修订后 |
|---|---|
| Action 只有 `id/type/payload/reason`，PLAN 又要求具体子类 | 明确采用七类判别联合，列出每类字段、默认值、限制和精确 `type` 字符串 |
| T01 直接运行 pytest，但环境没有 pytest | T01 Step 0 先创建 Python 3.12 `.venv`、安装 `.[dev]`，再进入 RED |
| `RunSnapshot` 和 negative budget 无定义 | 定义 `BudgetState`、`RunSnapshot` 全部字段及非负/上限约束 |
| T04 只写“normalize case”和“sensitive globs” | 固定 `AccessKind`、GitWildMatch、`normcase + abspath + commonpath` 与符号链接逃逸语义 |
| `PolicyDecision.risk`/`risk_level` 与 Feedback progress 混用 | 统一 `risk_level`，进展改由独立 `ProgressResult` 表示 |

冷启动实现目录保持隔离，任何代码均不合并回主项目。修订后的 SPEC/PLAN 必须再次经学生审核后才能进入正式 T01。

### 2.5 修订确认

- 学生于 2026-07-14 审核并明确确认冷启动修订通过。
- Gate 0 完成；后续可以提交规约修订并准备正式仓库/实现流程。

## 3. 过程反思

待后续迭代持续补充。

## 4. 书面规约确认

- `SPEC.md` 经占位符、内部一致性、范围和歧义自审后提交给学生审核。
- 自审修正：区分公网 mock 模式、离线 Docker 模式和需要 LLM 出站网络的本地真实模型模式，避免把策略边界误述为完整 OS/网络沙箱。
- 学生于 2026-07-13 明确确认书面 SPEC 通过，允许进入 `writing-plans`；此前未编写实现代码。
