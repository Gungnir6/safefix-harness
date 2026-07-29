from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from safefix.domain import BudgetState, RunSnapshot, RunStatus
from safefix.task_service import TaskService, TaskServiceError


def _snapshot(
    status: RunStatus,
    *,
    run_id: str = "run-1",
    pending_approval_id: str | None = None,
    changed_files: tuple[str, ...] = (),
) -> RunSnapshot:
    now = datetime.now(UTC)
    return RunSnapshot(
        run_id=run_id,
        task_id="task-1",
        project_id="C:/source/project",
        workspace_root="C:/SafeFix/runs/execution/workspace",
        description="fix addition",
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
        pending_approval_id=pending_approval_id,
        changed_files=changed_files,
        created_at=now,
        updated_at=now,
    )


class RecordingMemory:
    def __init__(self) -> None:
        self.added: list[tuple[str, str, str, tuple[str, ...]]] = []

    def add(
        self,
        project_id: str,
        record_type: str,
        content: str,
        keywords: tuple[str, ...],
    ) -> None:
        self.added.append((project_id, record_type, content, keywords))


class RecordingRuns:
    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot

    def get(self, run_id: str) -> RunSnapshot | None:
        return self.snapshot if run_id == self.snapshot.run_id else None


class SuccessfulLoop:
    def __init__(self, *, changed_files: tuple[str, ...] = ()) -> None:
        self.task = None
        self.snapshot = _snapshot(
            RunStatus.SUCCESS,
            changed_files=changed_files,
        )

    async def start(self, task: object) -> RunSnapshot:
        self.task = task
        return self.snapshot


@pytest.mark.asyncio
async def test_create_accepts_snapshot_without_status_when_no_approval() -> None:
    snapshot = SimpleNamespace(run_id="run-1", pending_approval_id=None)

    class LegacyLoop:
        async def start(self, task: object) -> object:
            del task
            return snapshot

    service = TaskService(
        lambda project_path, provider: LegacyLoop(),
        RecordingRuns(_snapshot(RunStatus.SUCCESS)),
    )

    created = await service.create(
        task="legacy injected run",
        project_path="C:/workspace",
        provider="mock",
    )

    assert created is snapshot


@pytest.mark.asyncio
async def test_pending_snapshot_without_status_preserves_existing_access() -> None:
    snapshot = SimpleNamespace(
        run_id="run-1",
        pending_approval_id="approval-1",
    )

    class LegacyLoop:
        async def start(self, task: object) -> object:
            del task
            return snapshot

    service = TaskService(
        lambda project_path, provider: LegacyLoop(),
        RecordingRuns(_snapshot(RunStatus.SUCCESS)),
    )
    existing_access = object()
    service._access["run-1"] = existing_access  # type: ignore[assignment]

    with pytest.raises(AttributeError):
        await service.create(
            task="invalid injected run",
            project_path="C:/workspace",
            provider="mock",
        )

    assert service._access["run-1"] is existing_access


@pytest.mark.asyncio
async def test_task_service_separates_project_identity_and_workspace() -> None:
    loop = SuccessfulLoop(changed_files=("calculator.py",))
    memory = RecordingMemory()
    service = TaskService(
        lambda project_path, provider: loop,
        RecordingRuns(loop.snapshot),
        memory_store=memory,
    )

    snapshot = await service.create(
        task="fix addition",
        project_path="C:/SafeFix/runs/execution/workspace",
        project_id="C:/source/project",
        provider="mock",
    )

    assert loop.task.workspace_root == "C:/SafeFix/runs/execution/workspace"
    assert loop.task.project_id == "C:/source/project"
    assert snapshot.status is RunStatus.SUCCESS
    assert memory.added == [
        (
            "C:/source/project",
            "repair_summary",
            "Task: fix addition\nResult: SUCCESS\nChanged files: calculator.py",
            ("calculator.py",),
        )
    ]


