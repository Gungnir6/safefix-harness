from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from safefix.web.app import AppDependencies, create_app
from tests.web.test_api import FakeService


class PageService(FakeService):
    def list_events(self, run_id: str) -> list[object]:
        del run_id
        return [
            SimpleNamespace(
                sequence=1,
                event_type="MODEL_REQUEST",
                redacted_payload={"summary": "inspect project"},
                created_at=datetime.now(UTC),
            ),
            SimpleNamespace(
                sequence=2,
                event_type="POLICY_DECISION",
                redacted_payload={
                    "rule_ids": ["CMD_GIT_WRITE"],
                    "reason": "Git writes require one-time approval",
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


def test_settings_page_never_renders_plaintext_credentials() -> None:
    client = TestClient(create_app(AppDependencies(service=PageService())))

    response = client.get("/settings?project_id=project")

    assert response.status_code == 200
    assert "已配置" in response.text
    assert "凭据来源" in response.text
    assert "模型服务商" in response.text
    assert "keyring" in response.text
    assert "sk-" not in response.text
