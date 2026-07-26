# SafeFix 中文引导演示 WebUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 SafeFix 全部 WebUI 中文化，并让公开演示首页和结果页直接解释每个安全机制证明了什么。

**Architecture:** 保持 FastAPI、Jinja2 和原有 API 不变。`PublicDemoService` 为确定性演示事件增加稳定机器码与中文摘要；路由向模板提供状态和事件类型的固定中文映射；模板负责可访问的中文结构，JavaScript 只负责提交、轮询和以 `textContent` 安全追加动态内容。

**Tech Stack:** Python 3.12、FastAPI、Jinja2、原生 JavaScript、CSS、pytest、FastAPI TestClient。

## Global Constraints

- 不修改策略、审批、工具、审计或 API 的安全语义。
- 公开模式不访问真实项目、不调用真实模型、不接收 project path、provider 或 API Key。
- 动态内容只能经 Jinja 自动转义或 `textContent` 渲染，禁止 `innerHTML`。
- 未知状态或错误保留稳定机器码，不能用模糊中文文本吞掉诊断信息。
- 本地模式必须继续提供项目路径、provider 和修复目标输入。
- 保留键盘操作、焦点样式、`aria-live` 和移动端响应式布局。

---

### Task 1: 为公开演示提供中文展示数据

**Files:**
- Modify: `src/safefix/demo.py`
- Modify: `tests/web/test_api.py`

**Interfaces:**
- Consumes: `PublicDemoService.create(*, task: str, project_path: str, **_: Any) -> RunSnapshot`
- Produces: 公开演示事件 payload `{"code": str, "summary": str}`，以及中文 `RunSnapshot.description`

- [ ] **Step 1: Write the failing API behavior test**

在 `tests/web/test_api.py` 增加真实服务测试：

```python
def test_public_demo_exposes_chinese_explanations_and_machine_codes() -> None:
    client = TestClient(
        create_app(AppDependencies(service=PublicDemoService(), public_demo=True))
    )

    created = client.post("/api/runs", json={"task": "feedback"})
    run_id = created.json()["run_id"]
    events = client.get(f"/api/runs/{run_id}/events").json()

    assert created.json()["description"] == "验证反馈演示"
    assert events[0]["payload"] == {
        "code": "VALIDATION:FAIL",
        "summary": "初次验证失败，系统获得了客观错误反馈。",
    }
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest tests/web/test_api.py::test_public_demo_exposes_chinese_explanations_and_machine_codes -q
```

Expected: FAIL because the current description is `feedback demo` and payload only contains `message`.

- [ ] **Step 3: Add deterministic presentation mappings**

在 `src/safefix/demo.py` 增加固定映射：

```python
_SCENARIO_TITLES = {
    "guardrail": "安全边界演示",
    "feedback": "验证反馈演示",
    "approval": "一次性审批演示",
}

_EVENT_SUMMARIES = {
    "POLICY:DENY": "危险命令被安全策略拒绝。",
    "RULE:CMD_PRIVILEGE_ESCALATION": "命中了禁止提权的永久规则。",
    "TOOL_CALLS:0": "危险动作没有进入工具执行层。",
    "VALIDATION:FAIL": "初次验证失败，系统获得了客观错误反馈。",
    "PATCH:WRONG": "第一次修复不正确，系统不会把修改当作成功。",
    "PATCH:CORRECT": "系统根据验证反馈生成了正确补丁。",
    "VALIDATION:PASS": "再次验证通过，修复完成。",
    "APPROVAL:PENDING": "敏感写操作暂停，等待人工决定。",
    "STORE:REOPENED": "审批状态持久化后可以安全恢复。",
    "ACTION_MISMATCH:BLOCKED": "被篡改的动作与原批准对象不一致，已拦截。",
    "APPROVAL:APPROVED": "冻结的原始动作获得一次性批准。",
    "TOKEN_REPLAY:BLOCKED": "重复使用审批能力被拒绝。",
    "TOOL_CALLS:1": "只有获得批准的原始动作执行了一次。",
}
```

`PublicDemoService.create()` 使用中文标题，并将每个事件保存为：

```python
redacted_payload={
    "code": message,
    "summary": _EVENT_SUMMARIES.get(message, message),
}
```

- [ ] **Step 4: Verify GREEN**

Run the focused test and all API tests:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest tests/web/test_api.py -q
```

Expected: all tests PASS.

---

### Task 2: 中文化首页、结果页、审批和设置页

**Files:**
- Modify: `src/safefix/web/routes.py`
- Modify: `src/safefix/web/templates/base.html`
- Modify: `src/safefix/web/templates/index.html`
- Modify: `src/safefix/web/templates/run.html`
- Modify: `src/safefix/web/templates/settings.html`
- Modify: `src/safefix/web/static/app.css`
- Modify: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: Task 1 的 `payload.code`、`payload.summary` 和中文 `description`
- Produces: 模板 context 中的 `status_label: str`、`event_labels: dict[str, str]`

- [ ] **Step 1: Write failing page tests**

更新并新增页面行为断言：

```python
def test_public_home_is_a_chinese_guided_demo_without_fake_task_input() -> None:
    client = TestClient(
        create_app(AppDependencies(service=PageService(), public_demo=True))
    )
    response = client.get("/")

    assert "安全地修复代码" in response.text
    assert "安全边界" in response.text
    assert "验证反馈" in response.text
    assert "一次性审批" in response.text
    assert 'name="task"' not in response.text
    assert 'name="scenario"' in response.text
    assert "不访问真实项目" in response.text


