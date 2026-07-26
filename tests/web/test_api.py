from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from safefix.demo import PublicDemoService
from safefix.domain import (
    ApprovalRequest,
    ApprovalStatus,
    BudgetState,
    RunSnapshot,
    RunStatus,
)
from safefix.task_service import ApprovalAccess
from safefix.web.app import AppDependencies, create_app


def _snapshot(status: RunStatus = RunStatus.AWAITING_APPROVAL) -> RunSnapshot:
    now = datetime.now(UTC)
    return RunSnapshot(
        run_id="run-1",
        task_id="task-1",
        project_id="project",
        workspace_root="C:/project",
        description="fix value",
        status=status,
        repair_round=0,
        step_count=1,
        budget=BudgetState(
            max_steps=3,
            remaining_steps=2,
            max_repair_rounds=2,
            remaining_repairs=2,
        ),
        version=1,
        pending_approval_id="approval-1"
        if status is RunStatus.AWAITING_APPROVAL
        else None,
        created_at=now,
        updated_at=now,
    )


class FakeService:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.decisions: list[tuple[str, str, str]] = []

    async def create(self, **values: object) -> RunSnapshot:
        self.created.append(values)
        return _snapshot(RunStatus.RUNNING)

    def get(self, run_id: str) -> RunSnapshot:
        if run_id == "missing":
            raise LookupError("run does not exist")
        return _snapshot()

    def list_events(self, run_id: str) -> list[object]:
        return [
            SimpleNamespace(
                sequence=1,
                event_type="POLICY_DECISION",
                redacted_payload={"token": "[REDACTED]"},
                created_at=datetime.now(UTC),
            )
        ]

    def get_approval(self, run_id: str) -> ApprovalAccess:
        now = datetime.now(UTC)
        request = ApprovalRequest(
            id="approval-1",
            run_id=run_id,
            action_hash="a" * 64,
            status=ApprovalStatus.PENDING,
            one_time_token_hash="b" * 64,
            frozen_action_json='{"type":"run_process"}',
            created_at=now,
            expires_at=now,
        )
        return ApprovalAccess(request, "capability-secret", "csrf-1")

    async def approve(self, run_id: str, token: str) -> RunSnapshot:
        self.decisions.append(("approve", run_id, token))
        return _snapshot(RunStatus.RUNNING)

    async def reject(self, run_id: str, token: str) -> RunSnapshot:
        self.decisions.append(("reject", run_id, token))
        return _snapshot(RunStatus.RUNNING)

    async def cancel(self, run_id: str) -> RunSnapshot:
        return _snapshot(RunStatus.CANCELLED)

    def list_memory(self, project_id: str) -> list[object]:
        return []

    def clear_memory(self, project_id: str) -> int:
        return 0

    def credential_status(self, provider: str) -> object:
        return SimpleNamespace(
            provider=provider, configured=True, source="keyring", warning=None
        )


def test_public_mode_rejects_project_path_and_real_provider() -> None:
    service = FakeService()
    client = TestClient(
        create_app(AppDependencies(service=service, public_demo=True))
    )

    response = client.post(
        "/api/runs",
        json={
            "task": "fix value",
            "project_path": "C:/private",
            "provider": "openai",
        },
    )

    assert response.status_code == 422
    assert "project_path" in response.text
    assert "provider" in response.text
    assert service.created == []


def test_public_mode_forces_embedded_project_and_mock_provider() -> None:
    service = FakeService()
    client = TestClient(
        create_app(
            AppDependencies(
                service=service,
                public_demo=True,
                embedded_project="C:/embedded",
            )
        )
    )

    response = client.post("/api/runs", json={"task": "fix value"})

    assert response.status_code == 202
    assert service.created[0]["project_path"] == "C:/embedded"
    assert service.created[0]["provider"] == "mock"


def test_public_demo_runs_inside_the_api_event_loop() -> None:
    client = TestClient(
        create_app(
            AppDependencies(service=PublicDemoService(), public_demo=True)
        )
    )

    response = client.post("/api/runs", json={"task": "feedback"})

    assert response.status_code == 202
    assert response.json()["status"] == "SUCCESS"


def test_approval_response_hides_capability_and_requires_csrf() -> None:
    service = FakeService()
    client = TestClient(create_app(AppDependencies(service=service)))

    response = client.get("/api/runs/run-1/approval")

    assert response.status_code == 200
    assert "capability-secret" not in response.text
    assert "token" not in response.json()
    assert response.json()["status"] == "PENDING"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie

    denied = client.post(
        "/api/runs/run-1/approval/approve", json={"csrf_token": "wrong"}
    )
    assert denied.status_code == 403

    accepted = client.post(
        "/api/runs/run-1/approval/approve", json={"csrf_token": "csrf-1"}
    )
    assert accepted.status_code == 200
    assert service.decisions == [("approve", "run-1", "capability-secret")]


def test_missing_run_has_stable_json_error_and_events_are_redacted() -> None:
    client = TestClient(create_app(AppDependencies(service=FakeService())))

    missing = client.get("/api/runs/missing")
    events = client.get("/api/runs/run-1/events")

    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "RUN_NOT_FOUND"}}
    assert events.json()[0]["payload"] == {"token": "[REDACTED]"}
