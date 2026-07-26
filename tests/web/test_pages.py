from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from safefix.web.app import AppDependencies, create_app
from tests.web.test_api import FakeService


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


def test_client_script_localizes_errors_statuses_and_event_types() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser mapping test")
    client = TestClient(create_app(AppDependencies(service=PageService())))
    script = client.get("/static/app.js").text
    harness = """
const fs = require("node:fs");
const vm = require("node:vm");
const context = {
  document: { querySelector: () => null, querySelectorAll: () => [] },
  window: { setTimeout: () => {} },
  fetch: async () => { throw new Error("not used"); },
  FormData: class {}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(0, "utf8"), context);
process.stdout.write(JSON.stringify({
  errors: [
    "INVALID_STATE",
    "RUN_NOT_FOUND",
    "PUBLIC_INPUT_FORBIDDEN",
    "RATE_LIMITED",
    "ACTIVE_RUN_LIMIT",
    "CSRF_INVALID",
    "UNKNOWN"
  ].map((code) => context.explainError(code)),
  statuses: ["CREATED", "RUNNING", "AWAITING_APPROVAL", "SUCCESS"]
    .map((code) => context.statusLabel(code)),
  events: ["MODEL_REQUEST", "POLICY_DECISION", "TOOL_RESULT", "DEMO_EVENT"]
    .map((code) => context.eventLabel(code))
}));
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
        ],
        "statuses": ["已创建", "运行中", "等待人工批准", "演示成功"],
        "events": ["模型请求", "策略判断", "工具结果", "演示步骤"],
    }


def test_run_page_explains_status_and_keeps_escaped_technical_details() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/runs/run-1")

    assert "等待人工批准" in response.text
    assert "策略判断" in response.text
    assert "查看技术细节" in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text


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


def test_settings_page_never_renders_plaintext_credentials() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/settings?project_id=project")

    assert response.status_code == 200
    assert "已配置" in response.text
    assert "凭据来源" in response.text
    assert "模型服务商" in response.text
    assert "keyring" in response.text
    assert "sk-" not in response.text


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