@pytest.mark.asyncio
async def test_awaiting_approval_does_not_write_memory() -> None:
    waiting = _snapshot(
        RunStatus.AWAITING_APPROVAL,
        pending_approval_id="approval-1",
    )

    class WaitingLoop:
        async def start(self, task: object) -> RunSnapshot:
            del task
            return waiting

    memory = RecordingMemory()
    service = TaskService(
        lambda project_path, provider: WaitingLoop(),
        RecordingRuns(waiting),
        memory_store=memory,
    )

    snapshot = await service.create(
        task="fix addition",
        project_path=waiting.workspace_root,
        project_id=waiting.project_id,
        provider="mock",
    )

    assert snapshot.status is RunStatus.AWAITING_APPROVAL
    assert memory.added == []


@pytest.mark.asyncio
async def test_successful_approve_writes_terminal_memory_once() -> None:
    waiting = _snapshot(
        RunStatus.AWAITING_APPROVAL,
        pending_approval_id="approval-1",
    )
    successful = _snapshot(
        RunStatus.SUCCESS,
        changed_files=("calculator.py",),
    )

    class ApprovalLoop:
        async def start(self, task: object) -> RunSnapshot:
            del task
            return waiting

        async def resume_approved(
            self, approval_id: str, token: str
        ) -> RunSnapshot:
            assert (approval_id, token) == ("approval-1", "token")
            return successful

    memory = RecordingMemory()
    loop = ApprovalLoop()
    service = TaskService(
        lambda project_path, provider: loop,
        RecordingRuns(waiting),
        memory_store=memory,
    )
    await service.create(
        task="fix addition",
        project_path=waiting.workspace_root,
        project_id=waiting.project_id,
        provider="mock",
    )

    first = await service.approve(waiting.run_id, "token")
    second = await service.approve(waiting.run_id, "token")

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert len(memory.added) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", [RunStatus.FAILED, RunStatus.CANCELLED])
async def test_unsuccessful_terminal_runs_do_not_write_memory(
    terminal_status: RunStatus,
) -> None:
    terminal = _snapshot(terminal_status)

    class TerminalLoop:
        async def start(self, task: object) -> RunSnapshot:
            del task
            return terminal

    memory = RecordingMemory()
    service = TaskService(
        lambda project_path, provider: TerminalLoop(),
        RecordingRuns(terminal),
        memory_store=memory,
    )

    await service.create(
        task="fix addition",
        project_path=terminal.workspace_root,
        project_id=terminal.project_id,
        provider="mock",
    )

    assert memory.added == []


@pytest.mark.asyncio
async def test_terminal_memory_excludes_raw_execution_and_model_text() -> None:
    secret_stdout = "RAW STDOUT SECRET"
    secret_stderr = "RAW STDERR SECRET"
    secret_model = "RAW MODEL SECRET"
    loop = SuccessfulLoop(changed_files=("calculator.py",))
    snapshot = loop.snapshot.model_copy(
        update={
            "latest_tool_result": {
                "action_id": "validation",
                "success": True,
                "stdout_summary": secret_stdout,
                "stderr_summary": secret_stderr,
            },
            "stop_reason": secret_model,
        }
    )
    loop.snapshot = snapshot
    memory = RecordingMemory()
    service = TaskService(
        lambda project_path, provider: loop,
        RecordingRuns(snapshot),
        memory_store=memory,
    )

    await service.create(
        task="fix addition",
        project_path=snapshot.workspace_root,
        project_id=snapshot.project_id,
        provider="mock",
    )

    rendered = repr(memory.added)
    assert secret_stdout not in rendered
    assert secret_stderr not in rendered
    assert secret_model not in rendered


@pytest.mark.asyncio
async def test_memory_failure_has_stable_service_error() -> None:
    secret = "storage-driver-secret"
    loop = SuccessfulLoop()

    class FailingMemory:
        def add(self, *args: object) -> None:
            del args
            raise RuntimeError(secret)

    service = TaskService(
        lambda project_path, provider: loop,
        RecordingRuns(loop.snapshot),
        memory_store=FailingMemory(),
    )

    with pytest.raises(
        TaskServiceError, match="^memory storage is unavailable$"
    ) as captured:
        await service.create(
            task="fix addition",
            project_path=loop.snapshot.workspace_root,
            project_id=loop.snapshot.project_id,
            provider="mock",
        )

    assert secret not in repr(captured.value)
