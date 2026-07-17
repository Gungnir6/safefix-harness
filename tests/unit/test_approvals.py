import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from safefix.domain import Action, ApprovalStatus, RiskLevel, RunProcessAction
from safefix.governance.approvals import (
    ActionMismatch,
    ApprovalAlreadyUsed,
    ApprovalStateMachine,
    ApprovalUnavailable,
    InvalidApprovalTransition,
)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    value = sqlite3.connect(":memory:")
    yield value
    value.close()


@pytest.fixture
def approval_store(connection: sqlite3.Connection) -> ApprovalStateMachine:
    return ApprovalStateMachine(connection)


@pytest.fixture
def risky_action() -> RunProcessAction:
    return RunProcessAction(
        id="a1",
        reason="commit",
        program="git",
        args=("commit", "-m", "ok"),
    )


def test_approval_cannot_authorize_changed_action(
    approval_store: ApprovalStateMachine,
) -> None:
    original = RunProcessAction(
        id="a1", reason="commit", program="git", args=("commit", "-m", "ok")
    )
    changed = RunProcessAction(id="a1", reason="commit", program="git", args=("push",))
    challenge = approval_store.request(
        "run-1", original, RiskLevel.MEDIUM, ("CMD_GIT_WRITE",), 300
    )
    with pytest.raises(ActionMismatch):
        approval_store.approve(challenge.id, challenge.token, changed)


def test_approval_token_is_single_use(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("CMD_GIT_WRITE",), 300
    )
    approval_store.approve(challenge.id, challenge.token, risky_action)
    with pytest.raises(ApprovalAlreadyUsed):
        approval_store.approve(challenge.id, challenge.token, risky_action)


def test_request_persists_only_token_digest_and_returns_redacted_challenge_repr(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("CMD_GIT_WRITE",), 300
    )
    row = connection.execute(
        "SELECT one_time_token_hash, frozen_action_json, status "
        "FROM approval_requests WHERE id = ?",
        (challenge.id,),
    ).fetchone()
    assert row is not None
    assert challenge.token not in repr(challenge)
    assert challenge.token not in repr(row)
    assert row[0] == hashlib.sha256(challenge.token.encode("utf-8")).hexdigest()
    assert row[1] == risky_action.model_dump_json(exclude_none=True)
    assert row[2] == "PENDING"


@pytest.mark.parametrize("risk", [RiskLevel.LOW, RiskLevel.HIGH])
def test_request_accepts_only_medium_risk(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
    risk: RiskLevel,
) -> None:
    with pytest.raises(InvalidApprovalTransition):
        approval_store.request("run-1", risky_action, risk, ("RULE",), 300)


def test_request_rejects_non_positive_ttl_and_empty_rules(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    with pytest.raises(InvalidApprovalTransition):
        approval_store.request("run-1", risky_action, RiskLevel.MEDIUM, (), 300)
    with pytest.raises(InvalidApprovalTransition):
        approval_store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 0)


def test_request_round_trip_and_audit_payload_are_exact(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE_B", "RULE_A"), 300
    )
    assert store.get(challenge.id) == challenge.request
    assert challenge.request.status is ApprovalStatus.PENDING
    assert challenge.request.created_at.tzinfo is not None
    assert challenge.request.expires_at - challenge.request.created_at == timedelta(
        seconds=300
    )
    stored_rules = connection.execute(
        "SELECT rule_ids FROM approval_requests WHERE id = ?", (challenge.id,)
    ).fetchone()[0]
    assert stored_rules == '["RULE_A","RULE_B"]'
    event = store._audit.list_events("run-1")[-1]
    assert event.event_type == "APPROVAL_REQUESTED"
    assert event.redacted_payload == {
        "action_hash": challenge.request.action_hash,
        "approval_id": challenge.id,
        "risk_level": "MEDIUM",
        "rule_ids": ["RULE_A", "RULE_B"],
        "status": "PENDING",
    }
    assert challenge.token not in repr(event)
    assert challenge.request.one_time_token_hash not in repr(event)
    assert challenge.request.frozen_action_json not in repr(event)


