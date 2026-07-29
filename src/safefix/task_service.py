from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from safefix.domain import ApprovalRequest, RunSnapshot, RunStatus, Task, TaskMode
from safefix.run_store import RunNotFound


class TaskServiceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalAccess:
    request: ApprovalRequest
    capability: str = field(repr=False)
    csrf_token: str = field(repr=False)


class TaskService:
    """Application boundary shared by HTTP and command-line adapters."""

    def __init__(
        self,
        loop_factory: Callable[[str, str], Any],
        run_store: Any,
        *,
        audit_store: Any | None = None,
        approval_store: Any | None = None,
        memory_store: Any | None = None,
        credential_service: Any | None = None,
    ) -> None:
        self._loop_factory = loop_factory
        self._runs = run_store
        self._audit = audit_store
        self._approvals = approval_store
        self._memory = memory_store
        self._credentials = credential_service
        self._loops: dict[str, Any] = {}
        self._access: dict[str, ApprovalAccess] = {}
        self._remembered_run_ids: set[str] = set()

    async def create(
        self,
        *,
        task: str,
        project_path: str,
        provider: str,
        project_id: str | None = None,
        mode: TaskMode = TaskMode.LOCAL,
    ) -> RunSnapshot:
        if provider != "mock" and self._credentials is not None:
            self._credentials.get_for_request(provider)
        loop = self._loop_factory(project_path, provider)
        domain_task = Task(
            id=str(uuid4()),
            project_id=project_id or project_path,
            workspace_root=project_path,
            description=task,
            mode=mode,
            created_at=datetime.now(UTC),
        )
        snapshot = await loop.start(domain_task)
        self._loops[snapshot.run_id] = loop
        self._refresh_approval_access(snapshot, loop)
        self._remember_terminal(snapshot)
        return snapshot

    def get(self, run_id: str) -> RunSnapshot:
        snapshot = self._runs.get(run_id)
        if snapshot is None:
            raise RunNotFound("run does not exist")
        return snapshot

    def list_events(self, run_id: str) -> list[Any]:
        self.get(run_id)
        return [] if self._audit is None else self._audit.list_events(run_id)

    def get_approval(self, run_id: str) -> ApprovalAccess:
        self.get(run_id)
        try:
            return self._access[run_id]
        except KeyError as exc:
            raise TaskServiceError("run has no pending approval") from exc

    async def approve(self, run_id: str, token: str) -> RunSnapshot:
        loop = self._required_loop(run_id)
        approval_id = self.get(run_id).pending_approval_id
        if approval_id is None:
            raise TaskServiceError("run has no pending approval")
        snapshot = await loop.resume_approved(approval_id, token)
        self._refresh_approval_access(snapshot, loop)
        self._remember_terminal(snapshot)
        return snapshot

    async def reject(self, run_id: str, token: str) -> RunSnapshot:
        loop = self._required_loop(run_id)
        approval_id = self.get(run_id).pending_approval_id
        if approval_id is None:
            raise TaskServiceError("run has no pending approval")
        snapshot = await loop.resume_rejected(approval_id, token)
        self._refresh_approval_access(snapshot, loop)
        self._remember_terminal(snapshot)
        return snapshot

    async def cancel(self, run_id: str) -> RunSnapshot:
        snapshot = await self._required_loop(run_id).cancel(run_id)
        self._access.pop(run_id, None)
        return snapshot

    def list_memory(self, project_id: str) -> list[Any]:
        return [] if self._memory is None else self._memory.list(project_id)

    def clear_memory(self, project_id: str) -> int:
        return 0 if self._memory is None else self._memory.delete_project(project_id)

    def credential_status(self, provider: str) -> Any:
        if self._credentials is None:
            raise TaskServiceError("credential service is unavailable")
        return self._credentials.status(provider)

    def _required_loop(self, run_id: str) -> Any:
        self.get(run_id)
        try:
            return self._loops[run_id]
        except KeyError as exc:
            raise TaskServiceError("run is not active in this process") from exc

    def _refresh_approval_access(self, snapshot: RunSnapshot, loop: Any) -> None:
        self._access.pop(snapshot.run_id, None)
        approval_id = snapshot.pending_approval_id
        if (
            snapshot.status is not RunStatus.AWAITING_APPROVAL
            or approval_id is None
            or self._approvals is None
        ):
            return
        capability = loop.take_approval_capability(approval_id)
        if capability is None:
            return
        self._access[snapshot.run_id] = ApprovalAccess(
            self._approvals.get(approval_id),
            capability,
            secrets.token_urlsafe(24),
        )

    def _remember_terminal(self, snapshot: RunSnapshot) -> None:
        if (
            self._memory is None
            or snapshot.status is not RunStatus.SUCCESS
            or snapshot.run_id in self._remembered_run_ids
        ):
            return
        content = (
            f"Task: {snapshot.description}\n"
            f"Result: {snapshot.status.value}\n"
            f"Changed files: {', '.join(snapshot.changed_files) or 'none'}"
        )
        storage_failed = False
        try:
            self._memory.add(
                snapshot.project_id,
                "repair_summary",
                content,
                snapshot.changed_files,
            )
        except Exception:
            storage_failed = True
        if storage_failed:
            raise TaskServiceError("memory storage is unavailable")
        self._remembered_run_ids.add(snapshot.run_id)
