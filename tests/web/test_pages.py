from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from safefix.domain import RunStatus
from safefix.governance.audit import AuditStore
from safefix.web.app import AppDependencies, create_app
from tests.web.test_api import FakeService, _snapshot


class _PageParser(HTMLParser):
    _VOID_ELEMENTS = {"input", "meta", "link", "br", "hr", "img"}

    def __init__(self) -> None:
        super().__init__()
        self.roots: list[dict[str, object]] = []
        self._stack: list[dict[str, object]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node: dict[str, object] = {
            "tag": tag,
            "attrs": dict(attrs),
            "children": [],
            "text": [],
        }
        if self._stack:
            self._stack[-1]["children"].append(node)  # type: ignore[union-attr]
        else:
            self.roots.append(node)
        if tag not in self._VOID_ELEMENTS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        while self._stack:
            node = self._stack.pop()
            if node["tag"] == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1]["text"].append(data)  # type: ignore[union-attr]


def _parse_page(html: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(html)
    return parser


def _find_nodes(
    nodes: list[dict[str, object]], tag: str
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for node in nodes:
        if node["tag"] == tag:
            matches.append(node)
        matches.extend(
            _find_nodes(node["children"], tag)  # type: ignore[arg-type]
        )
    return matches


def _node_text(node: dict[str, object]) -> str:
    chunks = list(node["text"])  # type: ignore[arg-type]
    for child in node["children"]:  # type: ignore[union-attr]
        chunks.append(_node_text(child))
    return "".join(chunks).strip()


class PageService(FakeService):
    def __init__(self, credential_source: str = "keyring") -> None:
        super().__init__()
        self.credential_source = credential_source

    def list_events(self, run_id: str) -> list[object]:
        del run_id
        return [
            SimpleNamespace(
                sequence=1,
                event_type="MODEL_REQUEST",
                redacted_payload={"summary": "检查项目"},
                created_at=datetime.now(UTC),
            ),
            SimpleNamespace(
                sequence=2,
                event_type="POLICY_DECISION",
                redacted_payload={
                    "rule_ids": ["CMD_GIT_WRITE"],
                    "reason": "Git 写入需要一次性批准",
                },
                created_at=datetime.now(UTC),
            ),
            SimpleNamespace(
                sequence=3,
                event_type="TOOL_RESULT",
                redacted_payload={"output": "<script>alert(1)</script>"},
                created_at=datetime.now(UTC),
            ),
        ]

    def credential_status(self, provider: str) -> object:
        return SimpleNamespace(
            provider=provider,
            configured=True,
            source=self.credential_source,
            warning=None,
        )


class SuccessPageService(PageService):
    def get(self, run_id: str) -> object:
        del run_id
        return _snapshot(status=RunStatus.SUCCESS)


class EventLabelPageService(PageService):
    def list_events(self, run_id: str) -> list[object]:
        event_types = (
            "MODEL_REQUEST",
            "ACTION",
            "POLICY_DECISION",
            "TOOL_RESULT",
            "APPROVAL_REQUESTED",
            "APPROVAL_APPROVED",
            "APPROVAL_EXPIRED",
            "APPROVAL_REJECTED",
            "APPROVAL_CANCELLED",
            "DEMO_EVENT",
            "FUTURE_EVENT",
        )
        connection = sqlite3.connect(":memory:")
        try:
            store = AuditStore(connection)
            for event_type in event_types:
                store.append(run_id, event_type, {"code": event_type})
            return store.list_events(run_id)
        finally:
            connection.close()


def test_local_home_is_accessible_and_has_task_controls() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/")

    assert response.status_code == 200
    assert "<h1" in response.text
    assert 'for="task"' in response.text
    assert 'name="task"' in response.text
    assert 'name="project_path"' in response.text
    assert 'name="provider"' in response.text
    assert 'aria-live="polite"' in response.text


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


def test_public_guardrail_card_describes_the_real_command_policy_demo() -> None:
    client = TestClient(
        create_app(AppDependencies(service=PageService(), public_demo=True))
    )

    response = client.get("/")

    assert "提权破坏命令" in response.text
    assert "策略拒绝" in response.text
    assert "工具执行次数为零" in response.text
    assert "越界路径" not in response.text


def test_public_home_has_exactly_three_named_radio_scenarios() -> None:
    client = TestClient(
        create_app(AppDependencies(service=PageService(), public_demo=True))
    )

    response = client.get("/")
    inputs = _find_nodes(_parse_page(response.text).roots, "input")
    scenarios = [
        node["attrs"]
        for node in inputs
        if node["attrs"].get("name") == "scenario"  # type: ignore[union-attr]
    ]

    assert len(scenarios) == 3
    assert all(item["type"] == "radio" for item in scenarios)
    assert {item["value"] for item in scenarios} == {
        "guardrail",
        "feedback",
        "approval",
    }


def test_client_script_runs_dynamic_status_timeline_and_cancel_behaviors() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser behavior test")
    client = TestClient(create_app(AppDependencies(service=PageService())))
    script = client.get("/static/app.js").text
    harness = """
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(0, "utf8");

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.dataset = {};
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = true;
    this.listeners = {};
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (selector === "[data-sequence]"
            && Object.hasOwn(child.dataset, "sequence")) {
          matches.push(child);
        }
        if (selector.startsWith(".")
            && child.className.split(" ").includes(selector.slice(1))) {
          matches.push(child);
        }
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
}

function makeResponse(body, ok = true) {
  return { ok, json: async () => body };
}

function createPage({
  initialSequences = [],
  placeholder = false,
  publicDemo = false,
  fetch
}) {
  const runHeader = new FakeElement("section");
  runHeader.dataset.runId = "run-1";
  runHeader.dataset.terminal = "false";
  runHeader.dataset.public = String(publicDemo);
  const status = new FakeElement("strong");
  const statusCode = new FakeElement("small");
  const timeline = new FakeElement("ol");
  const actionError = new FakeElement("div");
  const cancelButton = new FakeElement("button");
  cancelButton.textContent = "取消运行";
  if (placeholder) {
    const empty = new FakeElement("li");
    empty.className = "empty-state";
    timeline.appendChild(empty);
  }
  for (const sequence of initialSequences) {
    const event = new FakeElement("li");
    event.className = "event";
    event.dataset.sequence = String(sequence);
    timeline.appendChild(event);
  }
  const selectors = new Map([
    ["#run-form", null],
    ["[data-run-id]", runHeader],
    ["#run-status", status],
    ["#run-status-code", statusCode],
    ["#timeline", timeline],
    ["#action-error", actionError],
    ["#cancel-run", cancelButton],
    [".approval-panel", null],
    ["#clear-memory", null]
  ]);
  const scheduled = [];
  const context = {
    document: {
      createElement: (tagName) => new FakeElement(tagName),
      querySelector: (selector) => selectors.get(selector) || null,
      querySelectorAll: () => []
    },
    window: {
      location: { reload: () => {} },
      setTimeout: (callback) => { scheduled.push(callback); }
    },
    fetch,
    FormData: class {}
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return {
    actionError,
    cancelButton,
    context,
    scheduled,
    status,
    statusCode,
    timeline
  };
}

async function runTimelineScenario(initialSequences, placeholder, events) {
  const page = createPage({
    initialSequences,
    placeholder,
    fetch: async (url) => {
      if (url.endsWith("/events")) return makeResponse(events);
      return makeResponse({ status: "SUCCESS" });
    }
  });
  await page.scheduled[0]();
  return page;
}

(async () => {
  const synced = await runTimelineScenario(
    [1],
    false,
    [
      {
        sequence: 1,
        type: "MODEL_REQUEST",
        payload: { summary: "已有事件" },
        created_at: "2026-07-26T00:00:00Z"
      },
      {
        sequence: 2,
        type: "TOOL_RESULT",
        payload: { summary: "新增事件" },
        created_at: "2026-07-26T00:00:01Z"
      }
    ]
  );
  const populated = await runTimelineScenario(
    [],
    true,
    [{
      sequence: 3,
      type: "DEMO_EVENT",
      payload: { summary: "首个事件" },
      created_at: "2026-07-26T00:00:02Z"
    }]
  );
  const stillEmpty = await runTimelineScenario([], true, []);
  const fallbackTimeline = await runTimelineScenario(
    [],
    true,
    [
      "MODEL_REQUEST",
      "ACTION",
      "POLICY_DECISION",
      "TOOL_RESULT",
      "APPROVAL_REQUESTED",
      "APPROVAL_APPROVED",
      "APPROVAL_EXPIRED",
      "APPROVAL_REJECTED",
      "APPROVAL_CANCELLED",
      "DEMO_EVENT",
      "FUTURE_EVENT"
    ].map((type, index) => ({
      sequence: index + 10,
      type,
      payload: { code: type },
      created_at: "2026-07-26T00:00:03Z"
    }))
  );
  const publicStatus = createPage({
    publicDemo: true,
    fetch: async (url) => {
      if (url.endsWith("/events")) return makeResponse([]);
      return makeResponse({ status: "SUCCESS" });
    }
  });
  await publicStatus.scheduled[0]();

  let resolveCancel;
  const cancelling = createPage({
    fetch: (url) => {
      if (!url.endsWith("/cancel")) {
        return Promise.resolve(makeResponse({ status: "SUCCESS" }));
      }
      return new Promise((resolve) => { resolveCancel = resolve; });
    }
  });
  const clickEvent = { currentTarget: cancelling.cancelButton };
  const cancellation = cancelling.cancelButton.listeners.click(clickEvent);
  clickEvent.currentTarget = null;
  resolveCancel(makeResponse({ error: { code: "INVALID_STATE" } }, false));
  let cancellationError = null;
  try {
    await cancellation;
  } catch (error) {
    cancellationError = error.message;
  }

  process.stdout.write(JSON.stringify({
    errors: [
      "INVALID_STATE",
      "RUN_NOT_FOUND",
      "PUBLIC_INPUT_FORBIDDEN",
      "RATE_LIMITED",
      "ACTIVE_RUN_LIMIT",
      "CSRF_INVALID",
      "UNKNOWN",
      "constructor"
    ].map((code) => synced.context.explainError(code)),
    statuses: ["CREATED", "RUNNING", "AWAITING_APPROVAL", "SUCCESS"]
      .map((code) => synced.context.statusLabel(code, false)),
    publicSuccess: publicStatus.status.textContent,
    events: [
      "MODEL_REQUEST",
      "ACTION",
      "POLICY_DECISION",
      "TOOL_RESULT",
      "APPROVAL_REQUESTED",
      "APPROVAL_APPROVED",
      "APPROVAL_EXPIRED",
      "APPROVAL_REJECTED",
      "APPROVAL_CANCELLED",
      "DEMO_EVENT",
      "FUTURE_EVENT"
    ]
      .map((code) => synced.context.eventLabel(code)),
    syncedStatus: {
      label: synced.status.textContent,
      machineCode: synced.statusCode.textContent,
      dataStatus: synced.status.dataset.status
    },
    deduplicatedSequences: synced.timeline
      .querySelectorAll("[data-sequence]")
      .map((item) => item.dataset.sequence),
    syncedSummaries: synced.timeline
      .querySelectorAll(".event-summary")
      .map((item) => item.textContent),
    fallbackSummaries: fallbackTimeline.timeline
      .querySelectorAll(".event-summary")
      .map((item) => item.textContent),
    populatedTimeline: {
      emptyCount: populated.timeline.querySelectorAll(".empty-state").length,
      sequences: populated.timeline
        .querySelectorAll("[data-sequence]")
        .map((item) => item.dataset.sequence)
    },
    emptyTimeline: {
      emptyCount: stillEmpty.timeline.querySelectorAll(".empty-state").length,
      eventCount: stillEmpty.timeline.querySelectorAll("[data-sequence]").length
    },
    cancellation: {
      error: cancellationError,
      label: cancelling.cancelButton.textContent,
      disabled: cancelling.cancelButton.disabled,
      notice: cancelling.actionError.textContent
    }
  }));
})().catch((error) => {
  process.stderr.write(error.stack);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        [node, "-e", harness],
        input=script,
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "errors": [
            "当前状态不能执行这个操作 (INVALID_STATE)",
            "找不到这次运行 (RUN_NOT_FOUND)",
            "公开演示不接受项目路径或真实模型 (PUBLIC_INPUT_FORBIDDEN)",
            "操作太频繁，请稍后再试 (RATE_LIMITED)",
            "已有演示正在运行，请稍后再试 (ACTIVE_RUN_LIMIT)",
            "安全校验失败，请刷新页面重试 (CSRF_INVALID)",
            "UNKNOWN",
            "constructor",
        ],
        "statuses": ["已创建", "运行中", "等待人工批准", "运行成功"],
        "publicSuccess": "演示成功",
        "events": [
            "模型请求",
            "模型动作",
            "策略判断",
            "工具结果",
            "已请求审批",
            "审批已通过",
            "审批已过期",
            "审批已拒绝",
            "审批已取消",
            "演示步骤",
            "FUTURE_EVENT",
        ],
        "syncedStatus": {
            "label": "运行成功",
            "machineCode": "机器码 · SUCCESS",
            "dataStatus": "SUCCESS",
        },
        "deduplicatedSequences": ["1", "2"],
        "syncedSummaries": ["新增事件"],
        "fallbackSummaries": [
            "模型正在请求下一步受治理的动作。",
            "模型提出了一个结构化动作，等待策略检查。",
            "安全策略已完成对动作的判定。",
            "工具执行结果已返回并进入审计记录。",
            "高风险动作已暂停，等待人工审批。",
            "人工审批已通过，冻结动作可以继续。",
            "审批请求已过期，动作不会执行。",
            "人工审批已拒绝，动作不会执行。",
            "审批请求已取消，动作不会执行。",
            "演示已记录一个确定性步骤。",
        ],
        "populatedTimeline": {"emptyCount": 0, "sequences": ["3"]},
        "emptyTimeline": {"emptyCount": 1, "eventCount": 0},
        "cancellation": {
            "error": None,
            "label": "取消运行",
            "disabled": False,
            "notice": "取消运行失败：当前状态不能执行这个操作 (INVALID_STATE)",
        },
    }


def test_run_page_explains_status_and_keeps_escaped_technical_details() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/runs/run-1")

    assert "等待人工批准" in response.text
    assert "策略判断" in response.text
    assert "查看技术细节" in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    parser = _parse_page(response.text)
    detail_payloads = [
        json.loads(_node_text(node))
        for node in _find_nodes(parser.roots, "pre")
    ]
    assert detail_payloads[2] == {"output": "<script>alert(1)</script>"}


def test_pending_run_explains_risk_without_exposing_capability() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/runs/run-1")

    assert response.status_code == 200
    assert "CMD_GIT_WRITE" in response.text
    assert "仅批准这一次" in response.text
    assert "拒绝" in response.text
    assert "capability-secret" not in response.text


def test_run_page_has_typed_timeline_and_keyboard_buttons() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/runs/run-1")

    assert 'data-event-type="MODEL_REQUEST"' in response.text
    assert 'data-event-type="POLICY_DECISION"' in response.text
    assert 'data-event-type="TOOL_RESULT"' in response.text
    assert '<button type="button"' in response.text
    assert 'id="run-status" aria-live="polite"' in response.text


def test_run_page_events_include_sequence_summary_and_technical_details() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/runs/run-1")
    parser = _parse_page(response.text)
    event_items = [
        node
        for node in _find_nodes(parser.roots, "li")
        if "event" in str(node["attrs"].get("class", "")).split()  # type: ignore[union-attr]
    ]

    assert [item["attrs"]["data-sequence"] for item in event_items] == [  # type: ignore[index]
        "1",
        "2",
        "3",
    ]
    assert [item["attrs"]["data-event-type"] for item in event_items] == [  # type: ignore[index]
        "MODEL_REQUEST",
        "POLICY_DECISION",
        "TOOL_RESULT",
    ]
    assert "检查项目" in _node_text(event_items[0])
    for item in event_items:
        details = _find_nodes(item["children"], "details")  # type: ignore[arg-type]
        assert len(details) == 1
        summaries = _find_nodes(details[0]["children"], "summary")  # type: ignore[arg-type]
        assert [_node_text(summary) for summary in summaries] == ["查看技术细节"]
        assert len(_find_nodes(details[0]["children"], "pre")) == 1  # type: ignore[arg-type]


def test_run_page_exposes_chinese_status_and_machine_code_targets() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/runs/run-1")
    parser = _parse_page(response.text)
    status = next(
        node
        for node in _find_nodes(parser.roots, "strong")
        if node["attrs"].get("id") == "run-status"  # type: ignore[union-attr]
    )
    machine_code = next(
        node
        for node in _find_nodes(parser.roots, "small")
        if node["attrs"].get("id") == "run-status-code"  # type: ignore[union-attr]
    )

    assert _node_text(status) == "等待人工批准"
    assert status["attrs"]["data-status"] == "AWAITING_APPROVAL"  # type: ignore[index]
    assert _node_text(machine_code) == "机器码 · AWAITING_APPROVAL"


def test_run_page_uses_mode_specific_workspace_and_success_labels() -> None:
    local = TestClient(
        create_app(AppDependencies(service=SuccessPageService()))
    ).get("/runs/run-1")
    public = TestClient(
        create_app(
            AppDependencies(
                service=SuccessPageService(),
                public_demo=True,
                embedded_project="C:/private-embedded-fixture",
            )
        )
    ).get("/runs/run-1")

    assert "C:/project" in local.text
    assert "运行成功" in local.text
    assert "隔离内置工作区" in public.text
    assert "不访问真实项目" in public.text
    assert "C:/project" not in public.text
    assert "演示成功" in public.text


def test_run_page_localizes_all_real_audit_events_and_preserves_unknown_code() -> None:
    client = TestClient(
        create_app(AppDependencies(service=EventLabelPageService()))
    )

    response = client.get("/runs/run-1")
    event_items = [
        node
        for node in _find_nodes(_parse_page(response.text).roots, "li")
        if "event" in str(node["attrs"].get("class", "")).split()  # type: ignore[union-attr]
    ]
    labels = [
        _node_text(_find_nodes(item["children"], "strong")[0])  # type: ignore[arg-type]
        for item in event_items
    ]
    summaries = [
        _node_text(nodes[0]) if (nodes := _find_nodes(item["children"], "p")) else None  # type: ignore[arg-type]
        for item in event_items
    ]
    details = [
        json.loads(_node_text(_find_nodes(item["children"], "pre")[0]))  # type: ignore[arg-type]
        for item in event_items
    ]

    assert labels == [
        "模型请求",
        "模型动作",
        "策略判断",
        "工具结果",
        "已请求审批",
        "审批已通过",
        "审批已过期",
        "审批已拒绝",
        "审批已取消",
        "演示步骤",
        "FUTURE_EVENT",
    ]
    assert summaries == [
        "模型正在请求下一步受治理的动作。",
        "模型提出了一个结构化动作，等待策略检查。",
        "安全策略已完成对动作的判定。",
        "工具执行结果已返回并进入审计记录。",
        "高风险动作已暂停，等待人工审批。",
        "人工审批已通过，冻结动作可以继续。",
        "审批请求已过期，动作不会执行。",
        "人工审批已拒绝，动作不会执行。",
        "审批请求已取消，动作不会执行。",
        "演示已记录一个确定性步骤。",
        None,
    ]
    assert details == [
        {"code": event_type}
        for event_type in (
            "MODEL_REQUEST",
            "ACTION",
            "POLICY_DECISION",
            "TOOL_RESULT",
            "APPROVAL_REQUESTED",
            "APPROVAL_APPROVED",
            "APPROVAL_EXPIRED",
            "APPROVAL_REJECTED",
            "APPROVAL_CANCELLED",
            "DEMO_EVENT",
            "FUTURE_EVENT",
        )
    ]


def test_settings_page_never_renders_plaintext_credentials() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/settings?project_id=project")

    assert response.status_code == 200
    assert "已配置" in response.text
    assert "凭据来源" in response.text
    assert "模型服务商" in response.text
    assert "keyring" in response.text
    assert "sk-" not in response.text


def test_public_settings_explicitly_uses_deterministic_mock_without_credentials() -> None:
    client = TestClient(
        create_app(AppDependencies(service=PageService(), public_demo=True))
    )

    response = client.get("/settings?project_id=project")

    assert "确定性 Mock（无需凭据）" in response.text
    assert "openai-compatible" not in response.text
    assert "系统密钥环" not in response.text
    assert "密钥由本地命令行工具管理" not in response.text


def test_local_settings_still_reports_real_credential_status() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/settings?project_id=project")

    assert "已配置" in response.text
    assert "系统密钥环（keyring）" in response.text
    assert "openai-compatible" in response.text


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("keyring", "系统密钥环（keyring）"),
        ("secret-file", "本地密钥文件（secret-file）"),
        ("env-file", "环境变量文件（env-file）"),
    ],
)
def test_settings_page_localizes_known_credential_sources(
    source: str, label: str
) -> None:
    client = TestClient(
        create_app(AppDependencies(service=PageService(source)))
    )

    response = client.get("/settings?project_id=project")

    assert response.status_code == 200
    assert label in _node_text(_parse_page(response.text).roots[0])


def test_settings_page_preserves_unknown_credential_source() -> None:
    client = TestClient(
        create_app(AppDependencies(service=PageService("hardware-vault")))
    )

    response = client.get("/settings?project_id=project")

    assert "hardware-vault" in _node_text(_parse_page(response.text).roots[0])