def test_approve_rejects_cross_row_approval_trigger_tampering(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    first = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    second_action = risky_action.model_copy(update={"id": "a2"})
    second = store.request("run-1", second_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.execute(
        """
        CREATE TRIGGER approve_another_request
        AFTER UPDATE ON approval_requests
        WHEN NEW.status = 'APPROVED'
        BEGIN
            UPDATE approval_requests
            SET status = 'APPROVED', decided_at = NEW.decided_at
            WHERE id != NEW.id AND status = 'PENDING';
        END
        """
    )

    with pytest.raises(ApprovalUnavailable):
        store.approve(first.id, first.token, risky_action)

    rows = connection.execute(
        "SELECT id, status, decided_at FROM approval_requests ORDER BY id"
    ).fetchall()
    assert rows == sorted([(first.id, "PENDING", None), (second.id, "PENDING", None)])
    assert all(
        event.event_type != "APPROVAL_APPROVED"
        for event in store._audit.list_events("run-1")
    )


@pytest.mark.parametrize("calls_before_failure", [0, 1])
def test_action_boundary_failure_has_clean_stable_error(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
    calls_before_failure: int,
) -> None:
    secret = "must-not-leak-action-boundary-secret"
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    action = cast(
        Action,
        _ExplodingAction(
            risky_action.model_dump_json(exclude_none=True),
            calls_before_failure,
            secret,
        ),
    )

    with pytest.raises(ActionMismatch) as captured:
        store.approve(challenge.id, challenge.token, action)

    assert str(captured.value) == "Approval action does not match"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert secret not in repr(captured.value)
    assert store.get(challenge.id).status is ApprovalStatus.PENDING
    assert all(
        event.event_type != "APPROVAL_APPROVED"
        for event in store._audit.list_events("run-1")
    )


@pytest.mark.parametrize("signal", [KeyboardInterrupt(), SystemExit()])
def test_process_control_exception_cleans_savepoint_and_recovers(
    signal: BaseException,
    risky_action: RunProcessAction,
) -> None:
    connection = sqlite3.connect(":memory:", factory=_InterruptingApprovalConnection)
    assert isinstance(connection, _InterruptingApprovalConnection)
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.clear_tracking()
    connection.operation_signal = signal

    with pytest.raises(type(signal)) as captured:
        store.approve(challenge.id, challenge.token, risky_action)

    assert captured.value is signal
    assert len(connection.savepoint_names) == 1
    savepoint_name = connection.savepoint_names[0]
    assert connection.rollback_names == [savepoint_name]
    assert connection.release_names == [savepoint_name]
    with pytest.raises(sqlite3.OperationalError):
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
    assert store.get(challenge.id).status is ApprovalStatus.PENDING
    assert (
        store.approve(challenge.id, challenge.token, risky_action).status
        is ApprovalStatus.APPROVED
    )
    connection.close()


@pytest.mark.parametrize("operation_signal", [KeyboardInterrupt(), SystemExit()])
def test_cleanup_base_exception_preserves_original_and_recovers(
    operation_signal: BaseException,
    risky_action: RunProcessAction,
) -> None:
    connection = sqlite3.connect(":memory:", factory=_InterruptingApprovalConnection)
    assert isinstance(connection, _InterruptingApprovalConnection)
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.clear_tracking()
    connection.operation_signal = operation_signal
    connection.rollback_signal = _CleanupBaseException("cleanup")

    with pytest.raises(type(operation_signal)) as captured:
        store.approve(challenge.id, challenge.token, risky_action)

    assert captured.value is operation_signal
    assert len(connection.savepoint_names) == 1
    savepoint_name = connection.savepoint_names[0]
    assert connection.rollback_names == [savepoint_name, savepoint_name]
    assert connection.release_names == [savepoint_name]
    with pytest.raises(sqlite3.OperationalError):
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
    assert store.get(challenge.id).status is ApprovalStatus.PENDING
    assert (
        store.approve(challenge.id, challenge.token, risky_action).status
        is ApprovalStatus.APPROVED
    )
    connection.close()


@pytest.mark.parametrize("callback_kind", ["clock", "action"])
def test_caller_callback_cannot_install_approval_trigger(
    callback_kind: str,
    risky_action: RunProcessAction,
) -> None:
    connection = sqlite3.connect(":memory:")
    clock = _TriggerInstallingClock(
        connection,
        install_on_call=3 if callback_kind == "clock" else None,
    )
    store = ApprovalStateMachine(connection, clock=clock)
    first = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    second_action = risky_action.model_copy(update={"id": "a2"})
    second = store.request("run-1", second_action, RiskLevel.MEDIUM, ("RULE",), 300)
    approval_action = (
        cast(
            Action,
            _TriggerInstallingAction(
                connection,
                risky_action.model_dump_json(exclude_none=True),
            ),
        )
        if callback_kind == "action"
        else risky_action
    )

    with pytest.raises(ApprovalUnavailable):
        store.approve(first.id, first.token, approval_action)

    assert _approval_states(connection) == sorted(
        [(first.id, "PENDING", None), (second.id, "PENDING", None)]
    )
    assert all(
        event.event_type != "APPROVAL_APPROVED"
        for event in store._audit.list_events("run-1")
    )
    connection.close()


def test_audit_trigger_cannot_cross_update_another_approval(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    first = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    second_action = risky_action.model_copy(update={"id": "a2"})
    second = store.request("run-1", second_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.execute(
        """
        CREATE TRIGGER audit_approves_another_request
        AFTER INSERT ON audit_events
        WHEN NEW.event_type = 'APPROVAL_APPROVED'
        BEGIN
            UPDATE approval_requests
            SET status = 'APPROVED', decided_at = '2026-07-17T00:00:00.000000Z'
            WHERE id != json_extract(NEW.payload, '$.approval_id')
              AND status = 'PENDING';
        END
        """
    )

    with pytest.raises(ApprovalUnavailable):
        store.approve(first.id, first.token, risky_action)

    assert _approval_states(connection) == sorted(
        [(first.id, "PENDING", None), (second.id, "PENDING", None)]
    )
    assert all(
        event.event_type != "APPROVAL_APPROVED"
        for event in store._audit.list_events("run-1")
    )


def test_savepoint_after_execute_exception_is_cleaned_and_recovers(
    risky_action: RunProcessAction,
) -> None:
    connection, store, challenge = _interruptible_approval(risky_action)
    signal = KeyboardInterrupt()
    connection.savepoint_after_signal = signal

    _assert_interrupted_approval_recovers(
        connection,
        store,
        challenge,
        risky_action,
        signal,
        expected_rollback_attempts=1,
        expected_release_attempts=1,
    )


def test_rollback_before_execute_exception_is_retried_and_recovers(
    risky_action: RunProcessAction,
) -> None:
    connection, store, challenge = _interruptible_approval(risky_action)
    signal = KeyboardInterrupt()
    connection.operation_signal = signal
    connection.rollback_before_signal = SystemExit("cleanup")

    _assert_interrupted_approval_recovers(
        connection,
        store,
        challenge,
        risky_action,
        signal,
        expected_rollback_attempts=2,
        expected_release_attempts=1,
    )


def test_release_before_execute_exception_is_retried_and_recovers(
    risky_action: RunProcessAction,
) -> None:
    connection, store, challenge = _interruptible_approval(risky_action)
    signal = SystemExit()
    connection.operation_signal = signal
    connection.release_before_signal = KeyboardInterrupt("cleanup")

    _assert_interrupted_approval_recovers(
        connection,
        store,
        challenge,
        risky_action,
        signal,
        expected_rollback_attempts=1,
        expected_release_attempts=2,
    )


class _ExplodingAction:
    def __init__(
        self,
        canonical_json: str,
        calls_before_failure: int,
        secret: str,
    ) -> None:
        self._canonical_json = canonical_json
        self._calls_before_failure = calls_before_failure
        self._secret = secret

    def model_dump_json(self, *, exclude_none: bool = False) -> str:
        del exclude_none
        if self._calls_before_failure == 0:
            raise RuntimeError(self._secret)
        self._calls_before_failure -= 1
        return self._canonical_json


class _CleanupBaseException(BaseException):
    pass


class _TriggerInstallingClock:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        install_on_call: int | None,
    ) -> None:
        self._connection = connection
        self._install_on_call = install_on_call
        self._calls = 0

    def __call__(self) -> datetime:
        self._calls += 1
        if self._calls == self._install_on_call:
            _install_cross_row_trigger(self._connection, "callback_trigger")
        return datetime(2026, 7, 17, 12, 0, self._calls, tzinfo=UTC)


class _TriggerInstallingAction:
    def __init__(self, connection: sqlite3.Connection, canonical_json: str) -> None:
        self._connection = connection
        self._canonical_json = canonical_json
        self._installed = False

    def model_dump_json(self, *, exclude_none: bool = False) -> str:
        del exclude_none
        if not self._installed:
            _install_cross_row_trigger(self._connection, "action_callback_trigger")
            self._installed = True
        return self._canonical_json


class _InterruptingApprovalConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.operation_signal: BaseException | None = None
        self.rollback_signal: BaseException | None = None
        self.savepoint_after_signal: BaseException | None = None
        self.rollback_before_signal: BaseException | None = None
        self.release_before_signal: BaseException | None = None
        self.savepoint_names: list[str] = []
        self.rollback_names: list[str] = []
        self.release_names: list[str] = []

    def clear_tracking(self) -> None:
        self.savepoint_names.clear()
        self.rollback_names.clear()
        self.release_names.clear()

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        normalized = " ".join(sql.split())
        if normalized.startswith("SAVEPOINT "):
            self.savepoint_names.append(normalized.split()[1])
            if self.savepoint_after_signal is not None:
                signal = self.savepoint_after_signal
                self.savepoint_after_signal = None
                super().execute(sql, parameters)
                raise signal
        elif normalized.startswith("ROLLBACK TO SAVEPOINT "):
            savepoint_name = normalized.split()[3]
            self.rollback_names.append(savepoint_name)
            if self.rollback_before_signal is not None:
                signal = self.rollback_before_signal
                self.rollback_before_signal = None
                raise signal
            result = super().execute(sql, parameters)
            if self.rollback_signal is not None:
                signal = self.rollback_signal
                self.rollback_signal = None
                raise signal
            return result
        elif normalized.startswith("RELEASE SAVEPOINT "):
            self.release_names.append(normalized.split()[2])
            if self.release_before_signal is not None:
                signal = self.release_before_signal
                self.release_before_signal = None
                raise signal
        if self.operation_signal is not None and normalized.startswith(
            "UPDATE approval_requests SET status = ?, decided_at = ?"
        ):
            signal = self.operation_signal
            self.operation_signal = None
            super().execute(sql, parameters)
            raise signal
        return super().execute(sql, parameters)


def _install_cross_row_trigger(
    connection: sqlite3.Connection,
    trigger_name: str,
) -> None:
    connection.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        AFTER UPDATE ON approval_requests
        WHEN NEW.status = 'APPROVED'
        BEGIN
            UPDATE approval_requests
            SET status = 'APPROVED', decided_at = NEW.decided_at
            WHERE id != NEW.id AND status = 'PENDING';
        END
        """
    )


def _approval_states(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT id, status, decided_at FROM approval_requests ORDER BY id"
    ).fetchall()


def _interruptible_approval(
    risky_action: RunProcessAction,
) -> tuple[
    _InterruptingApprovalConnection,
    ApprovalStateMachine,
    object,
]:
    connection = sqlite3.connect(":memory:", factory=_InterruptingApprovalConnection)
    assert isinstance(connection, _InterruptingApprovalConnection)
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.clear_tracking()
    return connection, store, challenge


def _assert_interrupted_approval_recovers(
    connection: _InterruptingApprovalConnection,
    store: ApprovalStateMachine,
    challenge: Any,
    risky_action: RunProcessAction,
    signal: BaseException,
    *,
    expected_rollback_attempts: int,
    expected_release_attempts: int,
) -> None:
    with pytest.raises(type(signal)) as captured:
        store.approve(challenge.id, challenge.token, risky_action)

    assert captured.value is signal
    assert len(connection.savepoint_names) == 1
    savepoint_name = connection.savepoint_names[0]
    assert connection.rollback_names == [savepoint_name] * expected_rollback_attempts
    assert connection.release_names == [savepoint_name] * expected_release_attempts
    with pytest.raises(sqlite3.OperationalError):
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
    assert store.get(challenge.id).status is ApprovalStatus.PENDING
    assert (
        store.approve(challenge.id, challenge.token, risky_action).status
        is ApprovalStatus.APPROVED
    )
    connection.close()
