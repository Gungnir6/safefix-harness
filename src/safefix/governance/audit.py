"""Redacted, hash-chained audit event storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = ("token", "key", "secret", "password", "authorization")
_UNAVAILABLE_MESSAGE = "Audit storage is unavailable"


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
            if normalized_type != ("table",) or normalized_columns != expected_columns:
                raise ValueError
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()

    def append(self, run_id: str, event_type: str, payload: Any) -> AuditEvent:
        serialization_failed = False
        try:
            payload_json = self._canonical_payload(self._redact(payload))
        except Exception:
            serialization_failed = True
        if serialization_failed:
            self._raise_unavailable()

        transaction_failed = False
        savepoint_started = False
        try:
            self._connection.execute("SAVEPOINT safefix_audit_append")
            savepoint_started = True
            row = self._connection.execute(
                """
                SELECT sequence, event_hash
                FROM audit_events
                WHERE run_id = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            sequence = 1 if row is None else int(row[0]) + 1
            previous_hash = "" if row is None else str(row[1])
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
            self._connection.execute("RELEASE SAVEPOINT safefix_audit_append")
        except Exception:
            transaction_failed = True
            if savepoint_started:
                try:
                    self._connection.execute(
                        "ROLLBACK TO SAVEPOINT safefix_audit_append"
                    )
                    self._connection.execute("RELEASE SAVEPOINT safefix_audit_append")
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
        failed = False
        events: list[AuditEvent] = []
        try:
            rows = self._connection.execute(
                """
                SELECT sequence, event_type, payload, previous_hash, event_hash, created_at
                FROM audit_events
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
            events = [
                AuditEvent(
                    run_id=run_id,
                    sequence=int(row[0]),
                    event_type=str(row[1]),
                    redacted_payload=self._decode_canonical_payload(str(row[2])),
                    previous_hash=str(row[3]),
                    event_hash=str(row[4]),
                    created_at=self._parse_timestamp(str(row[5])),
                )
                for row in rows
            ]
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()
        return events

    def verify_chain(self, run_id: str) -> AuditVerification:
        failed = False
        rows: list[tuple[Any, ...]] = []
        try:
            rows = self._connection.execute(
                """
                SELECT sequence, event_type, payload, previous_hash, event_hash, created_at
                FROM audit_events
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()
        previous_hash = ""
        expected_sequence = 1
        for row in rows:
            row_invalid = False
            try:
                sequence = int(row[0])
                event_type = str(row[1])
                payload_json = str(row[2])
                row_previous_hash = str(row[3])
                row_event_hash = str(row[4])
                created_at_text = str(row[5])
                self._decode_canonical_payload(payload_json)
                self._parse_timestamp(created_at_text)
            except Exception:
                row_invalid = True
            if row_invalid:
                return AuditVerification(False, expected_sequence)
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
                return AuditVerification(False, expected_sequence)
            previous_hash = row_event_hash
            expected_sequence += 1
        return AuditVerification(True, None)

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
