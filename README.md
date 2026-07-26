# SafeFix Harness

SafeFix 是一个带确定性安全边界的自动代码修复实验框架。模型输出先被解析为结构化动作，再经过路径、命令、审批、审计和验证规则，才允许影响工作区。本仓库包含 CLI、Web 控制台以及不需要真实模型密钥的离线演示。

主仓库：[Gungnir6/safefix-harness](https://github.com/Gungnir6/safefix-harness)。

## Installation

需要 Python 3.12：

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

也可以构建标准 wheel/sdist：`python -m build`。

## Usage

SafeFix 同时提供 CLI 与 WebUI 两种使用方式。

CLI 适合在终端中运行确定性机制演示：

```bash
safefix demo
safefix-demo all
```

WebUI 适合通过浏览器查看状态、机器码、审计时间线和一次性审批流程。启动本地公开演示：

```bash
safefix serve --public-demo --host 127.0.0.1 --port 8000
```

启动后在本机访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。公开演示完全使用内置项目和确定性 mock，不需要真实模型凭据，也不访问用户项目。`safefix run` 面向集成方，需由调用代码注入实际任务服务。

## Credentials

凭据通过系统 keyring 管理，不写入仓库：

```bash
safefix credentials set --provider openai-compatible
safefix credentials status --provider openai-compatible
safefix credentials clear --provider openai-compatible --yes
```

不要提交 `.env`、私钥、数据库或日志。公开演示只使用确定性 mock，不需要凭据。

## Public Demo

`safefix-public-demo` 或上述 `safefix serve --public-demo` 命令会启动三个内置场景：

- **安全边界**：演示危险命令在执行前被确定性策略拦截，真实文件保持不变。
- **验证反馈**：演示系统根据测试失败反馈调整补丁，直到验证通过。
- **一次性审批**：演示高风险写入暂停等待人工决定，授权只绑定当前冻结动作且不能复用。

公开演示结果页会直接展示机制验证结论、关键证据，以及带中文状态徽标的审计时间线；中间的验证失败、策略拦截、修正和等待审批无需展开技术详情即可看见，最终通过也与过程状态明确区分。本地模式不会生成演示结论。

每次运行都会复制隔离 fixture，不访问用户项目或真实模型。若确需让同一局域网内的其他设备访问，可显式使用 `--host 0.0.0.0`；默认的 `127.0.0.1` 仅供本机访问。本仓库不虚构或预置公网部署地址。

## Distribution

项目使用 Hatchling 构建，wheel 内含 Web 模板、静态资源与演示 fixture。Docker 镜像基于 Python 3.12 slim，以 UID 10001 非 root 用户运行，并通过 `/health` 检查状态：

```bash
docker build -t safefix .
docker run --rm -p 8000:8000 safefix
```

GitHub Actions 执行测试、lint/type、Git 历史 secret scan 和镜像构建，仅在 tag 工作流中向 `ghcr.io/gungnir6/safefix-harness` 推送镜像。迁移前的 GitLab CI 配置保留用于课程环境兼容。

## Project Structure

- `src/safefix/`：领域模型、治理、工具、运行时、CLI 与 Web。
- `tests/`：单元、性质、集成和 Web 测试。
- `examples/python_bug/`：可重复的离线演示项目。
- `docs/`：架构决策、威胁模型及课程任务记录。

## Security Boundaries

可信边界由工作区路径规范化、命令白名单、结构化动作校验、风险策略、一次性审批、输出脱敏审计和验证预算共同组成。公开模式禁止客户端指定项目路径和真实 provider，并限制请求速率与并发运行数。

## Known Limitations

- 默认 Web 服务是内存中的离线演示适配器，重启后记录消失。
- `safefix run` 的真实模型/任务运行时需要集成方显式注入。
- 容器默认只运行 mock 公开演示，不挂载或修改宿主源码。
- Render 蓝图已提供，但仓库未保存账户凭据，部署 URL 需在 Render 中授权后生成。

## Architecture

主要数据流为：用户任务 → 模型适配器 → 动作解析器 → 确定性策略 → 受限工具 → 验证器 → 审计与反馈。Web 和 CLI 只负责输入输出，共用任务服务与治理层。

## Deployment

`render.yaml` 可直接创建 Docker Web Service，健康检查路径为 `/health`。也可以将 Dockerfile 部署到任意支持 OCI 镜像的平台。生产部署应配置 TLS、外部持久化、身份认证和更严格的限流。

## Third-Party Licenses

运行时依赖包括 FastAPI、Uvicorn、Pydantic、HTTPX、Jinja2、PyYAML、pathspec 与 keyring；构建产物不复制其源码。发布或再分发前请依据锁定版本的元数据检查并保留各依赖许可证。