def test_run_page_explains_status_and_keeps_escaped_technical_details() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))
    response = client.get("/runs/run-1")

    assert "等待人工批准" in response.text
    assert "策略判断" in response.text
    assert "查看技术细节" in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
```

同时保留并加强本地模式测试：

```python
assert 'name="task"' in response.text
assert 'name="project_path"' in response.text
assert 'name="provider"' in response.text
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest tests/web/test_pages.py -q
```

Expected: FAIL on missing Chinese copy, public task textarea still present, and missing technical details disclosure.

- [ ] **Step 3: Add fixed presentation mappings to the route**

在 `src/safefix/web/routes.py` 增加：

```python
_STATUS_LABELS = {
    RunStatus.CREATED: "已创建",
    RunStatus.RUNNING: "运行中",
    RunStatus.AWAITING_APPROVAL: "等待人工批准",
    RunStatus.SUCCESS: "演示成功",
    RunStatus.BLOCKED: "已被安全策略拦截",
    RunStatus.NO_PROGRESS: "没有取得进展",
    RunStatus.BUDGET_EXCEEDED: "已达到执行预算",
    RunStatus.FAILED: "运行失败",
    RunStatus.CANCELLED: "已取消",
}

_EVENT_LABELS = {
    "MODEL_REQUEST": "模型请求",
    "POLICY_DECISION": "策略判断",
    "TOOL_RESULT": "工具结果",
    "DEMO_EVENT": "演示步骤",
}
```

`run_page()` context 增加：

```python
"status_label": _STATUS_LABELS.get(snapshot.status, snapshot.status.value),
"event_labels": _EVENT_LABELS,
```

- [ ] **Step 4: Implement Chinese templates and scenario cards**

- `base.html`：设置 `lang="zh-CN"`，中文导航、跳转链接和页脚。
- `index.html`：公开模式渲染三个 radio 场景卡和安全声明，不渲染 task textarea；本地模式继续渲染原输入并中文化。
- `run.html`：使用 `status_label`；事件标题使用 `event_labels.get(...)`；优先显示 `payload.summary`；在 `<details>` 中用 `<pre>` 显示完整脱敏 payload。
- `settings.html`：中文化凭据、来源、项目记忆和清理按钮。
- `app.css`：新增 `.scenario-grid`、`.scenario-card`、`.demo-promise`、`.event-summary` 和 `details` 样式；保持现有色彩与移动端断点。

- [ ] **Step 5: Verify page tests GREEN**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest tests/web/test_pages.py tests/web/test_api.py -q
```

Expected: all tests PASS and malicious tool output remains escaped.

---

### Task 3: 中文化交互反馈并完成交付验证

**Files:**
- Modify: `src/safefix/web/static/app.js`
- Modify: `README.md`
- Modify: `PLAN.md`
- Modify: `AGENT_LOG.md`

**Interfaces:**
- Consumes: existing API response fields and stable error codes
- Produces: user-visible Chinese form, polling, approval, cancel and memory-clear feedback

- [ ] **Step 1: Localize stable frontend messages**

在 `app.js` 增加固定错误翻译：

```javascript
const errorMessages = {
  INVALID_STATE: "当前状态不能执行这个操作",
  RUN_NOT_FOUND: "找不到这次运行",
  PUBLIC_INPUT_FORBIDDEN: "公开演示不接受项目路径或真实模型",
  RATE_LIMITED: "操作太频繁，请稍后再试",
  ACTIVE_RUN_LIMIT: "已有演示正在运行，请稍后再试",
  CSRF_INVALID: "安全校验失败，请刷新页面重试"
};

function explainError(code) {
  return errorMessages[code] ? `${errorMessages[code]}（${code}）` : code;
}
```

将启动、轮询、审批、取消和记忆清理反馈改为中文，并继续通过 `textContent` 写入。

- [ ] **Step 2: Run static security and Web tests**

Run:

```powershell
rg -n "innerHTML|insertAdjacentHTML|document\\.write" src/safefix/web
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest tests/web -q
```

Expected: dangerous DOM insertion scan has zero matches; Web tests PASS.

- [ ] **Step 3: Run all three demos through the real API**

Start the server and submit `guardrail`, `feedback`, and `approval` to `/api/runs`. For each response, verify HTTP 202, `status == "SUCCESS"` and a non-empty event list containing both `code` and `summary`.

- [ ] **Step 4: Refresh the browser and inspect the rendered UI**

Verify:

- Public homepage has three keyboard-selectable cards and no task textarea.
- Each scenario reaches a Chinese result page.
- Each timeline shows a Chinese summary and expandable technical details.
- Mobile-width layout remains readable.
- Browser console has no errors.

- [ ] **Step 5: Update project documentation**

- `README.md`：说明公开 WebUI 是中文引导演示，并列出三个机制。
- `PLAN.md`：记录 RED/GREEN、完整回归和浏览器验收。
- `AGENT_LOG.md`：记录首次使用反馈、设计选择、实现和验证证据。

- [ ] **Step 6: Run full verification**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m mypy src
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit and push**

```powershell
git add src/safefix/demo.py src/safefix/web tests/web README.md PLAN.md AGENT_LOG.md
git commit -m "feat(web): 添加中文引导演示界面"
git push origin main
```

Monitor GitHub Actions until both `test-quality` and `image` jobs succeed.
