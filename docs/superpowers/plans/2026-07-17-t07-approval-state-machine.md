# T07 Persistent HITL Approval State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent SQLite approval state machine that binds a one-time capability token to one frozen MEDIUM-risk Action and atomically records every state transition in the T06 audit chain.

**Architecture:** `ApprovalStateMachine` owns one caller-provided `sqlite3.Connection` and constructs its `AuditStore` from that same connection. Every write uses a unique outer SAVEPOINT, a strict postcondition read, and a structured audit append before release; token plaintext is returned once and only its SHA-256 digest is stored. The implementation remains confined to one production module and one real-SQLite unit-test module.

**Tech Stack:** Python 3.12, stdlib `sqlite3` / `secrets` / `hashlib` / `hmac` / `threading`, Pydantic v2 domain models, pytest, Ruff, mypy.

## Global Constraints

- Create only `src/safefix/governance/approvals.py` and `tests/unit/test_approvals.py` as tracked implementation files; do not modify T01–T06 source modules.
- `ApprovalStateMachine` accepts exactly one caller-owned `sqlite3.Connection`, constructs `AuditStore` with that connection, and never closes or globally commits/rolls back it.
- `request(run_id, action, risk_level, rule_ids, ttl_seconds)` accepts only `RiskLevel.MEDIUM`; LOW bypasses HITL and HIGH remains permanently denied by policy.
- Persist only SHA-256 token digests. Compare supplied token digests with `hmac.compare_digest`; plaintext tokens never enter SQLite, audit payloads, exception messages, default repr, reports, or Git history.
- Frozen action JSON is exactly `action.model_dump_json(exclude_none=True)` and `action_hash` is exactly `action_digest(action)`.
- Allowed transitions are only `PENDING -> APPROVED|REJECTED|EXPIRED|CANCELLED`; terminal states never transition.
- Approval writes and their audit events share one logical SQLite operation. Audit failure, audit-chain corruption, trigger tampering, postcondition failure, or storage failure must leave no successful state transition.
- Stable public errors contain no approval ID, run ID, token, token digest, frozen JSON, SQL, configured secret, or underlying exception; `__cause__` and `__context__` are `None`.
- `KeyboardInterrupt` and `SystemExit` are never swallowed or converted; SAVEPOINT and thread-local guard cleanup runs before the original object propagates.
- All stored datetimes use aware UTC externally and canonical microsecond UTC `Z` text internally.
- All new tests use real SQLite behavior. Mocks may not replace persistence, conditional updates, SAVEPOINTs, triggers, audit writes, or concurrency.
- Use `C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe`; set `PYTHONPATH` to the active worktree's `src` before pytest and mypy.
- Preserve the three untracked course documents in the main checkout and never stage them.

---

### Task 1: Persistent Request, Challenge, Schema, and Strict Read

**Files:**
- Create: `src/safefix/governance/approvals.py`
- Create: `tests/unit/test_approvals.py`

**Interfaces:**
- Consumes: `Action`, `ApprovalRequest`, `ApprovalStatus`, `RiskLevel`, `action_digest`, and `AuditStore`.
- Produces: frozen `ApprovalChallenge(id, token, request)`; stable approval error classes; `request(run_id, action, risk_level, rule_ids, ttl_seconds) -> ApprovalChallenge`; `get(approval_id) -> ApprovalRequest`; the minimal frozen-action `approve(approval_id, plaintext_token, action) -> ApprovalRequest` needed by the two plan-locked tests.

- [ ] **Step 1: Add the plan-locked replacement and replay tests before production code**

Create `tests/unit/test_approvals.py` with these first two tests and fixtures:

```python
import sqlite3
from collections.abc import Iterator

import pytest

from safefix.domain import RiskLevel, RunProcessAction
from safefix.governance.approvals import (
    ActionMismatch,
    ApprovalAlreadyUsed,
    ApprovalStateMachine,
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
```

- [ ] **Step 2: Run the first RED exactly once and save its output in the ignored task report**

Run:

```powershell
$python = 'C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe'
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $python -m pytest tests/unit/test_approvals.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'safefix.governance.approvals'`.

- [ ] **Step 3: Add request-focused tests**

Extend test imports with `import hashlib`, `from datetime import timedelta`, `ApprovalStatus`, and `InvalidApprovalTransition`.

