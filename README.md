# SafeFix Harness

SafeFix 是一个带确定性安全边界的自动代码修复实验框架。模型每轮只产生一个结构化动作；路径、命令、审批、审计和验证规则会在动作影响工作区前生效。仓库同时提供真实 CLI、WebUI 和无需模型密钥的离线机制演示。

主仓库：[Gungnir6/safefix-harness](https://github.com/Gungnir6/safefix-harness)。

## Installation

SafeFix 要求 Python `>=3.12,<3.13`。从源码开发安装：

```bash
python -m venv .venv
# 激活虚拟环境后
python -m pip install -e ".[dev]"
```

也可以从构建产物或 GitHub Release 资产安装 wheel：

```bash
python -m pip install safefix_harness-0.1.0-py3-none-any.whl
safefix --help
```

Release 只有在仓库发布页出现实际 `.whl` 资产后才可使用；本 README 不把尚未发生的发布写成已完成。维护者可运行 `python -m build --wheel` 生成 `dist/` 下的 wheel。

## Real CLI Tutorial

先生成完整的保守配置。命令默认写入当前目录的 `safefix.yaml`，文件已存在时拒绝覆盖：

```bash
safefix config init
```

按 OpenAI-compatible 服务修改 `llm.endpoint` 与 `llm.model`，并检查 `validators` 中的 Python 路径和命令适合目标项目。随后验证配置：

```bash
safefix config validate
```

## Credentials

API key 存入系统 keyring，不进入 YAML、命令行或普通输出。`credentials set` 使用隐藏输入：

```bash
safefix credentials set --provider openai-compatible
safefix credentials status --provider openai-compatible
safefix credentials clear --provider openai-compatible
```

默认运行会把项目复制到持久化隔离工作区，原项目不会被修改：

```bash
safefix run C:\path\to\project --task "修复失败的测试"
```

可用 `--config PATH` 指定配置，以 `--data-dir PATH` 覆盖数据目录。未覆盖时，Windows 使用 `%LOCALAPPDATA%\SafeFix`，其他平台使用 `$XDG_DATA_HOME/safefix` 或 `~/.local/share/safefix`。CLI 会显示有效隔离工作区、运行状态、修改文件和审计数据库；结果通常位于数据目录的 `runs/<execution-id>/workspace`，审计记录位于数据目录的 `safefix.sqlite3`。

高风险动作会完整展示动作类型、结构化参数、理由和风险，再询问 `Approve this action once? [y/N]`。授权只绑定当前冻结动作且只消费一次。`--non-interactive` 遇到审批时一律拒绝。

> **危险：** `--in-place` 会直接修改原项目。只有明确需要且已自行备份或提交当前工作时才使用；路径围栏、敏感文件、命令策略和审批仍会生效，但 SafeFix 不会替你 commit、push 或发布。

脚本消费最终摘要时使用 `--json`。stdout 只包含稳定 JSON 摘要；若需要人工审批，提示写入 stderr：

```bash
safefix run ./project --task "修复测试" --json
```

当前真实 provider 只支持 OpenAI-compatible `/chat/completions`。服务必须兼容该协议；供应商专用协议、自动提交/推送/部署和多 Agent 编排不在当前范围。网络、认证、限流、超时或畸形响应会安全失败，默认不显示 Python traceback。

## Deterministic Mock

发行 wheel 内含 `python_bug` fixture 和 `mock_repair.jsonl`。以下 PowerShell 示例从已安装包定位它们，并通过真实 `AgentLoop`、工具、验证器、审计和隔离工作区完成确定性验收：

```powershell
$fixture = & python -c "from importlib.resources import files; print(files('safefix').joinpath('_fixtures/python_bug'))"
$script = & python -c "from importlib.resources import files; print(files('safefix').joinpath('_fixtures/mock_repair.jsonl'))"
safefix config init .mock-safefix.yaml
safefix run $fixture --task "修复失败的加法测试" --config .mock-safefix.yaml --provider mock --mock-script $script
```

在源码仓库中也可直接运行：

```bash
safefix run examples/python_bug --task "修复失败的加法测试" --config .mock-safefix.yaml --provider mock --mock-script examples/mock_repair.jsonl
```

Mock 是固定动作脚本驱动的离线验收 harness，用于复现安全与分发路径；它不是能理解任意任务的通用智能模型。独立机制演示可运行：

```bash
safefix-demo all
```

## Usage

本地 WebUI 默认绑定回环地址：

```bash
safefix serve --public-demo --host 127.0.0.1 --port 8000
```

启动后访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。公开演示只使用内置 fixture 和确定性 Mock，不访问用户项目、真实模型或本地凭据。只有需要局域网访问时才显式使用 `--host 0.0.0.0`。

## Public Demo

`safefix-public-demo` 提供三个确定性场景：

- **安全边界**：危险命令在执行前被策略拒绝，工具调用为零。
- **验证反馈**：错误补丁再次失败，正确补丁最终通过。
- **一次性审批**：高风险动作暂停，授权只绑定冻结动作且不可重放。

仓库提供 Render 蓝图，但当前没有可验证的公网部署 URL；部署完成前不宣称公开 WebUI 已上线。

## Distribution

项目使用 Hatchling 构建。wheel 包含 Web 模板、静态资源、Mock 脚本和 Python bug fixture。Docker 镜像基于 Python 3.12 slim，以 UID 10001 非 root 用户运行：

```bash
docker build -t safefix .
docker run --rm -p 8000:8000 safefix
docker run --rm safefix python -m safefix.demo all
```

GitHub Actions 执行 pytest、Ruff、mypy、fresh-wheel CLI smoke、Git 历史 secret scan 和镜像构建；只有 tag 工作流会尝试向 `ghcr.io/gungnir6/safefix-harness` 推送镜像。此处描述工作流配置，不代表当前提交已经在外部 CI 或 Release 中成功运行。

## Project Structure

- `src/safefix/`：领域模型、治理、工具、运行时、CLI 与 Web。
- `tests/`：单元、性质、集成和 Web 测试。
- `examples/python_bug/`：确定性离线修复 fixture。
- `examples/mock_repair.jsonl`：完整 Mock 修复动作脚本。
- `docs/`：设计、实施计划、威胁模型与课程任务记录。

## Security Boundaries

可信边界由工作区路径规范化、命令白名单、结构化动作校验、风险策略、一次性审批、输出脱敏审计和验证预算共同组成。隔离复制排除 `.git`、虚拟环境、缓存、构建输出、符号链接和配置的敏感路径。公开模式禁止客户端指定真实项目或 provider，并限制请求速率与并发运行数。

## Known Limitations

- 真实模型仅支持 OpenAI-compatible `/chat/completions`，具体服务的字段差异可能不兼容。
- 默认 Web 服务是内存中的确定性公开演示；重启后演示记录消失。
- Mock 只接受受限 JSONL 动作脚本，不理解任意自然语言任务。
- Docker 默认启动公开 Mock WebUI，不会自动挂载或修改宿主源码。
- GitHub Release 和公网 Render 部署需要仓库/平台账户权限，必须在外部成功后另行记录实际链接。

## Architecture

主要数据流为：用户任务 → 模型适配器 → 动作解析器 → 确定性策略 → 受限工具 → 验证器 → 审计与反馈。CLI 通过运行时组合层构造并复用同一 `TaskService` 和 `AgentLoop`，不会建立简化的第二套循环。

## Deployment

`render.yaml` 可创建 Docker Web Service，健康检查路径为 `/health`。生产部署还应配置 TLS、外部持久化、身份认证和更严格的限流。部署及 GitHub Release 都是独立外部状态步骤，不由本地构建结果替代。

## Third-Party Licenses

运行时依赖包括 FastAPI、Uvicorn、Pydantic、HTTPX、Jinja2、PyYAML、pathspec 与 keyring；构建产物不复制其源码。发布或再分发前请依据锁定版本的元数据检查并保留各依赖许可证。
