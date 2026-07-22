from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from safefix.domain import BudgetState, RunSnapshot, RunStatus
from safefix.run_store import InvalidTransition, RunStore, VersionConflict


def _snapshot(
    *, status: RunStatus = RunStatus.CREATED, version: int = 0
) -> RunSnapshot:
    now = datetime.now(UTC)
    return RunSnapshot(
        run_id="run-1",
        task_id="task-1",
        project_id="project-1",
        workspace_root="C:/workspace",
        description="fix tests",
        status=status,
        repair_round=0,
        step_count=0,
        budget=BudgetState(
            max_steps=20,
            remaining_steps=20,
            max_repair_rounds=3,
            remaining_repairs=3,
        ),
        version=version,
        created_at=now,
        updated_at=now,
    )


def test_run_snapshot_survives_store_reopen(tmp_path: Path) -> None:
    database = tmp_path / "runs.db"
    connection = sqlite3.connect(database)
    RunStore(connection).create(_snapshot())
    connection.close()

    reopened = RunStore(sqlite3.connect(database))

    assert reopened.get("run-1") == _snapshot().model_copy(
        update={
            "created_at": reopened.get("run-1").created_at,  # type: ignore[union-attr]
            "updated_at": reopened.get("run-1").updated_at,  # type: ignore[union-attr]
        }
    )


def test_run_store_enforces_transitions_and_versions() -> None:
    store = RunStore(sqlite3.connect(":memory:"))
    store.create(_snapshot())

    running = store.transition("run-1", RunStatus.RUNNING, expected_version=0)

    assert running.status == RunStatus.RUNNING
    assert running.version == 1
    with pytest.raises(VersionConflict):
        store.transition("run-1", RunStatus.SUCCESS, expected_version=0)


def test_run_store_rejects_invalid_transition() -> None:
    store = RunStore(sqlite3.connect(":memory:"))
    store.create(_snapshot())

    with pytest.raises(InvalidTransition):
        store.transition("run-1", RunStatus.SUCCESS, expected_version=0)


def test_save_snapshot_uses_compare_and_set() -> None:
    store = RunStore(sqlite3.connect(":memory:"))
    created = store.create(_snapshot())
    changed = created.model_copy(update={"step_count": 1})

    saved = store.save_snapshot(changed, expected_version=0)

    assert saved.step_count == 1
    assert saved.version == 1
    with pytest.raises(VersionConflict):
        store.save_snapshot(changed, expected_version=0)


def test_run_store_deletes_project_runs() -> None:
    store = RunStore(sqlite3.connect(":memory:"))
    store.create(_snapshot())

    assert store.delete_project("project-1") == 1
    assert store.get("run-1") is None

