# SafeFix Harness

SafeFix Harness 是课程作业 A（Coding Agent Harness）的确定性演示与本地修复工具。它围绕受限动作、风险治理、审批、客观验证反馈和审计构建；验收演示默认使用预设 Mock，不需要联网或 API Key。

| 交付入口 | 用途 | 链接 |
| --- | --- | --- |
| GitHub | 源码、文档与开发记录 | [Gungnir6/safefix-harness](https://github.com/Gungnir6/safefix-harness) |
| v0.1.1 Release | 下载已发布的 wheel 与校验信息 | [SafeFix Harness v0.1.1](https://github.com/Gungnir6/safefix-harness/releases/tag/v0.1.1) |
| Render WebUI | 无需安装的公开确定性 Mock 演示 | [SafeFix Public Demo](https://safefix-public-demo.onrender.com) |

## Core Mechanisms

- **护栏先于工具执行**：每个候选动作先经过路径、命令和风险策略检查；不满足边界的动作不会交给工具执行。
- **反馈改变下一步动作**：目标失败后，验证器产生结构化反馈，AgentLoop 在下一轮将其作为输入，而不是重复原动作。
- **一次性审批绑定冻结动作**：审批令牌只授予一个被冻结哈希的能力和动作；使用后失效，不能转移到其他动作。

## Quick Start

先打开 [Render WebUI](https://safefix-public-demo.onrender.com)，查看公开的确定性 Mock 展示。免费 Render 实例休眠后，首次访问可能需要等待唤醒。

安装 Release wheel 后，可在本地运行全部预设机制演示：

```powershell
safefix-demo all
```

## Installation

要求 **Python 3.12**（`>=3.12,<3.13`）。Python 3.13 不受支持。

Windows/Conda：

```powershell
conda create -n safefix python=3.12 -y
conda activate safefix
python --version
python -m pip install .\safefix_harness-0.1.1-py3-none-any.whl
safefix-demo all
```

使用 venv：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\safefix_harness-0.1.1-py3-none-any.whl
.\.venv\Scripts\safefix-demo.exe all
```

从源码进行开发安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Usage

启动本地公开演示服务器：

```powershell
safefix serve --public-demo --host 127.0.0.1 --port 8000
```

然后访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。它只展示内置的预设 Mock 场景，不接收真实项目、真实 Key、任意命令或任意自然语言任务。

运行打包提供的完整 Mock 修复流程：

```powershell
$fixture = & python -c "from importlib.resources import files; print(files('safefix').joinpath('_fixtures/python_bug'))"
$script = & python -c "from importlib.resources import files; print(files('safefix').joinpath('_fixtures/mock_repair.jsonl'))"
safefix config init .mock-safefix.yaml
safefix run $fixture --task "修复失败的加法测试" --config .mock-safefix.yaml --provider mock --mock-script $script
```

默认在隔离副本中修改，输出会给出结果工作区和审计数据库路径。`--in-place` 会直接修改原项目，应先备份项目。

## Credentials

真实模型是可选扩展，不是课程验收前提。目前支持 OpenAI-compatible `/chat/completions`；Key 通过隐藏输入写入系统 keyring：

```powershell
safefix credentials set --provider openai-compatible
safefix credentials status --provider openai-compatible
safefix credentials clear --provider openai-compatible
safefix run C:\path\to\project --task "修复失败的测试"
```

真实供应商可能因协议字段、模型行为、网络或配额而失败；这不影响 Mock 验收。不要把 Key 写入 YAML、命令参数或仓库。

## Architecture

公开 WebUI 是 `PublicDemoService` 支撑的确定性 Mock 展示层：它呈现预设情境及结果，不运行完整的 AgentLoop，也不用于处理任意任务。

本地生产路径由 `TaskService` 驱动：用户任务经过 LLM 抽象、严格动作解析、治理策略、受限工具或审批、验证器和结构化反馈，随后由 AgentLoop 决定下一轮或停机。该路径包含治理、工具和反馈组件，供本地 CLI 修复工作流使用。

## Distribution

`v0.1.1` 发布的 wheel 文件名为 `safefix_harness-0.1.1-py3-none-any.whl`，可从 [Release 页面](https://github.com/Gungnir6/safefix-harness/releases/tag/v0.1.1) 下载。需要自行构建时：

```powershell
python -m build --wheel
```

GitHub Actions 执行 pytest、Ruff、mypy、fresh-wheel CLI smoke 和 secret scan。Release 页面提供 wheel 与校验信息。

## Project Structure

- `src/safefix/`：AgentLoop、模型适配、治理、工具、审计、CLI 与 WebUI。
- `tests/`：单元、性质、集成和 Web 测试。
- `examples/` 与包内 `_fixtures/`：确定性 Mock 动作脚本和 Python 缺陷示例。
- `SPEC.md`、`PLAN.md`、`SPEC_PROCESS.md`、`AGENT_LOG.md`：规约、计划与开发证据。
- `docs/superpowers/`：设计与实施计划记录。

## Security Boundaries

SafeFix 对模型输出和仓库内容默认不信任。路径规范化、敏感路径限制、结构化命令、风险规则、冻结动作哈希、一次性审批令牌、输出脱敏、审计哈希链和执行预算均由项目代码强制执行。它不是完整的操作系统沙箱；即使使用 `--in-place` 或批准额外进程，也应先备份项目。

## Known Limitations

- Mock 仅执行预设 JSONL 动作，不理解任意自然语言任务。
- 公开 WebUI 是 `PublicDemoService` 的确定性展示，不运行完整 AgentLoop。
- 本地 WebUI 的公开演示状态存于内存，服务重启后清空。
- 真实模型适配器只覆盖 OpenAI-compatible 单次请求，不保证兼容所有供应商。
- SafeFix 不自动 commit、push、发布或部署用户代码。

## Third-Party Licenses

运行时依赖包括 FastAPI、Uvicorn、Pydantic、HTTPX、Jinja2、PyYAML、pathspec、keyring 与 pytest；构建产物不复制其源码。依赖版本及其许可证以锁定版本的包元数据为准，使用和再分发时应遵守各自许可证责任。
