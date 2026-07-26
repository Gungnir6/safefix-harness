# SafeFix 可用 CLI 运行时设计

## 背景

SafeFix 已经实现自有的 AgentLoop、结构化动作解析、文件与进程工具、确定性治理、验证反馈、记忆、审计和一次性审批状态机，也有公开 WebUI 与三种 Mock 机制演示。现有 `safefix run` 仍要求调用方注入 `task_service`，直接从安装后的命令行执行会报 `task service is not configured`。现有 `config init` 只写入 LLM endpoint，缺少模型和验证器，不能生成可直接运行的配置。

因此，当前内核符合机制实现与确定性测试要求，但 CLI 只是薄适配器，尚未形成用户可从零配置并运行的 Coding Agent Harness。本设计补齐生产组合根、隔离工作区、真实模型接入、交互审批、过程输出和分发验收。

## 目标

1. 用户安装 SafeFix 后，可以通过命令行安全配置 OpenAI-compatible 模型并运行真实修复任务。
2. 默认在持久化隔离副本中运行，不修改原项目。
3. 只有显式使用 `--in-place` 才允许在原项目中执行动作。
4. CLI 真实组装并调用现有 AgentLoop、工具、治理、反馈、记忆、审计和审批组件，不建立第二套简化循环。
5. 高风险动作必须在 CLI 中展示风险并获得一次性人工批准。
6. 用户可以理解每轮发生了什么，并能找到结果工作区、修改文件和审计记录。
7. 保留确定性 Mock 演示，满足离线测试和课程机制演示要求，但不把 Mock 描述成通用智能模型。
8. 在全新虚拟环境和容器中验证安装、启动器与演示可用。

## 非目标

- 不实现新的 Agent 编排框架或多 Agent 系统。
- 不在本轮支持多个供应商专用协议；统一使用 OpenAI-compatible `/chat/completions` 接口。
- 不自动提交、推送或部署用户项目。
- 不在无明确授权时写入用户原项目。
- 不承诺 Mock 能理解任意自然语言任务；Mock 仅用于脚本化离线验收。
- 不重写 WebUI；WebUI 后续只复用同一运行时组合层。

## 方案选择

### 方案一：在 `cli.py` 中直接组装全部组件

优点是改动少。缺点是 CLI 同时承担参数解析、资源生命周期、依赖构造、交互和展示，难以测试，也无法被 WebUI 复用。

### 方案二：新增独立运行时组合层（采用）

新增职责单一的组合模块，由它创建数据库连接、模型客户端、工作区、工具、策略和 AgentLoop；CLI 只负责解析参数、交互审批和展示结果。该方案最大程度复用现有内核，边界清晰，可独立测试。

### 方案三：重构为常驻后台服务

可以让 CLI 与 WebUI 都通过本地 API 使用运行时，但会引入进程管理、端口、认证和恢复等额外复杂度，超出本次交付需要。

## 架构

### 运行时组合层

新增 `safefix.runtime`，提供应用级工厂和资源生命周期管理：

- 加载并验证 `safefix.yaml`；
- 解析 SafeFix 数据目录；
- 初始化持久化 SQLite；
- 创建 `RunStore`、`AuditStore`、`MemoryStore` 和 `ApprovalStateMachine`；
- 创建操作系统钥匙串上的 `CredentialService`；
- 根据 provider 创建 `OpenAICompatibleClient` 或 `ScriptedMockLLM`；
- 基于有效工作区创建 `WorkspaceBoundary`、文件工具、进程工具和 `ValidatorRunner`；
- 创建 `ToolRegistry`、`PolicyEngine`、`FeedbackEngine`、`ContextBuilder` 与 `AgentLoop`；
- 返回供 `TaskService` 和 CLI 使用的受管运行时对象；
- 在退出时关闭 HTTP 和数据库资源。

CLI 不再接受“未注入时失败”的默认路径。测试仍可通过显式注入替代服务，保持适配器可测性。

### 工作区管理

新增工作区管理边界：

