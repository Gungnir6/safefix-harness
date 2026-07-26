# SafeFix 演示结果可视化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让公开 WebUI 不展开技术详情也能直观看到三个演示中的失败、拦截、修正、审批和最终通过。

**Architecture:** `PublicDemoService` 继续运行原有确定性机制，只为稳定事件码附加展示状态，并按运行保存只读的场景结论与关键证据。路由把结论加入公开运行响应和 Jinja 上下文；模板及原生 JavaScript使用相同的 `state`/`state_label` 渲染结论卡与状态徽标，不改变审计、策略、审批或工具执行语义。

**Tech Stack:** Python 3.12、FastAPI、Jinja2、原生 JavaScript/CSS、pytest、FastAPI TestClient。

## Global Constraints

- 只修改公开演示的展示元数据和结果页，不改变核心策略、审批状态机、工具调用或真实本地运行语义。
- 公开演示继续只使用内置 fixture 和 Mock，不接收项目路径、真实模型或 API Key。
- 事件保留原有 `code` 和 `summary`，新增 `state` 与 `state_label`；未知事件使用 `info`/`信息`。
- 状态集合固定为 `blocked`、`failed`、`pending`、`changed`、`passed`、`info`。
- 服务端模板依赖自动转义；客户端只使用 `createElement`/`textContent`，禁止 `innerHTML`、`insertAdjacentHTML` 和 `document.write`。
- 760px 以下单列，不只用颜色表达状态，并尊重 `prefers-reduced-motion`。
- 使用仓库根虚拟环境 `C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe`，不要调用系统 `python.exe`。

---

### Task 1: 公开演示结论与事件展示元数据

**Files:**
- Modify: `src/safefix/demo.py`
- Modify: `src/safefix/web/routes.py`
- Test: `tests/web/test_api.py`
- Test: `tests/integration/test_demo.py`

**Interfaces:**
- Produces: `PublicDemoService.get_presentation(run_id: str) -> dict[str, object]`
- Produces: 公开 `POST /api/runs` 与 `GET /api/runs/{run_id}` 响应中的 `presentation`
- Produces: 演示事件 payload 中的 `state: str` 与 `state_label: str`
- Consumes: 原有 `DemoResult.events` 稳定机器码序列

- [ ] **Step 1: 写出三个场景的失败 API 测试**

在 `tests/web/test_api.py` 增加参数化测试，真实调用公开 API：

```python
@pytest.mark.parametrize(
    ("scenario", "states", "evidence_count"),
    [
        ("guardrail", ["blocked", "blocked", "passed"], 3),
        ("feedback", ["failed", "changed", "failed", "changed", "passed"], 4),
        (
            "approval",
            ["pending", "info", "blocked", "passed", "blocked", "passed"],
            4,
        ),
    ],
)
def test_public_demo_exposes_verdict_evidence_and_event_states(
    scenario: str, states: list[str], evidence_count: int
) -> None:
    client = TestClient(
        create_app(AppDependencies(service=PublicDemoService(), public_demo=True))
    )

    created = client.post("/api/runs", json={"task": scenario})
    run_id = created.json()["run_id"]
    events = client.get(f"/api/runs/{run_id}/events").json()

    assert created.json()["presentation"]["verdict"] == "机制验证通过"
    assert len(created.json()["presentation"]["evidence"]) == evidence_count
    assert [event["payload"]["state"] for event in events] == states
    assert all(event["payload"]["state_label"] for event in events)
```

在 `tests/integration/test_demo.py` 增加断言，确保展示字段没有改变原有事件机器码及三个 demo 的 PASS 结果。

- [ ] **Step 2: 运行测试确认 RED**

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m pytest tests/web/test_api.py::test_public_demo_exposes_verdict_evidence_and_event_states -q
```

预期：FAIL，因为创建运行响应没有 `presentation`，事件 payload 没有 `state`。

- [ ] **Step 3: 增加固定展示映射和运行级结论**

在 `src/safefix/demo.py` 增加固定映射：

```python
_EVENT_PRESENTATION = {
    "POLICY:DENY": ("blocked", "已拦截"),
    "RULE:CMD_PRIVILEGE_ESCALATION": ("blocked", "规则命中"),
    "TOOL_CALLS:0": ("passed", "已验证"),
    "VALIDATION:FAIL": ("failed", "验证失败"),
    "PATCH:WRONG": ("changed", "尝试修正"),
    "VALIDATION:STILL_FAIL": ("failed", "仍未通过"),
    "PATCH:CORRECT": ("changed", "已修正"),
    "VALIDATION:PASS": ("passed", "验证通过"),
    "APPROVAL:PENDING": ("pending", "等待审批"),
    "STORE:REOPENED": ("info", "状态恢复"),
    "ACTION_MISMATCH:BLOCKED": ("blocked", "篡改拦截"),
    "APPROVAL:APPROVED": ("passed", "批准一次"),
    "TOKEN_REPLAY:BLOCKED": ("blocked", "复用拒绝"),
    "TOOL_CALLS:1": ("passed", "执行一次"),
}

