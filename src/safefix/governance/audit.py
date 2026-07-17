"""Redacted, hash-chained audit event storage."""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = ("token", "key", "secret", "password", "authorization")
_UNAVAILABLE_MESSAGE = "Audit storage is unavailable"
_CONNECTION_LOCKS = tuple(threading.RLock() for _ in range(64))
_SAVEPOINT_COUNTER = itertools.count()


class AuditUnavailable(RuntimeError):
    """Raised when an audit operation cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    run_id: str
    sequence: int
    event_type: str
    redacted_payload: Any
    previous_hash: str
    event_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditVerification:
    valid: bool
    first_invalid_sequence: int | None


@dataclass(frozen=True, slots=True)
class _ChainSnapshot:
    events: tuple[AuditEvent, ...]
    verification: AuditVerification


class AuditStore:
    def __init__(
        self,
        connection: sqlite3.Connection | Callable[[], sqlite3.Connection],
        *,
        configured_secret_values: Iterable[str] = (),
    ) -> None:
        failed = False
        try:
            self._connection = (
                connection
                if isinstance(connection, sqlite3.Connection)
                else connection()
            )
            self._secrets = tuple(
                sorted({secret for secret in configured_secret_values if secret})
            )
            self._lock = _CONNECTION_LOCKS[
                id(self._connection) % len(_CONNECTION_LOCKS)
            ]
            with self._lock:
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        run_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        previous_hash TEXT NOT NULL,
                        event_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (run_id, sequence)
                    )
                    """
                )
                object_type = self._connection.execute(
                    "SELECT type FROM sqlite_master WHERE name = ?", ("audit_events",)
                ).fetchone()
                columns = self._connection.execute(
                    "PRAGMA table_info(audit_events)"
                ).fetchall()
                expected_columns = [
                    (0, "run_id", "TEXT", 1, None, 1),
                    (1, "sequence", "INTEGER", 1, None, 2),
                    (2, "event_type", "TEXT", 1, None, 0),
                    (3, "payload", "TEXT", 1, None, 0),
                    (4, "previous_hash", "TEXT", 1, None, 0),
                    (5, "event_hash", "TEXT", 1, None, 0),
                    (6, "created_at", "TEXT", 1, None, 0),
                ]
                normalized_type = None if object_type is None else tuple(object_type)
                normalized_columns = [tuple(column) for column in columns]
                if (
                    normalized_type != ("table",)
                    or normalized_columns != expected_columns
                ):
                    raise ValueError
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()

    def append(self, run_id: str, event_type: str, payload: Any) -> AuditEvent:
        with self._lock:
            return self._append_locked(run_id, event_type, payload)

    def _append_locked(self, run_id: str, event_type: str, payload: Any) -> AuditEvent:
        if not self._metadata_is_safe(run_id) or not self._metadata_is_safe(event_type):
            self._raise_unavailable()
        serialization_failed = False
        try:
            payload_json = self._canonical_payload(self._redact(payload))
            if self._contains_secret(payload_json):
                raise ValueError
        except Exception:
            serialization_failed = True
        if serialization_failed:
            self._raise_unavailable()

        transaction_failed = False
        savepoint_started = False
        savepoint_name = f"safefix_audit_append_{next(_SAVEPOINT_COUNTER)}"
        try:
            self._connection.execute(f"SAVEPOINT {savepoint_name}")
            savepoint_started = True
            snapshot = self._read_chain(run_id)
            if not snapshot.verification.valid:
                raise ValueError
            sequence = len(snapshot.events) + 1
            previous_hash = (
                "" if not snapshot.events else snapshot.events[-1].event_hash
            )
            created_at = datetime.now(UTC)
            created_at_text = self._format_timestamp(created_at)
            event_hash = self._hash_event(
                run_id,
                sequence,
                event_type,
                payload_json,
                created_at_text,
                previous_hash,
            )
            self._connection.execute(
                """
                INSERT INTO audit_events (
                    run_id, sequence, event_type, payload,
                    previous_hash, event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    sequence,
                    event_type,
                    payload_json,
                    previous_hash,
                    event_hash,
                    created_at_text,
                ),
            )
            self._connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        except Exception:
            transaction_failed = True
            if savepoint_started:
                try:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                except Exception:
                    pass
        if transaction_failed:
            self._raise_unavailable()

        return AuditEvent(
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            redacted_payload=json.loads(payload_json),
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=created_at,
        )

    def list_events(self, run_id: str) -> list[AuditEvent]:
        with self._lock:
            return self._list_events_locked(run_id)

    def _list_events_locked(self, run_id: str) -> list[AuditEvent]:
        if not self._metadata_is_safe(run_id):
            self._raise_unavailable()
        failed = False
        events: list[AuditEvent] = []
        try:
            snapshot = self._read_chain(run_id)
            if not snapshot.verification.valid:
                raise ValueError
            events = list(snapshot.events)
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()
        return events

    def verify_chain(self, run_id: str) -> AuditVerification:
        with self._lock:
            return self._verify_chain_locked(run_id)

    def _verify_chain_locked(self, run_id: str) -> AuditVerification:
        if not self._metadata_is_safe(run_id):
            self._raise_unavailable()
        failed = False
        verification = AuditVerification(True, None)
        try:
            verification = self._read_chain(run_id).verification
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()
        return verification

    def _read_chain(self, run_id: str) -> _ChainSnapshot:
        rows = self._connection.execute(
            """
            SELECT run_id, sequence, event_type, payload,
                   previous_hash, event_hash, created_at
            FROM audit_events
            WHERE run_id = ?
               OR (typeof(run_id) != 'text' AND CAST(run_id AS TEXT) = ?)
            ORDER BY sequence
            """,
            (run_id, run_id),
        ).fetchall()
        return self._decode_chain(run_id, rows)

    def _decode_chain(self, run_id: str, rows: list[tuple[Any, ...]]) -> _ChainSnapshot:
        events: list[AuditEvent] = []
        previous_hash = ""
        expected_sequence = 1
        for row in rows:
            try:
                if (
                    len(row) != 7
                    or type(row[0]) is not str
                    or type(row[1]) is not int
                    or any(type(row[index]) is not str for index in range(2, 7))
                ):
                    raise ValueError
                stored_run_id = row[0]
                sequence = row[1]
                event_type = row[2]
                payload_json = row[3]
                row_previous_hash = row[4]
                row_event_hash = row[5]
                created_at_text = row[6]
                if stored_run_id != run_id or not self._metadata_is_safe(event_type):
                    raise ValueError
                self._decode_canonical_payload(payload_json)
                created_at = self._parse_timestamp(created_at_text)
            except Exception:
                return _ChainSnapshot(
                    tuple(events), AuditVerification(False, expected_sequence)
                )
            expected_hash = self._hash_event(
                run_id,
                sequence,
                event_type,
                payload_json,
                created_at_text,
                row_previous_hash,
            )
            if (
                sequence != expected_sequence
                or row_previous_hash != previous_hash
                or row_event_hash != expected_hash
            ):
                return _ChainSnapshot(
                    tuple(events), AuditVerification(False, expected_sequence)
                )
            events.append(
                AuditEvent(
                    run_id=run_id,
                    sequence=sequence,
                    event_type=event_type,
                    redacted_payload=self._decode_canonical_payload(payload_json),
                    previous_hash=row_previous_hash,
                    event_hash=row_event_hash,
                    created_at=created_at,
                )
            )
            previous_hash = row_event_hash
            expected_sequence += 1
        return _ChainSnapshot(tuple(events), AuditVerification(True, None))

    @staticmethod
    def _hash_event(
        run_id: str,
        sequence: int,
        event_type: str,
        payload_json: str,
        created_at: str,
        previous_hash: str,
    ) -> str:
        value = (
            f"{run_id}|{sequence}|{event_type}|{payload_json}|"
            f"{created_at}|{previous_hash}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise TypeError
            result: dict[str, Any] = {}
            for key, item in sorted(value.items()):
                output_key = _REDACTED if self._contains_secret(key) else key
                unique_key = output_key
                collision = 2
                while unique_key in result:
                    unique_key = f"{output_key}#{collision}"
                    collision += 1
                if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
                    result[unique_key] = _REDACTED
                else:
                    result[unique_key] = self._redact(item)
            return result
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        if isinstance(value, str) and self._contains_secret(value):
            return _REDACTED
        return value

    def _contains_secret(self, value: str) -> bool:
        return any(secret in value for secret in self._secrets)

    def _metadata_is_safe(self, value: object) -> bool:
        return type(value) is str and not self._contains_secret(value)

    @staticmethod
    def _canonical_payload(payload: Any) -> str:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def _decode_canonical_payload(self, payload: str) -> Any:
        value = json.loads(payload, parse_constant=self._reject_json_constant)
        if (
            self._canonical_payload(value) != payload
            or self._canonical_payload(self._redact(value)) != payload
        ):
            raise ValueError
        return value

    @staticmethod
    def _reject_json_constant(_value: str) -> NoReturn:
        raise ValueError

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        return (
            value.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )

    @classmethod
    def _parse_timestamp(cls, value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError
        parsed = parsed.astimezone(UTC)
        if cls._format_timestamp(parsed) != value:
            raise ValueError
        return parsed

    @staticmethod
    def _raise_unavailable() -> NoReturn:
        raise AuditUnavailable(_UNAVAILABLE_MESSAGE)
