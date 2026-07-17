import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import timedelta

import pytest

from safefix.domain import ApprovalStatus, RiskLevel, RunProcessAction
from safefix.governance.approvals import (
    ActionMismatch,
    ApprovalAlreadyUsed,
    ApprovalStateMachine,
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
    changed = RunProcessAction(
        id="a1", reason="commit", program="git", args=("push",)
    )
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
