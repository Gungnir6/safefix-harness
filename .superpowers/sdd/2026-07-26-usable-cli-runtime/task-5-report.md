# Task 5 Report: Fresh-Install Distribution, Documentation, and Final Evidence

## 状态

Task 5 的本地交付项已完成：CI fresh-wheel smoke、声明式构建依赖、真实 CLI 中文教程、Windows fresh-wheel 安装、exact Mock 用户旅程、PLAN/AGENT_LOG 证据和本地质量门禁均已执行。Docker build/container demo 因本机 daemon 未运行而阻塞；当前提交尚无 GitHub Actions、GitHub Release 或 Render 部署外部结果，未宣称成功。

## 改动

- `.github/workflows/ci.yml`
  - 在现有 pytest、Ruff、mypy 后新增 Linux clean venv wheel smoke。
  - 保留 Gitleaks、Docker build/push 和原有 job 依赖。
- `pyproject.toml`
  - dev extra 新增 `build>=1.2,<2`。
  - fresh wheel 暴露 demo validator 缺失后，将既有 `pytest>=8.3,<9` 约束从仅 dev extra 移到运行时依赖；未改变版本范围。
- `README.md`
  - 记录 Python 3.12、wheel/Release 安装、完整 config/keyring 流程、隔离默认值、结果/审计位置、`--in-place` 风险、审批、JSON、provider 限制、packaged Mock、WebUI 和 Docker。
  - 明确 Mock 是固定动作验收 harness，不是通用智能模型。
  - 明确当前提交没有可验证的 CI、Release 或公网 Render URL。
- `tests/integration/test_distribution_metadata.py`
  - 增加 demo validator 必须属于运行时依赖的回归。
- `PLAN.md` / `AGENT_LOG.md`
  - 记录 Tasks 1–5 的真实提交和 RED/GREEN/门禁证据，并逐项映射 12 条设计验收标准。

## Fresh-wheel 证据

仓库测试与构建工具使用：

```text
C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe
```

fresh-install smoke 使用 `.smoke-venv\Scripts\python.exe` 和该环境生成的 launcher；构建开始及每组 smoke 前均显式删除 `PYTHONPATH` 并断言变量不存在。仓库源码测试才设置 `PYTHONPATH=<worktree>/src`，不再把它用于 wheel 来源证明。没有调用系统 Python。

构建：

```powershell
python.exe -m build --wheel
```

结果：

```text
Successfully built safefix_harness-0.1.0-py3-none-any.whl
```

clean Windows venv 安装后：

```text
safefix_file=.smoke-venv\Lib\site-packages\safefix\__init__.py
fixture=.smoke-venv\Lib\site-packages\safefix\_fixtures\python_bug
mock_script=.smoke-venv\Lib\site-packages\safefix\_fixtures\mock_repair.jsonl
public_demo_entry=safefix.cli:public_demo_main
launcher OK: safefix.exe
launcher OK: safefix-demo.exe
launcher OK: safefix-public-demo.exe
safefix --help: exit 0
guardrail: PASS
feedback: PASS
approval: PASS
```

## Fresh-wheel RED / GREEN

首次 wheel 构建和安装成功，三个启动器均存在且 help exit 0，但：

```text
guardrail: PASS
AssertionError at passing_feedback.category is VALIDATION_SUCCESS
safefix-demo all: exit 1
```

根因证据：

```text
fresh venv python = .smoke-venv\Scripts\python.exe
pytest_spec = None
```

`safefix.demo` 通过该解释器执行 `-m pytest -q`，而 pytest 当时只在 dev extra。新增回归后的 RED：

```text
FAILED test_runtime_dependencies_include_the_demo_validator
assert any(requirement.startswith("pytest") for requirement in requirements)
```

把原有 pytest 约束移至运行时依赖后的 GREEN：

```text
1 passed, 1 existing third-party warning
```

首轮重建后的入口命令继承了工作树 `PYTHONPATH`，因此不能单独证明加载自 wheel；依赖 RED/GREEN 仍由 metadata 回归覆盖。审查修复轮以清除 `PYTHONPATH`、打印模块来源和 packaged resources 的新证据替代该入口来源结论。

## Source-checkout exact Mock CLI journey

使用工作树内 `SAFEFIX_DATA_DIR` 执行：

```powershell
safefix.exe config init .manual-safefix.yaml
safefix.exe config validate .manual-safefix.yaml
safefix.exe run examples\python_bug --task "修复失败的加法测试" --config .manual-safefix.yaml --provider mock --mock-script examples\mock_repair.jsonl
```

结果：

- exit 0；
- 原 `examples/python_bug/calculator.py` 的 SHA-256 前后相同；
- 输出包含“运行模式：隔离副本”、首次“验证失败”、`apply_patch`、后续“验证通过”、`修改文件: calculator.py`、`运行结果: SUCCESS` 和“审计数据库”；
- 结果副本包含 `return left + right`；
- 审计 SQLite 文件存在；
- 输出未匹配 capability、CSRF、approval token、traceback、API key；
- `PYTHONUTF8=1` 只用于 Windows PowerShell native-pipe 的可读中文捕获，没有改变 CLI 运行逻辑。