- 默认模式为 `isolated`；
- 验证输入目录存在、为目录且可读；
- 在 SafeFix 数据目录下预先创建 `runs/<execution-id>/workspace`，启动 AgentLoop 后将其生成的 `run_id` 写入该执行目录的元数据；
- 复制项目时排除 `.git`、`.venv`、缓存、构建输出、SafeFix 数据目录和配置中的敏感模式；
- 隔离工作区默认保留，便于用户检查结果；
- CLI 输出结果路径；
- `--in-place` 使用解析后的原项目路径，并在运行前打印醒目警告；
- 即使使用 `--in-place`，路径围栏、敏感文件规则、命令规则和审批仍然生效；
- SafeFix 不自动执行 Git commit、push 或其他外部发布动作。

隔离复制失败、目标空间不可写或项目路径非法时，AgentLoop 不启动，CLI 返回稳定的非零退出码。

### 模型提供者

首版支持：

- `openai-compatible`：从配置读取 endpoint/model，从钥匙串按 provider 读取 API key；
- `mock`：从显式脚本文件或内置机制演示读取确定性动作，供测试与离线验收使用。

真实 `run` 默认使用 `openai-compatible`。没有凭据时，CLI 给出 `credentials set` 的下一步命令，不回显密钥。模型请求继续使用现有超时、认证、限流和异常响应分类。

### CLI 交互层

主要用户流程：

```powershell
safefix config init
safefix credentials set --provider openai-compatible
safefix config validate
safefix credentials status --provider openai-compatible
safefix run C:\path\to\project --task "修复失败的测试"
```

`run` 支持：

- 位置参数 `project`；
- 必需参数 `--task`；
- `--config`，默认 `safefix.yaml`；
- `--provider`，默认 `openai-compatible`；
- `--in-place`，默认关闭；
- `--mock-script`，仅供 Mock 离线运行；
- `--non-interactive`，遇到需要审批的动作时直接拒绝；
- `--json`，输出稳定机器可读摘要。

`config init` 写入可通过 `config validate` 的完整模板，包括 endpoint、model、pytest 验证器、预算、记忆和保守策略默认值。若目标文件已存在，默认拒绝覆盖。

### 审批流程

当 AgentLoop 返回 `AWAITING_APPROVAL`：

1. CLI 从 `TaskService` 获取只对当前运行有效的审批访问对象；
2. 展示动作类型、规范化参数、理由、风险等级和命中规则；
3. 默认提示为 `Approve this action once? [y/N]`；
4. `y` 调用 `approve`，其余输入调用 `reject`；
5. 批准能力只消费一次，并只匹配冻结动作摘要；
6. `--non-interactive` 永远拒绝，不提供自动批准开关；
7. 运行继续，直到进入终态或再次等待审批。

CLI 不打印审批 token、CSRF token、API key 或敏感原始载荷。

### 过程与结果输出

人类可读输出包含：

- 运行 ID；
- 模式（隔离副本或原地）；
- 有效工作区；
- provider/model；
- 按顺序展示的模型动作、策略结论、工具结果和验证反馈；
- 等待审批提示；
- 最终状态和停止原因；
- 修改文件；
- 隔离结果路径；
- 审计数据库路径。

默认输出对 stdout/stderr 和事件载荷执行现有截断与脱敏。默认不显示 Python traceback；`--json` 输出稳定字段，便于 CI 或脚本消费。

退出码：

- `0`：运行成功或机制演示通过；
- `2`：参数或配置错误；
- `3`：凭据或模型供应商错误；
- `4`：工作区准备失败；
- `5`：运行完成但修复未成功；
- `6`：用户拒绝审批或取消；
- `7`：治理、审计或持久化不可用。

## 数据流

1. CLI 解析参数并加载配置。
2. 工作区管理器创建隔离副本或确认原地模式。
3. 运行时工厂构造共享数据库、凭据、模型、工具、策略和 AgentLoop。
4. `TaskService.create` 创建任务并启动 AgentLoop。
5. AgentLoop 请求模型输出一个结构化动作。
6. 动作解析后先进入治理；拒绝或审批结果形成反馈。
7. 被允许的动作由 ToolRegistry 分发。
8. 工具结果与验证器输出进入 FeedbackEngine，再反馈给下一轮模型上下文。
9. 每个动作、策略决定和工具结果进入审计链。
10. CLI 处理审批暂停，直到成功、失败、阻塞、预算耗尽、无进展或取消。
11. CLI 输出最终摘要，并保留隔离工作区和持久化审计证据。