Add exact tests that assert:

```python
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
```

Add this round-trip and minimal-audit test:

```python
def test_request_round_trip_and_audit_payload_are_exact(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE_B", "RULE_A"), 300
    )
    assert store.get(challenge.id) == challenge.request
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
```

The test may access the internal audit instance only in T07 unit coverage; production consumers use public APIs.

- [ ] **Step 4: Implement the public types, schema, request, and strict decoder**

Implement these locked public definitions:

```python
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
```

Implement `ApprovalStateMachine.__init__` so it:

```python
def __init__(
    self,
    connection: sqlite3.Connection,
    *,
    configured_secret_values: Iterable[str] = (),
    clock: Callable[[], datetime] = _utc_now,
) -> None:
    self._connection = connection
    self._clock = clock
    self._secrets = tuple(sorted({value for value in configured_secret_values if value}))
    self._lock = _CONNECTION_LOCKS[id(connection) % len(_CONNECTION_LOCKS)]
    self._audit = AuditStore(
        connection,
        configured_secret_values=self._secrets,
    )
    self._initialize_schema()
```

Use the exact table and index from the design spec. Implement canonical UTC formatting/parsing, sorted compact rule-ID JSON, `_StoredApproval`, `_read_one()`, `_decode_row()`, `_to_request()`, `_contains_secret()`, and stable error mapping. `_decode_row()` must parse frozen JSON with `TypeAdapter(Action)`, require reserialization equality, require `action_digest(parsed_action) == action_hash`, and enforce pending/terminal `decided_at` invariants.

Implement request as one guarded SAVEPOINT operation:

```python
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
```

`_insert_request()` inserts PENDING, appends `APPROVAL_REQUESTED`, strictly reloads the row, compares every stored field with the prepared candidate, and only then returns the challenge. Audit payload is exactly `approval_id`, `status`, `action_hash`, `risk_level`, and `rule_ids`.

Implement a minimal `approve()` in Task 1 so the plan-locked replacement and replay tests finish green. It must strictly load PENDING, compare the token digest with `hmac.compare_digest`, compare both digest and exact frozen JSON, conditionally update to APPROVED, append `APPROVAL_APPROVED`, reload the row, and return it. Task 2 adds wrong-token preservation, automatic expiry, cross-request tokens, tampered-row handling, and exact error boundaries.

- [ ] **Step 5: Run Task 1 GREEN and the full existing suite**

Run:

```powershell
& $python -m pytest tests/unit/test_approvals.py -k 'request or changed_action or single_use' -v
& $python -m pytest -q
& $python -m ruff check src/safefix/governance/approvals.py tests/unit/test_approvals.py
& $python -m mypy src/safefix/governance/approvals.py tests/unit/test_approvals.py
```

Expected: focused tests pass, the full suite has zero failures, Ruff reports `All checks passed!`, and mypy reports no issues.

- [ ] **Step 6: Commit Task 1**

```powershell
git add -- src/safefix/governance/approvals.py tests/unit/test_approvals.py
git diff --cached --check
git commit -m "feat(approval): 添加持久化审批请求"
```

---

### Task 2: Harden Frozen-Action Single-Use Approval

**Files:**
- Modify: `src/safefix/governance/approvals.py`
- Modify: `tests/unit/test_approvals.py`

**Interfaces:**
- Consumes: Task 1 `ApprovalStateMachine`, strict stored record, token digest, and audit helper.
- Produces: hardened `approve(id, plaintext_token, action) -> ApprovalRequest` with expiry, cross-request token, tamper, exact postcondition, and stable-error guarantees.

- [ ] **Step 1: Expand approval RED coverage**

Extend test imports with `ApprovalNotFound`, `ApprovalStatus`, `ApprovalUnavailable`, and `InvalidApprovalToken`.

Add tests with these exact outcomes:

```python
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
    assert approval_store.approve(
        challenge.id, challenge.token, risky_action
    ).status is ApprovalStatus.APPROVED


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
```

Add the following named cases with the stated exact outcome:

