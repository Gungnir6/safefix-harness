from __future__ import annotations

import hmac
import json
import time
from collections import deque
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from safefix.domain import RunStatus, TaskMode


_TERMINAL = {
    RunStatus.SUCCESS,
    RunStatus.BLOCKED,
    RunStatus.NO_PROGRESS,
    RunStatus.BUDGET_EXCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}

_STATUS_LABELS = {
    RunStatus.CREATED: "已创建",
    RunStatus.RUNNING: "运行中",
    RunStatus.AWAITING_APPROVAL: "等待人工批准",
    RunStatus.SUCCESS: "运行成功",
    RunStatus.BLOCKED: "已被安全策略拦截",
    RunStatus.NO_PROGRESS: "没有取得进展",
    RunStatus.BUDGET_EXCEEDED: "已达到执行预算",
    RunStatus.FAILED: "运行失败",
    RunStatus.CANCELLED: "已取消",
}

_EVENT_LABELS = {
    "MODEL_REQUEST": "模型请求",
    "ACTION": "模型动作",
    "POLICY_DECISION": "策略判断",
    "TOOL_RESULT": "工具结果",
    "APPROVAL_REQUESTED": "已请求审批",
    "APPROVAL_APPROVED": "审批已通过",
    "APPROVAL_EXPIRED": "审批已过期",
    "APPROVAL_REJECTED": "审批已拒绝",
    "APPROVAL_CANCELLED": "审批已取消",
    "DEMO_EVENT": "演示步骤",
}

_EVENT_SUMMARIES = {
    "MODEL_REQUEST": "模型正在请求下一步受治理的动作。",
    "ACTION": "模型提出了一个结构化动作，等待策略检查。",
    "POLICY_DECISION": "安全策略已完成对动作的判定。",
    "TOOL_RESULT": "工具执行结果已返回并进入审计记录。",
    "APPROVAL_REQUESTED": "高风险动作已暂停，等待人工审批。",
    "APPROVAL_APPROVED": "人工审批已通过，冻结动作可以继续。",
    "APPROVAL_EXPIRED": "审批请求已过期，动作不会执行。",
    "APPROVAL_REJECTED": "人工审批已拒绝，动作不会执行。",
    "APPROVAL_CANCELLED": "审批请求已取消，动作不会执行。",
    "DEMO_EVENT": "演示已记录一个确定性步骤。",
}


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1)
    project_path: str | None = None
    provider: str | None = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str = Field(min_length=1)


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(cast(Any, value))
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value


def _run_response(service: Any, snapshot: Any, public_demo: bool) -> dict[str, Any]:
    body = dict(_json(snapshot))
    if public_demo and hasattr(service, "get_presentation"):
        body["presentation"] = service.get_presentation(snapshot.run_id)
    return body


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, {"code": "RUN_NOT_FOUND"})
    return HTTPException(409, {"code": "INVALID_STATE"})