## 错误处理与安全

- 在启动模型前完成路径、配置、数据库和凭据检查，避免半初始化运行。
- 用户错误使用简短中文说明和修复命令；内部异常默认不泄露 traceback。
- SQLite、审计或治理不可用时失败关闭，不继续执行工具。
- 工作区路径必须经过解析、别名和边界检查。
- 敏感文件不复制、不读取、不写入。
- 进程只能使用配置允许的程序和结构化参数，仍禁止自由 shell 字符串。
- 高风险动作不能通过命令行参数全局自动批准。
- API key 只来自 `CredentialService`，不进入配置、日志、事件或命令行参数。
- 原地模式的风险必须在 README 和 CLI 中明确说明。

## 测试策略

### 单元测试

- 运行时工厂正确组装各组件并关闭资源；
- 配置初始化生成有效且保守的模板；
- provider 选择、缺失凭据和错误映射；
- 隔离复制排除规则、路径错误和原地模式确认；
- 审批展示、批准、拒绝、重复消费和非交互拒绝；
- 人类输出脱敏与 JSON 输出契约；
- 退出码映射。

### 集成测试

- 使用脚本 Mock 驱动完整 AgentLoop 修复 `examples/python_bug`；
- 真实复制隔离项目、运行失败测试、修改文件、再次验证并成功；
- 原项目保持不变，隔离副本包含正确修改；
- `--in-place` 明确启用后修改指定 fixture；
- 高风险动作暂停，拒绝后不执行，批准后只执行一次；
- OpenAI-compatible HTTP Mock 覆盖成功、401、429、超时和畸形响应；
- 审计链可验证，修改文件和最终状态可重开读取。

### 分发测试

- 在全新虚拟环境安装构建产物；
- 验证 `safefix`、`safefix-demo`、`safefix-public-demo` 启动器存在；
- 运行 `safefix --help`、配置初始化和离线演示；
- 构建并运行非 root Docker 镜像；
- CI 运行 pytest、Ruff、mypy、Gitleaks 和 Docker build。

## 作业要求对应关系

- 决策：真实或脚本 Mock LLM 每轮只产生一个结构化动作；
- 工具：文件读取、搜索、补丁、进程与验证器由自有 ToolRegistry 分发；
- 记忆：持久化 MemoryStore 进入上下文构造；
- 治理：路径围栏、敏感文件、进程规则和一次性审批在工具前执行；
- 反馈：测试、lint 和类型检查结果进入 FeedbackEngine 并回灌；
- 配置：完整、可验证的 YAML 配置与安全钥匙串；
- 分发：Python 包、CLI 启动器和 OCI 容器；
- 演示：三个确定性 Mock 机制场景继续保留；
- WebUI：继续提供公开演示，并在后续部署到公网。

## 验收标准

1. 全新虚拟环境执行 `pip install <artifact>` 后能直接运行 `safefix --help`。
2. `safefix config init` 生成的配置可立即通过 `config validate`。
3. 在未配置 key 时，真实运行安全失败并给出钥匙串配置命令。
4. 使用 OpenAI-compatible 测试服务时，CLI 能在隔离副本中完成完整 AgentLoop 运行。
5. 默认运行不改变原项目；结果副本和审计记录可定位。
6. `--in-place` 是修改原项目的唯一入口。
7. 高风险动作默认等待审批，拒绝不执行，批准只执行一次。
8. 用户能看到动作、治理、工具、验证反馈和最终结果，而不是只有 `PASS`。
9. Mock 端到端修复、真实提供者传输、隔离安全和分发启动器均有自动测试。
10. 全量 pytest、Ruff、mypy、密钥扫描和 Docker 构建通过。
11. README 清楚说明安装、真实运行、Mock 演示、凭据、隔离模式、原地模式和已知限制。
12. 公网 WebUI 与 GitHub Release 在最终 Gate 2 中提供可访问链接。