| Test | Setup | Exact outcome |
|---|---|---|
| `test_approve_missing_id_is_stable_not_found` | call approve with a syntactically valid unknown ID | `ApprovalNotFound`, fixed message, no cause/context |
| `test_token_from_another_request_is_rejected` | create two requests and swap their tokens | `InvalidApprovalToken`; both remain PENDING |
| `test_approved_request_replay_is_already_used` | approve once, then repeat exact call | `ApprovalAlreadyUsed`; one APPROVAL_APPROVED audit event |
| `test_approve_sets_exact_decided_at` | inject a fixed aware UTC clock | returned and stored `decided_at` equal the fixed instant |
| `test_approved_audit_payload_excludes_capability_and_action` | approve normally | audit payload equals the five safe fields and contains neither token/hash/frozen JSON |
| `test_tampered_frozen_action_or_hash_fails_closed` | mutate each column directly before approve | `ApprovalUnavailable`; row is not APPROVED |

- [ ] **Step 2: Run Task 2 RED**

Run:

```powershell
& $python -m pytest tests/unit/test_approvals.py -k 'approve or token or mismatch or replay' -v
```

Expected: new approval hardening tests fail because automatic expiry, cross-request handling, tamper detection, or exact error/postcondition behavior is incomplete; all Task 1 tests remain green.

- [ ] **Step 3: Implement token verification and conditional approval**

Implement these helpers with strict ordering:

```python
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
```

Implement `approve()` so its SAVEPOINT operation performs: strict load; terminal-state rejection; automatic expiry when `now >= expires_at`; token verification; action verification; `UPDATE ... WHERE id = ? AND status = 'PENDING'`; `rowcount == 1`; `APPROVAL_APPROVED` audit append; strict postcondition reload and full comparison.

For an expired request, commit the EXPIRED transition and its audit event first, then raise `ApprovalExpired` outside the transaction so the expiry is not rolled back.

- [ ] **Step 4: Run approval GREEN and regression gates**

```powershell
& $python -m pytest tests/unit/test_approvals.py -k 'approve or token or mismatch or replay or expired' -v
& $python -m pytest tests/unit/test_audit.py tests/unit/test_approvals.py -q
& $python -m ruff check src tests/unit/test_approvals.py
& $python -m mypy src tests/unit/test_approvals.py
```

Expected: all approval-focused tests pass; audit and approval tests have zero failures; Ruff and mypy are clean.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- src/safefix/governance/approvals.py tests/unit/test_approvals.py
git diff --cached --check
git commit -m "feat(approval): 实现冻结动作单次批准"
```

---

### Task 3: Reject, Cancel, Expire, and Reopen Persistence

**Files:**
- Modify: `src/safefix/governance/approvals.py`
- Modify: `tests/unit/test_approvals.py`

**Interfaces:**
- Consumes: Task 2 conditional transition and audit transaction helpers.
- Produces: `reject`, `cancel`, `expire_pending`, and reopen-safe transition behavior.

- [ ] **Step 1: Add RED tests for every remaining terminal transition**

Extend test imports with `from datetime import UTC, datetime, timedelta`.

Add these exact tests plus parametrized terminal-state rejection:

```python
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


def test_expire_pending_uses_aware_utc_cutoff(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    store = ApprovalStateMachine(connection, clock=lambda: now)
    challenge = store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 60
    )
    assert store.expire_pending(now + timedelta(seconds=59)) == ()
    expired = store.expire_pending(now + timedelta(seconds=60))
    assert [item.id for item in expired] == [challenge.id]
    assert expired[0].status is ApprovalStatus.EXPIRED
```

Add persistence test using a temporary file: request, close connection, reopen a new connection/state machine, approve with the original ID/token/action, and verify both APPROVED state and valid audit chain survive another reopen.

- [ ] **Step 2: Run transition RED**

```powershell
& $python -m pytest tests/unit/test_approvals.py -k 'reject or cancel or expire or reopen' -v
```

Expected: new tests fail because `reject`, `cancel`, or `expire_pending` are missing.

- [ ] **Step 3: Implement the remaining transitions through one shared helper**

Add one `_transition_pending(stored, target, decided_at, event_type)` implementation that:

```python
cursor = self._connection.execute(
    "UPDATE approval_requests SET status = ?, decided_at = ? "
    "WHERE id = ? AND status = ?",
    (
        target.value,
        self._format_timestamp(decided_at),
        stored.request.id,
        ApprovalStatus.PENDING.value,
    ),
)
if cursor.rowcount != 1:
    self._raise_transition_conflict()
