import hashlib
import sqlite3
import threading
import traceback
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from safefix.domain import (
    Action,
    ApprovalRequest,
    ApprovalStatus,
    RiskLevel,
    RunProcessAction,
)
from safefix.governance.approvals import (
    ActionMismatch,
    ApprovalAlreadyUsed,
    ApprovalChallenge,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalStateMachine,
    ApprovalUnavailable,
    InvalidApprovalToken,
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


def test_wrong_token_does_not_consume_pending_request(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    with pytest.raises(InvalidApprovalToken):
        approval_store.approve(challenge.id, "wrong-token", risky_action)
    assert approval_store.get(challenge.id).status is ApprovalStatus.PENDING
    assert (
        approval_store.approve(challenge.id, challenge.token, risky_action).status
        is ApprovalStatus.APPROVED
    )


def test_action_mismatch_does_not_consume_pending_request(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    changed = risky_action.model_copy(update={"args": ("push",)})
    with pytest.raises(ActionMismatch):
        approval_store.approve(challenge.id, challenge.token, changed)
    assert approval_store.get(challenge.id).status is ApprovalStatus.PENDING


def test_approve_missing_id_is_stable_not_found(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    with pytest.raises(ApprovalNotFound) as captured:
        approval_store.approve(
            "00000000-0000-0000-0000-000000000001",
            "unused-token",
            risky_action,
        )

    assert str(captured.value) == "Approval request was not found"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_token_from_another_request_is_rejected(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    first = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    second_action = risky_action.model_copy(update={"id": "a2"})
    second = approval_store.request(
        "run-1", second_action, RiskLevel.MEDIUM, ("RULE",), 300
    )

    with pytest.raises(InvalidApprovalToken):
        approval_store.approve(first.id, second.token, risky_action)
    with pytest.raises(InvalidApprovalToken):
        approval_store.approve(second.id, first.token, second_action)

    assert approval_store.get(first.id).status is ApprovalStatus.PENDING
    assert approval_store.get(second.id).status is ApprovalStatus.PENDING


def test_approved_request_replay_is_already_used(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    approval_store.approve(challenge.id, challenge.token, risky_action)

    with pytest.raises(ApprovalAlreadyUsed) as captured:
        approval_store.approve(challenge.id, challenge.token, risky_action)

    assert str(captured.value) == "Approval request has already been used"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert (
        sum(
            event.event_type == "APPROVAL_APPROVED"
            for event in approval_store._audit.list_events("run-1")
        )
        == 1
    )


def test_approve_sets_exact_decided_at(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    fixed = datetime(2026, 7, 17, 12, 34, 56, 123456, tzinfo=UTC)
    store = ApprovalStateMachine(connection, clock=lambda: fixed)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)

    approved = store.approve(challenge.id, challenge.token, risky_action)

    assert approved.decided_at == fixed
    assert store.get(challenge.id).decided_at == fixed


def test_approved_audit_payload_excludes_capability_and_action(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE_B", "RULE_A"), 300
    )
    approved = approval_store.approve(challenge.id, challenge.token, risky_action)

    event = approval_store._audit.list_events("run-1")[-1]
    assert event.event_type == "APPROVAL_APPROVED"
    assert event.redacted_payload == {
        "action_hash": approved.action_hash,
        "approval_id": challenge.id,
        "risk_level": "MEDIUM",
        "rule_ids": ["RULE_A", "RULE_B"],
        "status": "APPROVED",
    }
    assert challenge.token not in repr(event)
    assert approved.one_time_token_hash not in repr(event)
    assert approved.frozen_action_json not in repr(event)


def test_reject_requires_token_and_returns_rejected(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    with pytest.raises(InvalidApprovalToken):
        approval_store.reject(challenge.id, "wrong-token")
    rejected = approval_store.reject(challenge.id, challenge.token)
    assert rejected.status is ApprovalStatus.REJECTED
    assert rejected.decided_at is not None


def test_cancel_needs_no_token_but_only_pending_can_cancel(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    assert approval_store.cancel(challenge.id).status is ApprovalStatus.CANCELLED
    with pytest.raises(InvalidApprovalTransition):
        approval_store.cancel(challenge.id)


@pytest.mark.parametrize(
    ("transition", "expected_status", "expected_event"),
    [
        ("reject", ApprovalStatus.REJECTED, "APPROVAL_REJECTED"),
        ("cancel", ApprovalStatus.CANCELLED, "APPROVAL_CANCELLED"),
    ],
)
def test_reject_and_cancel_append_exact_audit_events(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
    transition: str,
    expected_status: ApprovalStatus,
    expected_event: str,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE_B", "RULE_A"), 300
    )

    if transition == "reject":
        updated = approval_store.reject(challenge.id, challenge.token)
    else:
        updated = approval_store.cancel(challenge.id)

    event = approval_store._audit.list_events("run-1")[-1]
    assert event.event_type == expected_event
    assert event.redacted_payload == {
        "action_hash": updated.action_hash,
        "approval_id": challenge.id,
        "risk_level": "MEDIUM",
        "rule_ids": ["RULE_A", "RULE_B"],
        "status": expected_status.value,
    }
    assert challenge.token not in repr(event)
    assert updated.one_time_token_hash not in repr(event)
    assert updated.frozen_action_json not in repr(event)


def test_expire_pending_uses_aware_utc_cutoff(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    store = ApprovalStateMachine(connection, clock=lambda: now)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 60)
    assert store.expire_pending(now + timedelta(seconds=59)) == ()
    expired = store.expire_pending(now + timedelta(seconds=60))
    assert [item.id for item in expired] == [challenge.id]
    assert expired[0].status is ApprovalStatus.EXPIRED


def test_expire_pending_rejects_naive_cutoff_with_stable_error(
    approval_store: ApprovalStateMachine,
) -> None:
    with pytest.raises(InvalidApprovalTransition) as captured:
        approval_store.expire_pending(datetime(2026, 7, 17, 12, 0))

    assert str(captured.value) == "Approval transition is invalid"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "terminal_status",
    [
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
    ],
)
@pytest.mark.parametrize("transition", ["reject", "cancel"])
def test_reject_and_cancel_reject_every_terminal_state(
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
    terminal_status: ApprovalStatus,
    transition: str,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    if terminal_status is ApprovalStatus.APPROVED:
        approval_store.approve(challenge.id, challenge.token, risky_action)
    elif terminal_status is ApprovalStatus.REJECTED:
        approval_store.reject(challenge.id, challenge.token)
    elif terminal_status is ApprovalStatus.EXPIRED:
        approval_store.expire_pending(challenge.request.expires_at)
    else:
        approval_store.cancel(challenge.id)

    with pytest.raises(InvalidApprovalTransition) as captured:
        if transition == "reject":
            approval_store.reject(challenge.id, "wrong-token")
        else:
            approval_store.cancel(challenge.id)

    assert str(captured.value) == "Approval transition is invalid"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_expire_pending_rolls_back_entire_stable_id_ordered_batch(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    store = ApprovalStateMachine(connection, clock=lambda: now)
    first = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 60)
    second_action = risky_action.model_copy(update={"id": "a2"})
    second = store.request("run-1", second_action, RiskLevel.MEDIUM, ("RULE",), 60)
    ordered_ids = sorted([first.id, second.id])
    connection.execute(
        "CREATE TRIGGER block_second_expiry BEFORE INSERT ON audit_events "
        "WHEN NEW.event_type = 'APPROVAL_EXPIRED' "
        "AND json_extract(NEW.payload, '$.approval_id') = '"
        f"{ordered_ids[1]}' "
        "BEGIN SELECT RAISE(ABORT, 'audit blocked'); END"
    )

    with pytest.raises(ApprovalUnavailable):
        store.expire_pending(now + timedelta(seconds=60))

    assert _approval_states(connection) == sorted(
        [(first.id, "PENDING", None), (second.id, "PENDING", None)]
    )
    assert all(
        event.event_type != "APPROVAL_EXPIRED"
        for event in store._audit.list_events("run-1")
    )
    connection.execute("DROP TRIGGER block_second_expiry")
    expired = store.expire_pending(now + timedelta(seconds=60))
    assert [item.id for item in expired] == ordered_ids


def test_reopen_preserves_approval_and_valid_audit_chain(
    tmp_path: Path,
    risky_action: RunProcessAction,
) -> None:
    database = tmp_path / "approvals.sqlite3"
    first_connection = sqlite3.connect(database)
    first_store = ApprovalStateMachine(first_connection)
    challenge = first_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    first_connection.close()

    second_connection = sqlite3.connect(database)
    second_store = ApprovalStateMachine(second_connection)
    approved = second_store.approve(challenge.id, challenge.token, risky_action)
    assert approved.status is ApprovalStatus.APPROVED
    second_connection.close()

    third_connection = sqlite3.connect(database)
    third_store = ApprovalStateMachine(third_connection)
    assert third_store.get(challenge.id).status is ApprovalStatus.APPROVED
    verification = third_store._audit.verify_chain("run-1")
    assert verification.valid is True
    assert verification.first_invalid_sequence is None
    assert [event.event_type for event in third_store._audit.list_events("run-1")] == [
        "APPROVAL_REQUESTED",
        "APPROVAL_APPROVED",
    ]
    third_connection.close()


@pytest.mark.parametrize("column", ["frozen_action_json", "action_hash"])
def test_tampered_frozen_action_or_hash_fails_closed(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
    column: str,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    tampered = (
        risky_action.model_copy(update={"args": ("push",)}).model_dump_json(
            exclude_none=True
        )
        if column == "frozen_action_json"
        else hashlib.sha256(b"tampered-action").hexdigest()
    )
    connection.execute(
        f"UPDATE approval_requests SET {column} = ? WHERE id = ?",
        (tampered, challenge.id),
    )

    with pytest.raises(ApprovalUnavailable) as captured:
        store.approve(challenge.id, challenge.token, risky_action)

    assert str(captured.value) == "Approval storage is unavailable"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    status = connection.execute(
        "SELECT status FROM approval_requests WHERE id = ?", (challenge.id,)
    ).fetchone()[0]
    assert status != "APPROVED"


def test_approve_expired_request_is_committed_before_approval_error(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    now = [datetime(2026, 7, 17, 12, 0, tzinfo=UTC)]
    store = ApprovalStateMachine(connection, clock=lambda: now[0])
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    now[0] = challenge.request.expires_at

    with pytest.raises(ApprovalExpired) as captured:
        store.approve(challenge.id, challenge.token, risky_action)

    assert str(captured.value) == "Approval request has expired"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    expired = store.get(challenge.id)
    assert expired.status is ApprovalStatus.EXPIRED
    assert expired.decided_at == now[0]
    event = store._audit.list_events("run-1")[-1]
    assert event.event_type == "APPROVAL_EXPIRED"
    assert event.redacted_payload == {
        "action_hash": expired.action_hash,
        "approval_id": challenge.id,
        "risk_level": "MEDIUM",
        "rule_ids": ["RULE"],
        "status": "EXPIRED",
    }


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


@pytest.mark.parametrize(
    ("cleanup_step", "signal"),
    [
        ("rollback", KeyboardInterrupt()),
        ("release", SystemExit()),
    ],
)
def test_cleanup_process_control_wins_over_storage_failure(
    cleanup_step: str,
    signal: BaseException,
    risky_action: RunProcessAction,
) -> None:
    connection, store, challenge = _interruptible_approval(risky_action)
    connection.execute(
        "CREATE TRIGGER reject_approval_audit BEFORE INSERT ON audit_events "
        "WHEN NEW.event_type = 'APPROVAL_APPROVED' "
        "BEGIN SELECT RAISE(ABORT, 'audit blocked'); END"
    )
    if cleanup_step == "rollback":
        connection.rollback_before_signal = signal
        expected_rollback_attempts = 2
        expected_release_attempts = 1
    else:
        connection.release_before_signal = signal
        expected_rollback_attempts = 1
        expected_release_attempts = 2

    with pytest.raises(type(signal)) as captured:
        store.approve(challenge.id, challenge.token, risky_action)

    assert captured.value is signal
    savepoint_name = connection.savepoint_names[0]
    approval_rollbacks = [
        name
        for name in connection.rollback_names
        if name.startswith("safefix_approval_write_")
    ]
    approval_releases = [
        name
        for name in connection.release_names
        if name.startswith("safefix_approval_write_")
    ]
    assert approval_rollbacks == [savepoint_name] * expected_rollback_attempts
    assert approval_releases == [savepoint_name] * expected_release_attempts
    with pytest.raises(sqlite3.OperationalError):
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
    assert store.get(challenge.id).status is ApprovalStatus.PENDING
    assert all(
        event.event_type != "APPROVAL_APPROVED"
        for event in store._audit.list_events("run-1")
    )
    connection.execute("DROP TRIGGER reject_approval_audit")
    assert (
        store.approve(challenge.id, challenge.token, risky_action).status
        is ApprovalStatus.APPROVED
    )
    connection.close()


def test_approve_rolls_back_when_audit_append_fails(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.execute(
        "CREATE TRIGGER reject_approval_audit BEFORE INSERT ON audit_events "
        "WHEN NEW.event_type = 'APPROVAL_APPROVED' "
        "BEGIN SELECT RAISE(ABORT, 'audit blocked'); END"
    )

    with pytest.raises(ApprovalUnavailable):
        store.approve(challenge.id, challenge.token, risky_action)

    assert store.get(challenge.id).status is ApprovalStatus.PENDING


@pytest.mark.parametrize("effect", ["delete", "rewrite"])
def test_request_rejects_audit_trigger_tampering(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
    effect: str,
) -> None:
    store = ApprovalStateMachine(connection)
    trigger_sql = (
        "DELETE FROM audit_events WHERE run_id = NEW.run_id AND sequence = NEW.sequence"
        if effect == "delete"
        else "UPDATE audit_events SET event_type = 'FORGED' "
        "WHERE run_id = NEW.run_id AND sequence = NEW.sequence"
    )
    connection.execute(
        "CREATE TRIGGER alter_approval_audit AFTER INSERT ON audit_events "
        f"WHEN NEW.event_type = 'APPROVAL_REQUESTED' BEGIN {trigger_sql}; END"
    )

    with pytest.raises(ApprovalUnavailable):
        store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)

    assert (
        connection.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0] == 0
    )


def test_approve_rejects_approval_table_after_update_tampering(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.execute(
        """
        CREATE TRIGGER rewrite_approval_decided_at
        AFTER UPDATE ON approval_requests
        WHEN NEW.status = 'APPROVED'
        BEGIN
            UPDATE approval_requests
            SET decided_at = '2026-07-17T00:00:00.000000Z'
            WHERE id = NEW.id;
        END
        """
    )

    with pytest.raises(ApprovalUnavailable):
        store.approve(challenge.id, challenge.token, risky_action)

    assert connection.execute(
        "SELECT status, decided_at FROM approval_requests WHERE id = ?",
        (challenge.id,),
    ).fetchone() == ("PENDING", None)
    assert all(
        event.event_type != "APPROVAL_APPROVED"
        for event in store._audit.list_events("run-1")
    )


def test_same_store_concurrent_approve_has_one_success(
    risky_action: RunProcessAction,
) -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    barrier = threading.Barrier(3)
    results: list[ApprovalRequest | BaseException] = []

    def approve() -> None:
        barrier.wait()
        try:
            results.append(store.approve(challenge.id, challenge.token, risky_action))
        except BaseException as error:
            results.append(error)

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert (
        sum(
            isinstance(result, ApprovalRequest)
            and result.status is ApprovalStatus.APPROVED
            for result in results
        )
        == 1
    )
    assert sum(isinstance(result, ApprovalAlreadyUsed) for result in results) == 1
    assert store.get(challenge.id).status is ApprovalStatus.APPROVED
    connection.close()


def test_two_stores_same_connection_share_write_lock(
    risky_action: RunProcessAction,
) -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    first_store = ApprovalStateMachine(connection)
    second_store = ApprovalStateMachine(connection)
    challenge = first_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    barrier = threading.Barrier(3)
    results: list[ApprovalRequest | BaseException] = []

    def approve(store: ApprovalStateMachine) -> None:
        barrier.wait()
        try:
            results.append(store.approve(challenge.id, challenge.token, risky_action))
        except BaseException as error:
            results.append(error)

    threads = [
        threading.Thread(target=approve, args=(first_store,)),
        threading.Thread(target=approve, args=(second_store,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    successes = [result for result in results if isinstance(result, ApprovalRequest)]
    assert len(successes) == 1
    assert successes[0].status is ApprovalStatus.APPROVED
    assert sum(isinstance(result, ApprovalAlreadyUsed) for result in results) == 1
    assert first_store.get(challenge.id).status is ApprovalStatus.APPROVED
    connection.close()


def test_two_connections_same_file_approve_at_most_once(
    tmp_path: Path,
    risky_action: RunProcessAction,
) -> None:
    database = tmp_path / "concurrent-approvals.sqlite3"
    setup_connection = sqlite3.connect(database)
    setup_store = ApprovalStateMachine(setup_connection)
    challenge = setup_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    setup_connection.close()

    barrier = threading.Barrier(3)
    first_connection = sqlite3.connect(
        database,
        check_same_thread=False,
    )
    second_connection = sqlite3.connect(
        database,
        check_same_thread=False,
    )
    first_store = ApprovalStateMachine(first_connection)
    second_store = ApprovalStateMachine(second_connection)
    results: list[ApprovalRequest | BaseException] = []

    def approve(store: ApprovalStateMachine) -> None:
        barrier.wait()
        try:
            results.append(store.approve(challenge.id, challenge.token, risky_action))
        except BaseException as error:
            results.append(error)

    threads = [
        threading.Thread(target=approve, args=(first_store,)),
        threading.Thread(target=approve, args=(second_store,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    first_connection.close()
    second_connection.close()

    assert sum(isinstance(result, ApprovalRequest) for result in results) == 1
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], (ApprovalAlreadyUsed, ApprovalUnavailable))
    reopened_connection = sqlite3.connect(database)
    reopened_store = ApprovalStateMachine(reopened_connection)
    assert reopened_store.get(challenge.id).status is ApprovalStatus.APPROVED
    assert reopened_store._audit.verify_chain("run-1").valid is True
    assert [
        event.event_type for event in reopened_store._audit.list_events("run-1")
    ] == ["APPROVAL_REQUESTED", "APPROVAL_APPROVED"]
    reopened_connection.close()


def test_audit_failure_preserves_outer_transaction_sentinel(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    connection.execute("CREATE TABLE outer_sentinel (value TEXT NOT NULL)")
    connection.execute("BEGIN")
    connection.execute("INSERT INTO outer_sentinel VALUES ('preserved')")
    connection.execute(
        "CREATE TRIGGER reject_outer_approval_audit BEFORE INSERT ON audit_events "
        "WHEN NEW.event_type = 'APPROVAL_APPROVED' "
        "BEGIN SELECT RAISE(ABORT, 'audit blocked'); END"
    )

    with pytest.raises(ApprovalUnavailable):
        store.approve(challenge.id, challenge.token, risky_action)

    assert connection.in_transaction is True
    assert connection.execute("SELECT value FROM outer_sentinel").fetchall() == [
        ("preserved",)
    ]
    assert store.get(challenge.id).status is ApprovalStatus.PENDING
    connection.commit()
    assert connection.execute("SELECT value FROM outer_sentinel").fetchall() == [
        ("preserved",)
    ]


def test_connection_callback_reentrant_approve_is_rejected(
    risky_action: RunProcessAction,
) -> None:
    connection = sqlite3.connect(
        ":memory:",
        factory=_ReentrantApprovalConnection,
    )
    assert isinstance(connection, _ReentrantApprovalConnection)
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    inner_errors: list[BaseException] = []

    def reenter() -> None:
        try:
            store.approve(challenge.id, challenge.token, risky_action)
        except BaseException as error:
            inner_errors.append(error)

    connection.before_approval_update = reenter
    approved = store.approve(challenge.id, challenge.token, risky_action)

    assert len(inner_errors) == 1
    assert isinstance(inner_errors[0], ApprovalUnavailable)
    assert approved.status is ApprovalStatus.APPROVED
    assert store.get(challenge.id).status is ApprovalStatus.APPROVED
    assert [event.event_type for event in store._audit.list_events("run-1")] == [
        "APPROVAL_REQUESTED",
        "APPROVAL_APPROVED",
    ]
    connection.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("run_id", sqlite3.Binary(b"run-1")),
        ("status", "UNKNOWN"),
        ("risk_level", "LOW"),
        ("rule_ids", '[ "RULE" ]'),
        ("frozen_action_json", '{"type":"run_process"}'),
        ("action_hash", "0" * 64),
        ("created_at", "2026-07-17T12:00:00"),
        ("expires_at", "2026-07-17T11:00:00Z"),
        ("decided_at", "2026-07-17T12:00:00Z"),
    ],
)
def test_tampered_pending_row_fails_closed(
    connection: sqlite3.Connection,
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
    column: str,
    value: object,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    connection.execute(
        f"UPDATE approval_requests SET {column} = ? WHERE id = ?",
        (value, challenge.id),
    )

    with pytest.raises(ApprovalUnavailable):
        approval_store.get(challenge.id)

    row_before = connection.execute(
        "SELECT * FROM approval_requests WHERE id = ?", (challenge.id,)
    ).fetchone()
    audit_before = connection.execute(
        "SELECT * FROM audit_events ORDER BY run_id, sequence"
    ).fetchall()
    for operation in (
        lambda: approval_store.approve(challenge.id, challenge.token, risky_action),
        lambda: approval_store.reject(challenge.id, challenge.token),
        lambda: approval_store.cancel(challenge.id),
        lambda: approval_store.expire_pending(datetime(2100, 1, 1, tzinfo=UTC)),
    ):
        try:
            operation()
        except ApprovalUnavailable:
            pass
        assert (
            connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (challenge.id,)
            ).fetchone()
            == row_before
        )
        assert (
            connection.execute(
                "SELECT * FROM audit_events ORDER BY run_id, sequence"
            ).fetchall()
            == audit_before
        )


def test_terminal_row_without_decided_at_fails_closed(
    connection: sqlite3.Connection,
    approval_store: ApprovalStateMachine,
    risky_action: RunProcessAction,
) -> None:
    challenge = approval_store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
    connection.execute(
        "UPDATE approval_requests SET status = 'APPROVED', decided_at = NULL "
        "WHERE id = ?",
        (challenge.id,),
    )
    row_before = connection.execute(
        "SELECT * FROM approval_requests WHERE id = ?", (challenge.id,)
    ).fetchone()
    audit_before = connection.execute(
        "SELECT * FROM audit_events ORDER BY run_id, sequence"
    ).fetchall()

    with pytest.raises(ApprovalUnavailable):
        approval_store.get(challenge.id)
    with pytest.raises(ApprovalUnavailable):
        approval_store.approve(challenge.id, challenge.token, risky_action)

    assert (
        connection.execute(
            "SELECT * FROM approval_requests WHERE id = ?", (challenge.id,)
        ).fetchone()
        == row_before
    )
    assert (
        connection.execute(
            "SELECT * FROM audit_events ORDER BY run_id, sequence"
        ).fetchall()
        == audit_before
    )


def test_configured_secret_never_leaks_from_approval_boundaries(
    connection: sqlite3.Connection,
) -> None:
    secret = "approval-configured-sentinel-secret"
    store = ApprovalStateMachine(connection, configured_secret_values=(secret,))
    safe_action = RunProcessAction(
        id="safe", reason="safe", program="git", args=("status",)
    )
    safe_challenge = store.request(
        "safe-run", safe_action, RiskLevel.MEDIUM, ("SAFE_RULE",), 300
    )
    secret_action = RunProcessAction(
        id="a-secret",
        reason=f"reason-{secret}",
        program="git",
        args=("commit", secret),
    )

    with pytest.raises(ApprovalUnavailable) as captured:
        store.request(
            f"run-{secret}",
            secret_action,
            RiskLevel.MEDIUM,
            (f"RULE-{secret}",),
            300,
        )

    assert secret not in repr(safe_challenge)
    assert secret not in repr(captured.value)
    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    raw_approval_rows = connection.execute(
        "SELECT * FROM approval_requests ORDER BY id"
    ).fetchall()
    raw_audit_rows = connection.execute(
        "SELECT * FROM audit_events ORDER BY run_id, sequence"
    ).fetchall()
    assert secret not in repr(raw_approval_rows)
    assert secret not in repr(raw_audit_rows)
    assert secret not in "".join(
        traceback.format_exception(
            captured.type,
            captured.value,
            captured.tb,
        )
    )
    captured_traceback = traceback.TracebackException.from_exception(
        captured.value,
        capture_locals=True,
    )
    approval_frames = [
        frame
        for frame in captured_traceback.stack
        if frame.filename.replace("\\", "/").endswith(
            "/safefix/governance/approvals.py"
        )
    ]
    assert approval_frames
    assert all(secret not in repr(frame.locals) for frame in approval_frames)


@pytest.mark.parametrize("token_source", ["wrong", "cross"])
def test_approve_token_error_never_leaks_sensitive_frames(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
    token_source: str,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    if token_source == "cross":
        other_action = risky_action.model_copy(update={"id": "a2"})
        supplied_token = store.request(
            "run-1", other_action, RiskLevel.MEDIUM, ("RULE",), 300
        ).token
    else:
        supplied_token = "wrong-approval-token-sentinel"

    with pytest.raises(InvalidApprovalToken) as captured:
        store.approve(challenge.id, supplied_token, risky_action)

    _assert_sensitive_approval_failure(
        captured,
        (supplied_token,),
        "Approval token is invalid",
    )
    assert store.get(challenge.id).status is ApprovalStatus.PENDING


def test_approve_action_mismatch_never_leaks_sensitive_frames(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    secret = "approval-action-mismatch-sentinel"
    store = ApprovalStateMachine(connection, configured_secret_values=(secret,))
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)
    changed_action = risky_action.model_copy(
        update={"reason": f"reason-{secret}", "args": ("push", secret)}
    )

    with pytest.raises(ActionMismatch) as captured:
        store.approve(challenge.id, challenge.token, changed_action)

    _assert_sensitive_approval_failure(
        captured,
        (secret,),
        "Approval action does not match",
    )
    assert store.get(challenge.id).status is ApprovalStatus.PENDING


def test_reject_wrong_token_never_leaks_sensitive_frames(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    secret = "reject-wrong-token-sentinel"
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300)

    with pytest.raises(InvalidApprovalToken) as captured:
        store.reject(challenge.id, secret)

    _assert_sensitive_approval_failure(
        captured,
        (secret,),
        "Approval token is invalid",
    )
    assert store.get(challenge.id).status is ApprovalStatus.PENDING


def test_request_audit_failure_never_leaks_sensitive_frames(
    connection: sqlite3.Connection,
) -> None:
    input_secret = "request-audit-input-sentinel"
    trigger_secret = "request-audit-trigger-sentinel"
    store = ApprovalStateMachine(connection)
    secret_action = RunProcessAction(
        id="a-secret",
        reason=f"reason-{input_secret}",
        program="git",
        args=("commit", input_secret),
    )
    connection.execute(
        "CREATE TRIGGER reject_sensitive_request_audit "
        "BEFORE INSERT ON audit_events "
        "WHEN NEW.event_type = 'APPROVAL_REQUESTED' "
        f"BEGIN SELECT RAISE(ABORT, '{trigger_secret}'); END"
    )

    with pytest.raises(ApprovalUnavailable) as captured:
        store.request(
            f"run-{input_secret}",
            secret_action,
            RiskLevel.MEDIUM,
            (f"RULE-{input_secret}",),
            300,
        )

    _assert_sensitive_approval_failure(
        captured,
        (input_secret, trigger_secret),
        "Approval storage is unavailable",
    )
    assert connection.execute("SELECT COUNT(*) FROM approval_requests").fetchone() == (
        0,
    )
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (0,)


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
def test_approve_process_control_never_leaks_sensitive_frames_and_recovers(
    risky_action: RunProcessAction,
    signal_type: type[BaseException],
) -> None:
    action_secret = "approval-process-action-sentinel"
    action = risky_action.model_copy(
        update={"reason": f"reason-{action_secret}", "args": ("commit", action_secret)}
    )
    connection = sqlite3.connect(":memory:", factory=_InterruptingApprovalConnection)
    assert isinstance(connection, _InterruptingApprovalConnection)
    store = ApprovalStateMachine(connection)
    challenge = store.request("run-1", action, RiskLevel.MEDIUM, ("RULE",), 300)
    token_secret = challenge.token
    signal = signal_type()
    connection.clear_tracking()
    connection.operation_signal = signal

    with pytest.raises(signal_type) as captured:
        store.approve(challenge.id, token_secret, action)

    assert captured.value is signal
    _assert_sensitive_approval_failure(
        captured,
        (token_secret, action_secret),
    )
    savepoint_name = connection.savepoint_names[0]
    with pytest.raises(sqlite3.OperationalError):
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
    assert store.get(challenge.id).status is ApprovalStatus.PENDING
    assert (
        store.approve(challenge.id, token_secret, action).status
        is ApprovalStatus.APPROVED
    )
    connection.close()


@pytest.mark.parametrize(
    ("method_name", "message"),
    [
        ("get", "Approval request was not found"),
        ("cancel", "Approval request was not found"),
    ],
)
def test_public_id_error_never_leaks_sensitive_frames(
    connection: sqlite3.Connection,
    method_name: str,
    message: str,
) -> None:
    secret = f"{method_name}-approval-id-sentinel"
    store = ApprovalStateMachine(connection)

    with pytest.raises(ApprovalNotFound) as captured:
        getattr(store, method_name)(secret)

    _assert_sensitive_approval_failure(captured, (secret,), message)


def test_public_token_annotations_are_str() -> None:
    assert ApprovalStateMachine.approve.__annotations__["plaintext_token"] == "str"
    assert ApprovalStateMachine.reject.__annotations__["plaintext_token"] == "str"


def _assert_sensitive_approval_failure(
    captured: Any,
    secrets: tuple[str, ...],
    message: str | None = None,
) -> None:
    if message is not None:
        assert str(captured.value) == message
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
    rendered_error = repr(captured.value)
    default_traceback = "".join(
        traceback.format_exception(captured.type, captured.value, captured.tb)
    )
    captured_traceback = traceback.TracebackException.from_exception(
        captured.value,
        capture_locals=True,
    )
    approval_frames = [
        frame
        for frame in captured_traceback.stack
        if frame.filename.replace("\\", "/").endswith(
            "/safefix/governance/approvals.py"
        )
    ]
    assert approval_frames
    for secret in secrets:
        assert secret not in rendered_error
        assert secret not in default_traceback
        assert all(secret not in repr(frame.locals) for frame in approval_frames)


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
            if self.rollback_before_signal is not None and savepoint_name.startswith(
                "safefix_approval_write_"
            ):
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
            savepoint_name = normalized.split()[2]
            self.release_names.append(savepoint_name)
            if self.release_before_signal is not None and savepoint_name.startswith(
                "safefix_approval_write_"
            ):
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


class _ReentrantApprovalConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.before_approval_update: Callable[[], None] | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        normalized = " ".join(sql.split())
        if (
            normalized.startswith(
                "UPDATE approval_requests SET status = ?, decided_at = ?"
            )
            and self.before_approval_update is not None
        ):
            callback = self.before_approval_update
            self.before_approval_update = None
            callback()
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
    ApprovalChallenge,
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
