"""Persistent, frozen-action approval requests."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn, TypeVar

from pydantic import TypeAdapter

from safefix.domain import (
    Action,
    ApprovalRequest,
    ApprovalStatus,
    RiskLevel,
    action_digest,
)
from safefix.governance.audit import AuditStore

_UNAVAILABLE_MESSAGE = "Approval storage is unavailable"
_NOT_FOUND_MESSAGE = "Approval request was not found"
_INVALID_TOKEN_MESSAGE = "Approval token is invalid"
_ACTION_MISMATCH_MESSAGE = "Approval action does not match"
_ALREADY_USED_MESSAGE = "Approval request has already been used"
_EXPIRED_MESSAGE = "Approval request has expired"
_INVALID_TRANSITION_MESSAGE = "Approval transition is invalid"
_HEX_DIGITS = frozenset("0123456789abcdef")
_CONNECTION_LOCKS = tuple(threading.RLock() for _ in range(64))
_SAVEPOINT_COUNTER = itertools.count()
_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)
_T = TypeVar("_T")


class _WriteThreadState(threading.local):
    def __init__(self) -> None:
        self.active_connection_ids: set[int] = set()


_WRITE_THREAD_STATE = _WriteThreadState()


class ApprovalError(RuntimeError):
    pass


class ApprovalUnavailable(ApprovalError):
    pass


class ApprovalNotFound(ApprovalError):
    pass


class InvalidApprovalToken(ApprovalError):
    pass


class ActionMismatch(ApprovalError):
    pass


class ApprovalAlreadyUsed(ApprovalError):
    pass


class ApprovalExpired(ApprovalError):
    pass


class InvalidApprovalTransition(ApprovalError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalChallenge:
    id: str
    token: str = field(repr=False)
    request: ApprovalRequest


@dataclass(frozen=True, slots=True)
class _StoredApproval:
    id: str
    run_id: str
    action_hash: str
    status: ApprovalStatus
    one_time_token_hash: str
    frozen_action_json: str
    action_type: str
    risk_level: RiskLevel
    rule_ids: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    action: Action


@dataclass(frozen=True, slots=True)
class _PreparedRequest:
    stored: _StoredApproval
    token: str = field(repr=False)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalStateMachine:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        configured_secret_values: Iterable[str] = (),
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._secrets = tuple(
            sorted({value for value in configured_secret_values if value})
        )
        self._lock = _CONNECTION_LOCKS[id(connection) % len(_CONNECTION_LOCKS)]
        self._audit = AuditStore(
            connection,
            configured_secret_values=self._secrets,
        )
        self._initialize_schema()

    def request(
        self,
        run_id: str,
        action: Action,
        risk_level: RiskLevel,
        rule_ids: tuple[str, ...],
        ttl_seconds: int,
    ) -> ApprovalChallenge:
        prepared = self._prepare_request(
            run_id, action, risk_level, rule_ids, ttl_seconds
        )
        return self._run_write(lambda: self._insert_request(prepared))

    def get(self, approval_id: str) -> ApprovalRequest:
        failure: ApprovalError | None = None
        result: ApprovalRequest | None = None
        with self._lock:
            try:
                result = self._to_request(self._read_one(approval_id))
            except ApprovalError as error:
                failure = error
            except Exception:
                failure = ApprovalUnavailable(_UNAVAILABLE_MESSAGE)
        if failure is not None:
            failure.__traceback__ = None
            raise failure from None
        if result is None:
            self._raise_unavailable()
        return result

    def approve(
        self,
        approval_id: str,
        plaintext_token: object,
        action: Action,
    ) -> ApprovalRequest:
        request, expired = self._run_write(
            lambda: self._approve(approval_id, plaintext_token, action)
        )
        if expired:
            self._raise_expired()
        return request

    def _initialize_schema(self) -> None:
        failed = False
        try:
            with self._lock:
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approval_requests (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        action_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        one_time_token_hash TEXT NOT NULL UNIQUE,
                        frozen_action_json TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        risk_level TEXT NOT NULL,
                        rule_ids TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        decided_at TEXT
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                        idx_approval_requests_status_expires_at
                    ON approval_requests (status, expires_at)
                    """
                )
                self._verify_schema()
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()

    def _verify_schema(self) -> None:
        object_type = self._connection.execute(
            "SELECT type FROM sqlite_master WHERE name = ?",
            ("approval_requests",),
        ).fetchone()
        columns = self._connection.execute(
            "PRAGMA table_info(approval_requests)"
        ).fetchall()
        expected_columns = [
            (0, "id", "TEXT", 0, None, 1),
            (1, "run_id", "TEXT", 1, None, 0),
            (2, "action_hash", "TEXT", 1, None, 0),
            (3, "status", "TEXT", 1, None, 0),
            (4, "one_time_token_hash", "TEXT", 1, None, 0),
            (5, "frozen_action_json", "TEXT", 1, None, 0),
            (6, "action_type", "TEXT", 1, None, 0),
            (7, "risk_level", "TEXT", 1, None, 0),
            (8, "rule_ids", "TEXT", 1, None, 0),
            (9, "created_at", "TEXT", 1, None, 0),
            (10, "expires_at", "TEXT", 1, None, 0),
            (11, "decided_at", "TEXT", 0, None, 0),
        ]
        if (
            object_type is None
            or tuple(object_type) != ("table",)
            or [tuple(column) for column in columns] != expected_columns
        ):
            raise ValueError

        indexes = self._connection.execute(
            "PRAGMA index_list(approval_requests)"
        ).fetchall()
        has_token_unique = False
        has_expiry_index = False
        for index in indexes:
            if (
                len(index) != 5
                or type(index[1]) is not str
                or type(index[2]) is not int
                or type(index[3]) is not str
                or type(index[4]) is not int
            ):
                raise ValueError
            name = index[1]
            signature = tuple(
                row[2]
                for row in self._connection.execute(
                    f'PRAGMA index_info("{name}")'
                ).fetchall()
            )
            if index[2:] == (1, "u", 0) and signature == ("one_time_token_hash",):
                has_token_unique = True
            if (
                name == "idx_approval_requests_status_expires_at"
                and index[2:] == (0, "c", 0)
                and signature == ("status", "expires_at")
            ):
                has_expiry_index = True
        if not has_token_unique or not has_expiry_index:
            raise ValueError
        for schema in ("sqlite_master", "sqlite_temp_master"):
            triggers = self._connection.execute(
                f"SELECT tbl_name, sql FROM {schema} WHERE type = 'trigger'"
            ).fetchall()
            for trigger in triggers:
                if (
                    len(trigger) != 2
                    or type(trigger[0]) is not str
                    or type(trigger[1]) is not str
                    or trigger[0].casefold() == "approval_requests"
                    or "approval_requests" in trigger[1].casefold()
                ):
                    raise ValueError

    def _prepare_request(
        self,
        run_id: str,
        action: Action,
        risk_level: RiskLevel,
        rule_ids: tuple[str, ...],
        ttl_seconds: int,
    ) -> _PreparedRequest:
        if (
            type(run_id) is not str
            or not run_id.strip()
            or risk_level is not RiskLevel.MEDIUM
            or type(rule_ids) is not tuple
            or not rule_ids
            or any(
                type(rule_id) is not str or not rule_id.strip() for rule_id in rule_ids
            )
            or type(ttl_seconds) is not int
            or ttl_seconds <= 0
        ):
            raise InvalidApprovalTransition(_INVALID_TRANSITION_MESSAGE)

        failed = False
        prepared: _PreparedRequest | None = None
        try:
            frozen_action_json = action.model_dump_json(exclude_none=True)
            action_hash = action_digest(action)
            action_type = action.type
            sorted_rule_ids = tuple(sorted(rule_ids))
            rule_ids_json = self._format_rule_ids(sorted_rule_ids)
            if any(
                self._contains_secret(value)
                for value in (run_id, frozen_action_json, action_type, rule_ids_json)
            ):
                raise ValueError
            created_at = self._normalize_timestamp(self._clock())
            expires_at = self._normalize_timestamp(
                created_at + timedelta(seconds=ttl_seconds)
            )
            approval_id = str(uuid.uuid4())
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            stored = _StoredApproval(
                id=approval_id,
                run_id=run_id,
                action_hash=action_hash,
                status=ApprovalStatus.PENDING,
                one_time_token_hash=token_hash,
                frozen_action_json=frozen_action_json,
                action_type=action_type,
                risk_level=RiskLevel.MEDIUM,
                rule_ids=sorted_rule_ids,
                created_at=created_at,
                expires_at=expires_at,
                decided_at=None,
                action=action,
            )
            if self._decode_row(self._stored_row(stored)) != stored:
                raise ValueError
            prepared = _PreparedRequest(stored=stored, token=token)
        except Exception:
            failed = True
        if failed or prepared is None:
            self._raise_unavailable()
        return prepared

    def _insert_request(self, prepared: _PreparedRequest) -> ApprovalChallenge:
        stored = prepared.stored
        self._connection.execute(
            """
            INSERT INTO approval_requests (
                id, run_id, action_hash, status, one_time_token_hash,
                frozen_action_json, action_type, risk_level, rule_ids,
                created_at, expires_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._stored_row(stored),
        )
        self._audit.append(
            stored.run_id,
            "APPROVAL_REQUESTED",
            self._audit_payload(stored),
        )
        persisted = self._read_one(stored.id)
        if persisted != stored:
            raise ValueError
        request = self._to_request(persisted)
        return ApprovalChallenge(id=stored.id, token=prepared.token, request=request)

    def _approve(
        self,
        approval_id: str,
        plaintext_token: object,
        action: Action,
    ) -> tuple[ApprovalRequest, bool]:
        stored = self._read_one(approval_id)
        if stored.status is not ApprovalStatus.PENDING:
            raise ApprovalAlreadyUsed(_ALREADY_USED_MESSAGE)
        decided_at = self._read_clock()
        if decided_at >= stored.expires_at:
            return self._expire_during_approval(stored, decided_at), True
        self._verify_token(stored.one_time_token_hash, plaintext_token)
        try:
            self._verify_action(stored, action)
        except ActionMismatch:
            raise
        except Exception:
            self._raise_action_mismatch()

        cursor = self._connection.execute(
            "UPDATE approval_requests SET status = ?, decided_at = ? "
            "WHERE id = ? AND status = ?",
            (
                ApprovalStatus.APPROVED.value,
                self._format_timestamp(decided_at),
                stored.id,
                ApprovalStatus.PENDING.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ApprovalAlreadyUsed(_ALREADY_USED_MESSAGE)
        expected = replace(
            stored,
            status=ApprovalStatus.APPROVED,
            decided_at=decided_at,
        )
        self._audit.append(
            expected.run_id,
            "APPROVAL_APPROVED",
            self._audit_payload(expected),
        )
        persisted = self._read_one(stored.id)
        if persisted != expected:
            raise ValueError
        return self._to_request(persisted), False

    def _expire_during_approval(
        self,
        stored: _StoredApproval,
        decided_at: datetime,
    ) -> ApprovalRequest:
        cursor = self._connection.execute(
            "UPDATE approval_requests SET status = ?, decided_at = ? "
            "WHERE id = ? AND status = ?",
            (
                ApprovalStatus.EXPIRED.value,
                self._format_timestamp(decided_at),
                stored.id,
                ApprovalStatus.PENDING.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ApprovalAlreadyUsed(_ALREADY_USED_MESSAGE)
        expected = replace(
            stored,
            status=ApprovalStatus.EXPIRED,
            decided_at=decided_at,
        )
        self._audit.append(
            expected.run_id,
            "APPROVAL_EXPIRED",
            self._audit_payload(expected),
        )
        persisted = self._read_one(stored.id)
        if persisted != expected:
            raise ValueError
        return self._to_request(persisted)

    def _verify_token(self, stored_hash: str, plaintext_token: object) -> None:
        if type(plaintext_token) is not str:
            self._raise_invalid_token()
        supplied_hash = hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(stored_hash, supplied_hash):
            self._raise_invalid_token()

    def _verify_action(self, stored: _StoredApproval, action: Action) -> None:
        frozen_json = action.model_dump_json(exclude_none=True)
        if (
            self._contains_secret(frozen_json)
            or not hmac.compare_digest(stored.action_hash, action_digest(action))
            or frozen_json != stored.frozen_action_json
        ):
            self._raise_action_mismatch()

    def _read_clock(self) -> datetime:
        failed = False
        value: datetime | None = None
        try:
            value = self._normalize_timestamp(self._clock())
        except Exception:
            failed = True
        if failed or value is None:
            self._raise_unavailable()
        return value

    def _read_one(self, approval_id: str) -> _StoredApproval:
        if type(approval_id) is not str or not approval_id:
            raise ApprovalNotFound(_NOT_FOUND_MESSAGE)
        if self._contains_secret(approval_id):
            raise ValueError
        rows = self._connection.execute(
            """
            SELECT id, run_id, action_hash, status, one_time_token_hash,
                   frozen_action_json, action_type, risk_level, rule_ids,
                   created_at, expires_at, decided_at
            FROM approval_requests
            WHERE id = ?
               OR (typeof(id) != 'text' AND CAST(id AS TEXT) = ?)
            """,
            (approval_id, approval_id),
        ).fetchall()
        if not rows:
            raise ApprovalNotFound(_NOT_FOUND_MESSAGE)
        if len(rows) != 1:
            raise ValueError
        return self._decode_row(tuple(rows[0]))

    def _decode_row(self, row: tuple[Any, ...]) -> _StoredApproval:
        if (
            len(row) != 12
            or any(type(row[index]) is not str for index in range(11))
            or (row[11] is not None and type(row[11]) is not str)
        ):
            raise ValueError
        (
            approval_id,
            run_id,
            action_hash,
            status_text,
            token_hash,
            frozen_action_json,
            action_type,
            risk_text,
            rule_ids_json,
            created_at_text,
            expires_at_text,
            decided_at_text,
        ) = row
        if (
            str(uuid.UUID(approval_id)) != approval_id
            or not run_id.strip()
            or not self._is_sha256(action_hash)
            or not self._is_sha256(token_hash)
            or not action_type
            or any(
                self._contains_secret(value)
                for value in (run_id, frozen_action_json, action_type, rule_ids_json)
            )
        ):
            raise ValueError
        status = ApprovalStatus(status_text)
        risk_level = RiskLevel(risk_text)
        if risk_level is not RiskLevel.MEDIUM:
            raise ValueError
        rule_ids = self._parse_rule_ids(rule_ids_json)
        action = _ACTION_ADAPTER.validate_json(frozen_action_json)
        if (
            action.model_dump_json(exclude_none=True) != frozen_action_json
            or action.type != action_type
            or action_digest(action) != action_hash
        ):
            raise ValueError
        created_at = self._parse_timestamp(created_at_text)
        expires_at = self._parse_timestamp(expires_at_text)
        if expires_at <= created_at:
            raise ValueError
        decided_at = (
            None if decided_at_text is None else self._parse_timestamp(decided_at_text)
        )
        if (status is ApprovalStatus.PENDING) != (decided_at is None):
            raise ValueError
        return _StoredApproval(
            id=approval_id,
            run_id=run_id,
            action_hash=action_hash,
            status=status,
            one_time_token_hash=token_hash,
            frozen_action_json=frozen_action_json,
            action_type=action_type,
            risk_level=risk_level,
            rule_ids=rule_ids,
            created_at=created_at,
            expires_at=expires_at,
            decided_at=decided_at,
            action=action,
        )

    @staticmethod
    def _to_request(stored: _StoredApproval) -> ApprovalRequest:
        return ApprovalRequest(
            id=stored.id,
            run_id=stored.run_id,
            action_hash=stored.action_hash,
            status=stored.status,
            one_time_token_hash=stored.one_time_token_hash,
            frozen_action_json=stored.frozen_action_json,
            created_at=stored.created_at,
            expires_at=stored.expires_at,
            decided_at=stored.decided_at,
        )

    @staticmethod
    def _stored_row(stored: _StoredApproval) -> tuple[Any, ...]:
        return (
            stored.id,
            stored.run_id,
            stored.action_hash,
            stored.status.value,
            stored.one_time_token_hash,
            stored.frozen_action_json,
            stored.action_type,
            stored.risk_level.value,
            ApprovalStateMachine._format_rule_ids(stored.rule_ids),
            ApprovalStateMachine._format_timestamp(stored.created_at),
            ApprovalStateMachine._format_timestamp(stored.expires_at),
            None
            if stored.decided_at is None
            else ApprovalStateMachine._format_timestamp(stored.decided_at),
        )

    @staticmethod
    def _audit_payload(stored: _StoredApproval) -> dict[str, object]:
        return {
            "approval_id": stored.id,
            "status": stored.status.value,
            "action_hash": stored.action_hash,
            "risk_level": stored.risk_level.value,
            "rule_ids": list(stored.rule_ids),
        }

    def _run_write(self, operation: Callable[[], _T]) -> _T:
        with self._lock:
            connection_id = id(self._connection)
            active = _WRITE_THREAD_STATE.active_connection_ids
            if connection_id in active:
                self._raise_unavailable()
            active.add(connection_id)
            try:
                return self._run_savepoint(operation)
            finally:
                active.remove(connection_id)

    def _run_savepoint(self, operation: Callable[[], _T]) -> _T:
        failure: BaseException | None = None
        result: _T | None = None
        savepoint_name = f"safefix_approval_write_{next(_SAVEPOINT_COUNTER)}"
        savepoint_active = True
        try:
            self._connection.execute(f"SAVEPOINT {savepoint_name}")
            self._lock_and_verify_write_schema()
            result = operation()
            self._verify_schema()
            self._connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            savepoint_active = False
        except BaseException as error:
            failure = error
        cleanup_process_control = None
        if savepoint_active:
            cleanup_process_control = self._cleanup_savepoint(savepoint_name)
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            self._propagate_failure(failure)
        if cleanup_process_control is not None:
            self._propagate_failure(cleanup_process_control)
        if failure is not None:
            self._propagate_failure(failure)
        if result is None:
            self._raise_unavailable()
        return result

    def _lock_and_verify_write_schema(self) -> None:
        self._connection.execute("UPDATE approval_requests SET id = id WHERE 0")
        self._verify_schema()

    def _cleanup_savepoint(self, savepoint_name: str) -> BaseException | None:
        process_control: BaseException | None = None
        statements = (
            f"ROLLBACK TO SAVEPOINT {savepoint_name}",
            f"RELEASE SAVEPOINT {savepoint_name}",
        )
        for statement in statements:
            for _attempt in range(2):
                try:
                    self._connection.execute(statement)
                    break
                except BaseException as error:
                    if process_control is None and isinstance(
                        error, (KeyboardInterrupt, SystemExit)
                    ):
                        process_control = error
        return process_control

    @classmethod
    def _propagate_failure(cls, failure: BaseException) -> NoReturn:
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise failure
        if isinstance(failure, ApprovalError):
            failure.__cause__ = None
            failure.__context__ = None
            failure.__suppress_context__ = False
            failure.__traceback__ = None
            raise failure
        if isinstance(failure, Exception):
            cls._raise_unavailable()
        raise failure

    def _contains_secret(self, value: str) -> bool:
        return any(secret in value for secret in self._secrets)

    @staticmethod
    def _is_sha256(value: str) -> bool:
        return len(value) == 64 and all(character in _HEX_DIGITS for character in value)

    @staticmethod
    def _format_rule_ids(rule_ids: tuple[str, ...]) -> str:
        return json.dumps(list(rule_ids), separators=(",", ":"), ensure_ascii=False)

    def _parse_rule_ids(self, value: str) -> tuple[str, ...]:
        parsed = json.loads(value)
        if (
            type(parsed) is not list
            or not parsed
            or any(type(item) is not str or not item.strip() for item in parsed)
        ):
            raise ValueError
        rule_ids = tuple(parsed)
        if (
            tuple(sorted(rule_ids)) != rule_ids
            or self._format_rule_ids(rule_ids) != value
        ):
            raise ValueError
        return rule_ids

    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError
        offset = value.utcoffset()
        if offset is None:
            raise ValueError
        return cls._parse_timestamp(cls._format_timestamp(value))

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
        raise ApprovalUnavailable(_UNAVAILABLE_MESSAGE)

    @staticmethod
    def _raise_invalid_token() -> NoReturn:
        raise InvalidApprovalToken(_INVALID_TOKEN_MESSAGE) from None

    @staticmethod
    def _raise_action_mismatch() -> NoReturn:
        raise ActionMismatch(_ACTION_MISMATCH_MESSAGE) from None

    @staticmethod
    def _raise_expired() -> NoReturn:
        raise ApprovalExpired(_EXPIRED_MESSAGE) from None