def create_router(dependencies: Any) -> APIRouter:
    router = APIRouter()
    service = dependencies.service
    templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
    requests: deque[float] = deque()
    active_runs: set[str] = set()

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"public_demo": dependencies.public_demo},
        )

    @router.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str) -> Response:
        try:
            snapshot = service.get(run_id)
            events = service.list_events(run_id)
        except Exception as exc:
            raise _error(exc) from None
        access = None
        if snapshot.status is RunStatus.AWAITING_APPROVAL:
            try:
                access = service.get_approval(run_id)
            except Exception:
                access = None
        response = templates.TemplateResponse(
            request=request,
            name="run.html",
            context={
                "run": snapshot,
                "events": events,
                "presentation": (
                    service.get_presentation(run_id)
                    if dependencies.public_demo and hasattr(service, "get_presentation")
                    else None
                ),
                "approval": access,
                "terminal": snapshot.status in _TERMINAL,
                "status_label": (
                    "机制验证通过"
                    if dependencies.public_demo
                    and snapshot.status is RunStatus.SUCCESS
                    else _STATUS_LABELS.get(snapshot.status, snapshot.status.value)
                ),
                "event_labels": _EVENT_LABELS,
                "event_summaries": _EVENT_SUMMARIES,
                "event_json": lambda payload: json.dumps(
                    payload, ensure_ascii=False, sort_keys=True
                ),
                "public_demo": dependencies.public_demo,
            },
        )
        if access is not None:
            response.headers.append(
                "set-cookie",
                "safefix_approval="
                f"{access.capability}; HttpOnly; "
                f"Path=/api/runs/{run_id}/approval; SameSite=Strict",
            )
        return response

    @router.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, project_id: str = "default") -> Response:
        credential = None
        if not dependencies.public_demo:
            try:
                credential = service.credential_status("openai-compatible")
            except Exception:
                credential = None
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "project_id": project_id,
                "credential": credential,
                "memory": service.list_memory(project_id),
                "public_demo": dependencies.public_demo,
            },
        )

    @router.post("/api/runs", status_code=202)
    async def create_run(request: CreateRunRequest) -> Any:
        project_path = request.project_path
        provider = request.provider
        mode = TaskMode.LOCAL
        if dependencies.public_demo:
            invalid = [
                name
                for name, value in (
                    ("project_path", project_path),
                    ("provider", provider),
                )
                if value is not None
            ]
            if invalid:
                raise HTTPException(
                    422,
                    {"code": "PUBLIC_INPUT_FORBIDDEN", "fields": invalid},
                )
            now = time.monotonic()
            while requests and requests[0] <= now - 60:
                requests.popleft()
            if len(requests) >= dependencies.public_rate_limit:
                raise HTTPException(429, {"code": "RATE_LIMITED"})
            if len(active_runs) >= dependencies.public_active_run_limit:
                raise HTTPException(429, {"code": "ACTIVE_RUN_LIMIT"})
            requests.append(now)
            project_path = dependencies.embedded_project
            provider = "mock"
            mode = TaskMode.PUBLIC_DEMO
        elif project_path is None:
            raise HTTPException(422, {"code": "PROJECT_PATH_REQUIRED"})
        try:
            snapshot = await service.create(
                task=request.task,
                project_path=project_path,
                provider=provider or "openai-compatible",
                mode=mode,
            )
        except Exception as exc:
            raise _error(exc) from None
        if dependencies.public_demo and snapshot.status not in _TERMINAL:
            active_runs.add(snapshot.run_id)
        return _run_response(service, snapshot, dependencies.public_demo)

    @router.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> Any:
        try:
            snapshot = service.get(run_id)
        except Exception as exc:
            raise _error(exc) from None
        if getattr(snapshot, "status", None) in _TERMINAL:
            active_runs.discard(run_id)
        return _run_response(service, snapshot, dependencies.public_demo)

    @router.get("/api/runs/{run_id}/events")
    def list_events(run_id: str) -> list[dict[str, Any]]:
        try:
            events = service.list_events(run_id)
        except Exception as exc:
            raise _error(exc) from None
        return [
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "payload": event.redacted_payload,
                "created_at": event.created_at,
            }
            for event in events
        ]

    @router.get("/api/runs/{run_id}/approval")
    def get_approval(run_id: str, response: Response) -> dict[str, Any]:
        try:
            access = service.get_approval(run_id)
        except Exception as exc:
            raise _error(exc) from None
        response.headers.append(
            "set-cookie",
            "safefix_approval="
            f"{access.capability}; HttpOnly; "
            f"Path=/api/runs/{run_id}/approval; SameSite=Strict",
        )
        return {
            "id": access.request.id,
            "run_id": access.request.run_id,
            "status": access.request.status.value,
            "expires_at": access.request.expires_at,
            "csrf_token": access.csrf_token,
        }

    async def decide(
        run_id: str,
        decision: ApprovalDecision,
        capability: str | None,
        operation: str,
    ) -> Any:
        try:
            access = service.get_approval(run_id)
        except Exception as exc:
            raise _error(exc) from None
        if not capability or not hmac.compare_digest(
            decision.csrf_token, access.csrf_token
        ):
            raise HTTPException(403, {"code": "CSRF_INVALID"})
        try:
            method = service.approve if operation == "approve" else service.reject
            return _json(await method(run_id, capability))
        except Exception as exc:
            raise _error(exc) from None

    @router.post("/api/runs/{run_id}/approval/approve")
    async def approve(
        run_id: str,
        decision: ApprovalDecision,
        safefix_approval: str | None = Cookie(None),
    ) -> Any:
        return await decide(run_id, decision, safefix_approval, "approve")

    @router.post("/api/runs/{run_id}/approval/reject")
    async def reject(
        run_id: str,
        decision: ApprovalDecision,
        safefix_approval: str | None = Cookie(None),
    ) -> Any:
        return await decide(run_id, decision, safefix_approval, "reject")

    @router.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> Any:
        try:
            snapshot = await service.cancel(run_id)
        except Exception as exc:
            raise _error(exc) from None
        active_runs.discard(run_id)
        return _json(snapshot)

    @router.get("/api/projects/{project_id}/memory")
    def list_memory(project_id: str) -> list[Any]:
        return [_json(item) for item in service.list_memory(project_id)]

    @router.delete("/api/projects/{project_id}/memory")
    def clear_memory(project_id: str) -> dict[str, int]:
        return {"deleted": service.clear_memory(project_id)}

    @router.get("/api/credentials/{provider}")
    def credential_status(provider: str) -> Any:
        return _json(service.credential_status(provider))

    return router
