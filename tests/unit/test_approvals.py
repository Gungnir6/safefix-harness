import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
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


class _InterruptingApprovalConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.operation_signal: BaseException | None = None
        self.rollback_signal: BaseException | None = None
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
        elif normalized.startswith("ROLLBACK TO SAVEPOINT "):
            savepoint_name = normalized.split()[3]
            self.rollback_names.append(savepoint_name)
            result = super().execute(sql, parameters)
            if self.rollback_signal is not None:
                signal = self.rollback_signal
                self.rollback_signal = None
                raise signal
            return result
        elif normalized.startswith("RELEASE SAVEPOINT "):
            self.release_names.append(normalized.split()[2])
        if self.operation_signal is not None and normalized.startswith(
            "UPDATE approval_requests SET status = ?, decided_at = ?"
        ):
            signal = self.operation_signal
            self.operation_signal = None
            super().execute(sql, parameters)
            raise signal
        return super().execute(sql, parameters)
