from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime

from safefix.domain import RunSnapshot, RunStatus


class RunNotFound(LookupError):
    pass


class VersionConflict(RuntimeError):
    pass


class InvalidTransition(ValueError):
    pass


_ALLOWED_TRANSITIONS = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.AWAITING_APPROVAL,
            RunStatus.SUCCESS,
            RunStatus.BLOCKED,
            RunStatus.NO_PROGRESS,
            RunStatus.BUDGET_EXCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
}


class RunStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = threading.RLock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_snapshots (
                run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL
            )
            """
        )

    def create(self, snapshot: RunSnapshot) -> RunSnapshot:
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO run_snapshots (
                        run_id, project_id, status, version, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.run_id,
                        snapshot.project_id,
                        snapshot.status.value,
                        snapshot.version,
                        snapshot.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("run already exists") from exc
        return snapshot

    def get(self, run_id: str) -> RunSnapshot | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT snapshot_json FROM run_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return RunSnapshot.model_validate_json(row[0])

    def transition(
        self,
        run_id: str,
        new_status: RunStatus,
        *,
        expected_version: int,
    ) -> RunSnapshot:
        current = self._required(run_id)
        if current.version != expected_version:
            raise VersionConflict("run version does not match")
        target = RunStatus(new_status)
        if target not in _ALLOWED_TRANSITIONS.get(current.status, frozenset()):
            raise InvalidTransition(
                f"transition from {current.status.value} to {target.value} is not allowed"
            )
        candidate = current.model_copy(
            update={"status": target, "updated_at": datetime.now(UTC)}
        )
        return self.save_snapshot(candidate, expected_version=expected_version)

    def save_snapshot(
        self, snapshot: RunSnapshot, *, expected_version: int
    ) -> RunSnapshot:
        current = self._required(snapshot.run_id)
        if current.version != expected_version:
            raise VersionConflict("run version does not match")
        if snapshot.project_id != current.project_id:
            raise ValueError("run project cannot change")
        if snapshot.status != current.status and snapshot.status not in _ALLOWED_TRANSITIONS.get(
            current.status, frozenset()
        ):
            raise InvalidTransition("snapshot contains an invalid status transition")
        saved = snapshot.model_copy(update={"version": expected_version + 1})
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE run_snapshots
                SET project_id = ?, status = ?, version = ?, snapshot_json = ?
                WHERE run_id = ? AND version = ?
                """,
                (
                    saved.project_id,
                    saved.status.value,
                    saved.version,
                    saved.model_dump_json(),
                    saved.run_id,
                    expected_version,
                ),
            )
        if cursor.rowcount != 1:
            raise VersionConflict("run version changed concurrently")
        return saved

    def delete_project(self, project_id: str) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM run_snapshots WHERE project_id = ?", (project_id,)
            )
        return cursor.rowcount

    def _required(self, run_id: str) -> RunSnapshot:
        snapshot = self.get(run_id)
        if snapshot is None:
            raise RunNotFound("run does not exist")
        return snapshot