## Review fix round 1: unpolluted wheel evidence

重建 wheel 和 fresh venv 后，每组命令先执行：

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
if (Test-Path Env:PYTHONPATH) { throw "PYTHONPATH still set" }
```

来源与资源检查结果：

```text
safefix_file=C:\...\usable-cli-runtime\.smoke-venv\Lib\site-packages\safefix\__init__.py
fixture=C:\...\usable-cli-runtime\.smoke-venv\Lib\site-packages\safefix\_fixtures\python_bug
mock_script=C:\...\usable-cli-runtime\.smoke-venv\Lib\site-packages\safefix\_fixtures\mock_repair.jsonl
public_demo_entry=safefix.cli:public_demo_main
```

检查同时断言 `safefix.__file__` 是 fresh venv `site-packages` 的后代且不是工作树 `src` 的后代；两个 packaged resources、fixture 的 `calculator.py` 及 public-demo console entry 都可加载。

无 `PYTHONPATH` 的入口结果：

```text
launcher OK: safefix.exe
launcher OK: safefix-demo.exe
launcher OK: safefix-public-demo.exe
safefix --help: exit 0
guardrail: PASS
feedback: PASS
approval: PASS
```

public-demo 采用 console entry 聚焦 import 检查，避免启动无法自动退出的长驻服务。

packaged Mock journey 将 site-packages 中的 fixture/script 复制到工作树临时目录，再使用 `.smoke-venv` launcher 执行 README 等价流程。结果：

```text
RunExit=0
PYTHONPATHPresent=False
PackagedInputCopied=True
PackagedScriptCopied=True
SourceCopyUnchanged=True
IsolatedWorkspace=True
FixedAddition=True
HasFailure=True
HasPatch=True
HasPass=True
HasSuccess=True
AuditDatabaseExists=True
LeaksTraceback=False
LeaksCapability=False
```

分发依赖测试使用 `packaging.Requirement` 解析每个 requirement，再经 `canonicalize_name` 精确检查 `pytest`，不再接受 `pytest-fake` 一类前缀。README 也明确 pytest 用于内置 feedback/Mock 验收和 `config init` 的默认 validator。

## 第一轮完整本地验证

```text
pytest -q:
942 passed, 2 skipped, 1 warning in 58.59s

ruff check .:
All checks passed!

mypy src:
Success: no issues found in 33 source files

python -m safefix.demo all:
guardrail: PASS
feedback: PASS
approval: PASS

git diff --check:
exit 0
```

唯一 warning 是既有 FastAPI TestClient 的 `StarletteDeprecationWarning`。

## Docker 与外部状态

Docker 探测：

```text
failed to connect to the docker API at npipe:////./pipe/docker_engine
The system cannot find the file specified.
```

本机 Docker daemon 未运行，因此没有 image build 或 container demo 结果。GitHub Actions、Release 和 Render 部署需要集成后外部账户状态；当前未执行、未伪称成功。

## 清理

删除 `.smoke-venv`、`dist`、`.manual-data` 和 `.manual-safefix.yaml` 前，均通过绝对路径检查证明其为当前 worktree 的严格后代；清理后没有生成物残留。

## 提交

提交信息：

```text
docs(cli): 完成真实运行与分发说明
```

精确 SHA 由提交完成回执记录。

## Final review fixes（2026-07-29，轮次 1/5）

- 连续审批：`TaskService` 在 create/approve/reject 成功取得 snapshot 后统一清理旧 access，并在仍为 `AWAITING_APPROVAL` 时从同一 loop 单次取出新 capability、读取新请求并生成新 CSRF。恢复调用抛错发生在刷新前，因此当前 access 保留。真实 `AgentLoop` 回归覆盖批准与拒绝两条恢复路径、双危险动作、不同 capability、旧 capability replay 失败和最终成功。
- 数据边界：原地模式不再绕过解析后的 data-dir 边界；运行时在创建目录和 SQLite 前防御性检查解析后的 data/database path 均不位于 prepared workspace。source、source/data 和 `..` 解析别名均 fail closed，且断言未生成 `safefix.sqlite3`。
- 终端边界：所有普通事件动态 key/value/type、摘要 stop reason/changed files/workspace/audit path 及 banner provider/model/path 均先编码 Unicode Cc/Cf、再有界截断；中文保持可读，事件 payload 为终端安全 JSON。`--json` 分支仍直接序列化原始摘要，反序列化后保留原值。
- 审批规则：审批领域请求携带持久化 `rule_ids`，CLI 安全编码并有界展示；空规则显示“无/未知”。既有 Web API 继续显式返回原字段集合。
- 验证：244 个受影响 unit passed；64 个 integration/Web passed（1 条既有 Starlette warning）；Ruff passed；mypy 对 6 个变更源码 passed；diff check passed。按上游要求未运行全套。