self._append_audit(event_type, stored, target)
updated = self._read_one(stored.request.id)
self._require_transition_postcondition(stored, updated, target, decided_at)
return updated.request
```

`reject()` verifies the token before calling the helper with event type `APPROVAL_REJECTED`. `cancel()` skips token verification but still requires PENDING and uses `APPROVAL_CANCELLED`. `expire_pending(now)` rejects naive datetimes, normalizes aware values to UTC, selects due PENDING IDs in stable ID order, and transitions the entire batch inside one `_run_write()` call using `APPROVAL_EXPIRED` for every row.

- [ ] **Step 4: Run transition and persistence GREEN**

```powershell
& $python -m pytest tests/unit/test_approvals.py -k 'reject or cancel or expire or reopen or terminal' -v
& $python -m pytest tests/unit/test_approvals.py -q
& $python -m ruff check src tests/unit/test_approvals.py
& $python -m ruff format --check src tests/unit/test_approvals.py
& $python -m mypy src tests/unit/test_approvals.py
```

Expected: all approval tests pass and static gates are clean.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- src/safefix/governance/approvals.py tests/unit/test_approvals.py
git diff --cached --check
git commit -m "feat(approval): 完善审批终态与持久化"
```

---

### Task 4: Audit Atomicity, Concurrency, Tamper Resistance, and Exception Safety

**Files:**
- Modify: `src/safefix/governance/approvals.py`
- Modify: `tests/unit/test_approvals.py`

**Interfaces:**
- Consumes: all Task 1–3 APIs.
- Produces: production-ready fail-closed behavior under real SQLite triggers, concurrent connections, malicious rows, nested transactions, reentry, and process-control exceptions.

- [ ] **Step 1: Add deterministic audit-atomicity RED tests**

Extend test imports with `threading`, `traceback`, and the concrete approval errors used below.

Use real triggers and assert exact rollback behavior:

```python
def test_approve_rolls_back_when_audit_append_fails(
    connection: sqlite3.Connection,
    risky_action: RunProcessAction,
) -> None:
    store = ApprovalStateMachine(connection)
    challenge = store.request(
        "run-1", risky_action, RiskLevel.MEDIUM, ("RULE",), 300
    )
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
    assert connection.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0] == 0
```

Add `test_approve_rejects_approval_table_after_update_tampering`: an `AFTER UPDATE` trigger rewrites `decided_at` to a fixed old timestamp; `approve()` raises `ApprovalUnavailable`, the row rolls back to PENDING with `decided_at IS NULL`, and no `APPROVAL_APPROVED` event remains.

- [ ] **Step 2: Add concurrency, outer-transaction, reentry, and interruption RED tests**

Add these exact deterministic tests:

| Test | Required synchronization and assertion |
|---|---|
| `test_same_store_concurrent_approve_has_one_success` | one `check_same_thread=False` connection, one store, `Barrier(3)`; one APPROVED return, one `ApprovalAlreadyUsed`, persisted APPROVED |
| `test_two_stores_same_connection_share_write_lock` | same connection and token, two stores, `Barrier(3)`; one success, no successful result disappears |
| `test_two_connections_same_file_approve_at_most_once` | two connections to one temp DB, barrier before conditional update; exactly one success, other is `ApprovalAlreadyUsed` or `ApprovalUnavailable`, reopened state APPROVED, audit chain valid |
| `test_audit_failure_preserves_outer_transaction_sentinel` | explicit outer transaction and sentinel row before approve; failed approve keeps sentinel and PENDING, outer transaction remains active and commits sentinel |
| `test_connection_callback_reentrant_approve_is_rejected` | Connection subclass calls inner approve before outer UPDATE; inner gets `ApprovalUnavailable`, outer success remains persisted and audited |
| `test_process_control_exception_cleans_savepoint_and_recovers` | parametrize the identical `KeyboardInterrupt` and `SystemExit` objects; object identity preserved, no internal savepoint remains, later approve succeeds |

Use `threading.Barrier` or `threading.Event`, never sleeps, to control interleavings.

- [ ] **Step 3: Add tamper and non-disclosure RED tests**

Use this fixed tamper matrix; after each mutation `get()` raises `ApprovalUnavailable`, and the write methods must not create a new terminal transition or audit event:

```python
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
```

Add a separate terminal-row case that sets APPROVED with `decided_at = NULL`; it must fail closed.

