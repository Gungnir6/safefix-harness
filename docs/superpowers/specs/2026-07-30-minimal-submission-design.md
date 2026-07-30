# SafeFix 最低可交付版精简设计

## 目标

将仓库收缩为只服务于 AI4SE Coding Agent Harness 作业验收的版本。最终交付以自研 Harness 内核、Mock LLM 确定性机制演示、Mock WebUI、wheel 分发、CI 和课程过程文档为核心；不再把对话式 CLI、真实模型效果、Render 或 Docker 作为交付范围。

## 验收依据

精简后的仓库必须继续满足以下要求：

- 自己实现 Agent 主循环、上下文组织、动作解析、工具分发、结果回灌和停机判断。
- 六个维度具有可运行最低实现：决策、工具、记忆、治理、反馈、配置。
- Mock/stub LLM 可注入，核心机制无需网络或真实 LLM 即可确定性测试。
- 可重复展示护栏拦截、失败反馈后改变动作、一次性审批三个机制。
- 提供可安装 wheel、明确运行命令、CI 和最后一次通过记录。
- 保留 Mock WebUI，以覆盖书面交付清单中的可访问界面要求；真实公网部署不在本轮实施范围内，不虚构 URL。
- 保留 SPEC、PLAN、SPEC_PROCESS、README、AGENT_LOG 及历史设计/计划证据。
- REFLECTION.md 必须由学生本人撰写，本轮不生成正文。

## 保留范围

### Harness 内核

保留以下能力及其测试：

- `ActionParser` 与结构化动作领域模型；
- `AgentLoop`、上下文构建、预算和停机判断；
- 文件系统工具、进程工具、工具注册与分发；
- 路径围栏、策略判断、一次性审批和防篡改审计；
- validator、反馈分类、多轮修复和无进展判断；
- 运行快照、记忆存储、配置加载和执行工作区；
- `ScriptedMockLLM`、三种机制 demo 和 Python bug fixture。

### 交付入口

保留以下稳定入口：

```text
safefix-demo all
safefix serve --public-demo
safefix run <project> --provider mock --mock-script <script> --task <task>
```

保留 `config`、`credentials` 和一次性 `run` CLI。OpenAI-compatible adapter 与安全凭据模块作为可选扩展保留，用于说明供应商边界和凭据治理；README 明确其不属于作业演示与验收路径。

### 分发与界面

- wheel 是唯一承诺的分发产物；
- GitHub Actions 运行测试、Ruff、mypy、Gitleaks、wheel 构建和 fresh-install smoke；
- `.gitlab-ci.yml` 保留名为 `unit-test` 的 job；
- Mock WebUI、模板、静态资源及其 API/页面测试保留；
- `safefix-public-demo` 保留为本地或托管平台可调用的 WebUI 入口。

## 删除范围

删除以下运行时代码和测试：

- `src/safefix/cli_chat.py`；
- `src/safefix/cli_setup.py`；
- `tests/unit/test_cli_chat.py`；
- `tests/unit/test_cli_setup.py`；
- `cli.py` 中无参数对话入口、`chat` 和 `setup` 子命令；
- `cli_runner.py` 中仅供对话模式记录最近结果的 `summary_observer` 接口及测试。

删除以下分发和部署内容：

- `Dockerfile`；
- `render.yaml`；
- GitHub Actions 中 Docker image 构建、登录和推送 job；
- README 中 Render、Docker、对话式 CLI 和真实模型作为主要体验的说明。

删除仓库根的 `.try-safefix-20260729.yaml` 与 `.try-safefix-data-20260729/` 本地临时体验数据。课程要求原文、其他用户未跟踪文件、历史 worktree 和非本任务分支不删除。

## 最终用户流程

### 机制验收

```powershell
safefix-demo all
```

必须稳定输出：

```text
guardrail: PASS
feedback: PASS
approval: PASS
```

### WebUI 演示

```powershell
safefix serve --public-demo --host 127.0.0.1 --port 8000
```

页面只运行内置 fixture 和确定性 Mock，不读取用户项目、系统凭据或真实模型。

### 完整 Mock 修复

README 提供 wheel 安装资源定位方式，以及源码仓库下使用 `examples/python_bug` 和 `examples/mock_repair.jsonl` 的单条可复制命令。运行必须经过真实 AgentLoop、工具、validator、反馈、隔离工作区和审计。

## 文档调整

- README 首屏只介绍三条作业验收入口；真实 provider 移到“可选扩展与已知限制”。
- PLAN 新增“最低可交付版精简”任务并记录提交和验证结果。
- AGENT_LOG 记录删除理由、TDD/回归证据和“真实供应商不是硬性验收”的判断依据。
- SPEC 与 SPEC_PROCESS 只在出现与最终范围直接矛盾时做最小修订，不重写历史。
- 历史 `docs/superpowers/` 设计和计划保留，作为 Superpowers 工作流证据。
- README 明确 REFLECTION.md 由学生本人完成，仓库在提交前必须补齐。

## 安全与错误处理

- 删除对话模式不得改变工具、策略、审批、审计或隔离边界。
- Mock 演示不得访问网络或读取真实凭据。
- 可选真实 provider 失败时继续安全失败，不承诺兼容所有 OpenAI-compatible 服务。
- 清理本地临时数据前验证目标的解析后绝对路径严格位于仓库根目录内。
- 不删除任何无法确认归属的未跟踪文件或 worktree。

## 验证标准

1. 聚焦 CLI 回归证明 `chat`、`setup` 和无参数自动对话均不存在，保留命令仍可用。
2. 完整 pytest 无失败。
3. Ruff、mypy 和 `git diff --check` 通过。
4. `safefix-demo all` 精确通过三个场景。
5. Mock WebUI API 与页面测试通过。
6. wheel 构建成功，fresh venv 在不继承工作树 `PYTHONPATH` 时安装成功。
7. fresh wheel 的 `safefix --help`、`safefix-demo all` 和公开 WebUI entry point 可加载。
8. README、PLAN 和 AGENT_LOG 与精简后的真实功能一致，不宣称未发生的 Release、CI 或公网部署。
