# SafeFix Harness

SafeFix 是课程作业 A（Coding Agent Harness）的实现。它自研了 AgentLoop，并用代码实现工作区围栏、三级风险策略、一次性人工审批、客观验证反馈、记忆和防篡改审计。验收与演示默认使用确定性 Mock，不需要联网或 API Key。

主仓库：[Gungnir6/safefix-harness](https://github.com/Gungnir6/safefix-harness)。

## Installation

要求 **Python 3.12**（`>=3.12,<3.13`）。Python 3.13 当前不受支持，使用 3.13 安装时 pip 会按设计拒绝继续。

Windows/Conda 用户推荐直接创建独立环境，避免现有 `(base)` 环境的 Python 版本干扰：

```powershell
conda create -n safefix python=3.12 -y
conda activate safefix
python --version
python -m pip install .\safefix_harness-0.1.1-py3-none-any.whl
safefix-demo all
```

正式分发链接：[SafeFix Harness v0.1.1](https://github.com/Gungnir6/safefix-harness/releases/tag/v0.1.1)。上述 wheel 文件从该页面下载。

没有 Conda 时，先确认 `python --version` 显示 `3.12.x`，再使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\safefix_harness-0.1.1-py3-none-any.whl
.\.venv\Scripts\safefix-demo.exe all
```

从源码开发安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Usage

最直接的作业验收命令是：

```powershell
safefix-demo all
```

它依次展示三个确定性机制：危险动作被护栏拦截、失败验证反馈促使下一轮改变动作、一次性审批暂停与恢复。三个场景均使用 `ScriptedMockLLM`，会经过真实的 AgentLoop、策略、工具和反馈代码。

若要查看 WebUI：

```powershell
safefix serve --public-demo --host 127.0.0.1 --port 8000
```

然后访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。WebUI 只运行内置 Mock 场景，不接收真实项目、真实 Key 或任意命令。

无需安装也可访问线上演示：[SafeFix Public Demo](https://safefix-public-demo.onrender.com)。免费实例休眠后的首次访问可能需要等待 Render 唤醒。

若要验证一次完整的“失败测试 → 修改 → 测试通过”闭环：

```powershell
$fixture = & python -c "from importlib.resources import files; print(files('safefix').joinpath('_fixtures/python_bug'))"
$script = & python -c "from importlib.resources import files; print(files('safefix').joinpath('_fixtures/mock_repair.jsonl'))"
safefix config init .mock-safefix.yaml
safefix run $fixture --task "修复失败的加法测试" --config .mock-safefix.yaml --provider mock --mock-script $script
```

默认在隔离副本中修改，不改变输入 fixture；输出会给出结果工作区和审计数据库路径。`--in-place` 会直接修改原项目，不建议用于演示。

## Credentials

真实模型不是课程验收前提，仅作为可选扩展保留。当前只支持 OpenAI-compatible `/chat/completions`，Key 通过隐藏输入写入系统 keyring：

```powershell
safefix credentials set --provider openai-compatible
safefix credentials status --provider openai-compatible
safefix credentials clear --provider openai-compatible
safefix run C:\path\to\project --task "修复失败的测试"
```

真实供应商可能因协议字段、模型行为、网络或配额而失败；这不影响 Mock 验收结果。Key 不应写入 YAML、命令参数或仓库。

## Distribution

本项目同时提供 CLI Release 和 [在线 WebUI](https://safefix-public-demo.onrender.com)。构建 wheel：

```powershell
python -m build --wheel
```

GitHub Actions 和保留的 GitLab CI 执行 pytest、Ruff、mypy、fresh-wheel CLI smoke 与 secret scan。`v0.1.1` 从通过 CI 的 `main` 构建并发布，Release 页面同时提供 wheel 与校验信息。

## Project Structure

- `src/safefix/`：AgentLoop、模型适配、治理、工具、审计、CLI 与 WebUI。
- `tests/`：单元、性质、集成和 Web 测试。
- `examples/` 与包内 `_fixtures/`：确定性 Mock 动作脚本和 Python 缺陷示例。
- `SPEC.md`、`PLAN.md`、`SPEC_PROCESS.md`、`AGENT_LOG.md`：规约、计划与开发证据。
- `docs/superpowers/`：设计与实施计划记录。

## Security Boundaries

SafeFix 对模型输出和仓库内容默认不信任。路径规范化、敏感路径、结构化命令、风险规则、冻结动作哈希、一次性审批令牌、输出脱敏、审计哈希链和执行预算均由项目代码强制执行。它不是完整的操作系统沙箱；即使使用 `--in-place` 或批准额外进程，也应先备份项目。

## Known Limitations

- Mock 只执行预设 JSONL 动作，不理解任意自然语言任务。
- 本地 WebUI 的公开演示状态存于内存，服务重启后清空。
- 真实模型适配器只覆盖 OpenAI-compatible 单次请求，不保证兼容所有供应商。
- SafeFix 不自动 commit、push、发布或部署用户代码。
- 仓库目前仍缺少由学生本人撰写的 `REFLECTION.md`；这是提交前必须完成的人工作业项。

## Architecture

主要数据流为：用户任务 → LLM 抽象（默认 Mock）→ 严格动作解析 → 确定性策略 → 受限工具/审批 → 验证器 → 结构化反馈 → AgentLoop 下一轮或停机。CLI 与 WebUI 复用同一个 `TaskService` 和 AgentLoop。

## Third-Party Licenses

运行时依赖包括 FastAPI、Uvicorn、Pydantic、HTTPX、Jinja2、PyYAML、pathspec、keyring 与 pytest；构建产物不复制其源码。发布前应依据锁定版本的包元数据检查并保留相应许可证。