Use a configured sentinel secret in run ID, action args, action reason, and rule ID. Assert it is absent from raw SQLite rows, audit rows, `repr(ApprovalChallenge)`, stable errors, default traceback, and every approvals.py frame under `traceback.TracebackException(capture_locals=True)`.

- [ ] **Step 4: Harden locking, SAVEPOINT lifecycle, postconditions, and error boundaries**

Implement module-level striped RLocks, a monotonically unique SAVEPOINT counter, and thread-local active connection IDs. `_run_write()` must have this structure:

```python
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
```

`_run_savepoint()` must clean up on every `BaseException`, rethrow process-control exceptions unchanged, preserve declared `ApprovalError` instances, and convert all other `Exception` instances only after leaving the exception context so cause/context remain empty.

Before SAVEPOINT release, request and transition operations must strictly reload the approval row and rely on T06 `AuditStore.append()` postconditions. Never infer success from `cursor.rowcount` alone.

- [ ] **Step 5: Run security GREEN, then every final gate from scratch**

```powershell
& $python -m pytest tests/unit/test_approvals.py -q
& $python -m pytest -q
& $python -m ruff check src tests
& $python -m ruff format --check src tests
& $python -m mypy src tests/unit/test_approvals.py
& $python -m pip check
git diff --check
git status --short
```

Expected: approval tests and the full suite have zero failures; Ruff, format, mypy, pip check, and diff check are clean; only the two planned implementation files differ from the task base, plus the already committed design/plan documents.

- [ ] **Step 6: Commit security hardening**

```powershell
git add -- src/safefix/governance/approvals.py tests/unit/test_approvals.py
git diff --cached --check
git commit -m "fix(approval): 强化审批事务与并发边界"
```

---

### Task 5: Independent Review and Delivery Gate

**Files:**
- Verify: `src/safefix/governance/approvals.py`
- Verify: `tests/unit/test_approvals.py`
- Update after approval: `PLAN.md`
- Update after approval: `AGENT_LOG.md`

**Interfaces:**
- Consumes: the complete T07 branch.
- Produces: frozen review packages, approved review verdicts, fresh root verification, and process evidence ready for a GitLab MR.

- [ ] **Step 1: Freeze the complete implementation diff**

Generate a review package from the T07 implementation base to HEAD with the `subagent-driven-development/scripts/review-package` helper. Record base SHA, head SHA, commit list, stat, and full diff in `.superpowers/sdd/`; never ask reviewers to inspect a moving working tree.

- [ ] **Step 2: Run independent specification and security-quality reviews**

Dispatch two read-only reviewers in parallel. The specification reviewer checks exact API, state transitions, token/frozen-action contracts, persistence, expiry, and audit atomicity. The security-quality reviewer checks token/secret disclosure, SQLite dynamic types, trigger tampering, concurrency, reentry, SAVEPOINT cleanup, exception chains, and test determinism. Reviewers do not rerun the full suite.

Expected: every Critical and Important finding is either reproduced and fixed with RED/GREEN evidence or rejected with concrete technical evidence; re-review reports no remaining Critical/Important.

- [ ] **Step 3: Run one whole-branch review**

Freeze a fresh complete `BASE..HEAD` package and dispatch a senior whole-branch reviewer. Check T07 integration with T01 domain and T06 audit, future T12/T16 consumers, transaction ownership, production readiness, and deferred Minor risks.

- [ ] **Step 4: Perform root-agent verification**

Run the Task 4 final gate commands again in the root agent after all fixes. Inspect `git diff --name-status BASE..HEAD`, `git log BASE..HEAD`, and working-tree status. Do not claim completion from implementer or reviewer reports.

- [ ] **Step 5: Update process evidence and commit**

Mark T07 PLAN checkboxes complete, add actual implementation/fix/review evidence to `AGENT_LOG.md`, and update the Task Status Ledger to `Ready for MR`. Commit only those process documents:

```powershell
git add -- PLAN.md AGENT_LOG.md
git diff --cached --check
git commit -m "docs(process): 记录 T07 实现与审查"
```

- [ ] **Step 6: Invoke finishing-a-development-branch**

After fresh verification, present the four standard integration options. Recommend option 2: push `codex/t07-approval-state-machine` and create a GitLab merge request targeting `main`. Preserve the worktree until the MR is merged and reverified.