_SCENARIO_PRESENTATION = {
    "guardrail": {
        "verdict": "机制验证通过",
        "conclusion": "危险命令在进入工具层前被确定性策略拒绝。",
        "evidence": (
            "危险命令已拒绝",
            "命中禁止提权规则",
            "工具调用次数为零",
        ),
    },
    "feedback": {
        "verdict": "机制验证通过",
        "conclusion": "系统把客观验证失败回灌给循环，并据此生成正确修复。",
        "evidence": (
            "首次验证失败",
            "错误补丁仍未通过",
            "根据反馈完成修正",
            "最终验证通过",
        ),
    },
    "approval": {
        "verdict": "机制验证通过",
        "conclusion": "高风险动作只对冻结的原始动作授予一次性能力。",
        "evidence": (
            "进入人工检查点",
            "篡改动作被拦截",
            "原动作只批准一次",
            "授权复用被拒绝",
        ),
    },
}
```

`PublicDemoService` 新增 `_presentations`，创建运行时保存当前场景的只读副本：

```python
self._presentations: dict[str, dict[str, object]] = {}

state, state_label = _EVENT_PRESENTATION.get(message, ("info", "信息"))
payload = {
    "code": message,
    "summary": _EVENT_SUMMARIES.get(message, message),
    "state": state,
    "state_label": state_label,
}

def get_presentation(self, run_id: str) -> dict[str, object]:
    self.get(run_id)
    return self._presentations[run_id]
```

禁止从摘要字符串猜测状态。

- [ ] **Step 4: 将 presentation 加入公开运行响应**

在 `src/safefix/web/routes.py` 提取不改变本地响应的帮助函数：

```python
def _run_response(service: Any, snapshot: Any, public_demo: bool) -> dict[str, Any]:
    body = dict(_json(snapshot))
    if public_demo and hasattr(service, "get_presentation"):
        body["presentation"] = service.get_presentation(snapshot.run_id)
    return body
```

公开和本地共用 `POST /api/runs`、`GET /api/runs/{run_id}`，但只有公开 demo 服务响应包含 `presentation`。运行页上下文增加：

```python
"presentation": (
    service.get_presentation(run_id)
    if dependencies.public_demo and hasattr(service, "get_presentation")
    else None
),
```

公开 `SUCCESS` 的友好标签改为“机制验证通过”，机器码继续为 `SUCCESS`。

- [ ] **Step 5: 运行 Task 1 GREEN 与回归**

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m pytest tests/web/test_api.py tests/integration/test_demo.py -q
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m ruff check src/safefix/demo.py src/safefix/web/routes.py tests/web/test_api.py tests/integration/test_demo.py
```

预期：相关测试全部 PASS；Ruff 无问题。

- [ ] **Step 6: 提交 Task 1**

```powershell
git add src/safefix/demo.py src/safefix/web/routes.py tests/web/test_api.py tests/integration/test_demo.py
git commit -m "feat(demo): 添加机制验证展示元数据"
```

---

### Task 2: 结论卡与状态化审计时间线

**Files:**
- Modify: `src/safefix/web/templates/run.html`
- Modify: `src/safefix/web/static/app.js`
- Modify: `src/safefix/web/static/app.css`
- Modify: `tests/web/test_pages.py`
- Modify: `README.md`
- Modify: `PLAN.md`
- Modify: `AGENT_LOG.md`

**Interfaces:**
- Consumes: Task 1 `presentation.verdict`、`presentation.conclusion`、`presentation.evidence`
- Consumes: 事件 payload 的 `state` 与 `state_label`
- Produces: `.demo-verdict`、`.evidence-grid`、`.event-state` 和 `data-state`

- [ ] **Step 1: 写结果页失败测试**

在 `tests/web/test_pages.py` 使用真实 `PublicDemoService` 分别创建三个场景，解析返回页面并断言：

```python
@pytest.mark.parametrize(
    ("scenario", "required_states"),
    [
        ("guardrail", {"blocked", "passed"}),
        ("feedback", {"failed", "changed", "passed"}),
        ("approval", {"pending", "blocked", "passed"}),
    ],
)
def test_public_result_page_explains_mechanism_outcome(
    scenario: str, required_states: set[str]
) -> None:
    service = PublicDemoService()
    client = TestClient(
        create_app(AppDependencies(service=service, public_demo=True))
    )
    created = client.post("/api/runs", json={"task": scenario}).json()
    page = client.get(f"/runs/{created['run_id']}")

    assert "机制验证结论" in page.text
    assert "机制验证通过" in page.text
    assert 'class="demo-verdict"' in page.text
    assert required_states <= set(
        re.findall(r'data-state="([^"]+)"', page.text)
    )
```

增加恶意 payload 测试，保证摘要与结论文本仍被转义。扩展 Node 行为测试，动态追加一个 `failed` 事件后断言：

```json
{
  "state": "failed",
  "stateLabel": "验证失败"
}
```

对应节点必须拥有 `data-state="failed"` 和文本徽标“验证失败”。

- [ ] **Step 2: 运行页面测试确认 RED**

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m pytest tests/web/test_pages.py -q
```

预期：FAIL，因为结果页没有结论卡、状态徽标或 `data-state`。

- [ ] **Step 3: 渲染结论卡与服务端状态徽标**

在 `run.html` 的运行头部之后、错误提示之前，仅当 `presentation` 存在时渲染：

```jinja2
<section class="demo-verdict" aria-labelledby="verdict-title">
  <div class="verdict-mark" aria-hidden="true">✓</div>
  <div class="verdict-copy">
    <p class="eyebrow">机制验证结论</p>
    <h2 id="verdict-title">{{ presentation.verdict }}</h2>
    <p>{{ presentation.conclusion }}</p>
  </div>
  <ul class="evidence-grid" aria-label="关键证据">
    {% for evidence in presentation.evidence %}
    <li><span aria-hidden="true">→</span>{{ evidence }}</li>
    {% endfor %}
  </ul>
</section>
```

服务端事件 `<li>` 增加安全白名单后的状态：

```jinja2
{% set state = event.redacted_payload.state or "info" %}
<li class="event event-{{ event.event_type | lower }}"
    data-sequence="{{ event.sequence }}"
    data-event-type="{{ event.event_type }}"
    data-state="{{ state }}">
...
<span class="event-state">{{ event.redacted_payload.state_label or "信息" }}</span>
```

只接受 Task 1 产生的固定状态；未知值在路由或模板中回退 `info`。

- [ ] **Step 4: 让动态时间线结构与服务端一致**

在 `app.js` 中增加安全状态白名单：

```javascript
const demoStates = new Set([
  "blocked", "failed", "pending", "changed", "passed", "info"
]);

function demoState(payload) {
  return demoStates.has(payload?.state) ? payload.state : "info";
}
```

`appendEvents` 为动态事件设置：

```javascript
const state = demoState(event.payload);
item.dataset.state = state;
const badge = document.createElement("span");
badge.className = "event-state";
badge.textContent = event.payload?.state_label || "信息";
meta.appendChild(badge);
```

公开 `SUCCESS` 的客户端标签同步改为“机制验证通过”。禁止使用 HTML 字符串插入。

- [ ] **Step 5: 添加工业控制台视觉样式**

在 `app.css` 沿用现有变量，增加：

```css
.demo-verdict {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 20px 28px;
  margin-top: 32px;
  padding: 24px;
  border: 1px solid color-mix(in srgb, var(--acid) 45%, var(--line));
  background: linear-gradient(135deg, rgba(166, 255, 74, .08), transparent 58%);
}
.evidence-grid {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
}
.event-state {
  display: inline-flex;
  border: 1px solid currentColor;
  padding: 3px 7px;
  font-size: 10px;
  letter-spacing: .08em;
}
.event[data-state="blocked"] { --event-accent: #ff5f57; }
.event[data-state="failed"] { --event-accent: #ff9f43; }
.event[data-state="pending"] { --event-accent: var(--amber); }
.event[data-state="changed"] { --event-accent: var(--cyan); }
.event[data-state="passed"] { --event-accent: var(--acid); }
.event[data-state="info"] { --event-accent: var(--muted); }
.event-body { border-left-color: var(--event-accent, var(--line)); }
.event-state { color: var(--event-accent, var(--muted)); }
```

在现有移动端媒体查询中把 `.demo-verdict` 和 `.evidence-grid` 改为单列；现有 reduced-motion 规则继续覆盖新增元素，不添加自动播放。

- [ ] **Step 6: 运行 Task 2 GREEN 与安全扫描**

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m pytest tests/web -q
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m ruff check src/safefix/web tests/web
$matches = rg -n "innerHTML|insertAdjacentHTML|document\\.write" src/safefix/web
if ($LASTEXITCODE -eq 1) { "DOM safety scan: no matches" }
```

预期：Web 测试全部 PASS；Ruff 无问题；安全扫描无匹配。

- [ ] **Step 7: 更新交付文档**

- `README.md`：说明公开结果页会明确展示中间失败与最终机制结论。
- `PLAN.md`：记录 RED、GREEN、完整回归和浏览器验收。
- `AGENT_LOG.md`：记录用户首次体验反馈“看不见失败”、设计决策、技能、测试与提交。

- [ ] **Step 8: 完整验证与真实浏览器验收**

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m pytest -q
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m ruff check .
& "C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe" -m mypy src
git diff --check
```

浏览器分别运行 `guardrail`、`feedback`、`approval`：

- 不展开详情即可读出关键证据。
- 反馈时间线清楚显示失败、尝试修正、仍失败、已修正、验证通过。
- 状态徽标含中文文字，窄屏不横向溢出。
- 控制台无错误。

- [ ] **Step 9: 提交 Task 2**

```powershell
git add src/safefix/web/templates/run.html src/safefix/web/static/app.js src/safefix/web/static/app.css tests/web/test_pages.py README.md PLAN.md AGENT_LOG.md
git commit -m "feat(web): 突出演示失败与机制结论"
```

