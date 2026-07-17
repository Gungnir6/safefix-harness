import hashlib
import json
import sqlite3
import threading
from dataclasses import FrozenInstanceError
from datetime import UTC, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from safefix.governance.audit import AuditEvent, AuditStore, AuditUnavailable


def test_audit_chain_detects_modified_payload() -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    store.append("run-1", "ACTION", {"token": "sk-SECRET", "value": 1})
    store.append("run-1", "DECISION", {"outcome": "DENY"})
    connection.execute(
        "UPDATE audit_events SET payload = ? WHERE sequence = 1", ('{"value":9}',)
    )
    result = store.verify_chain("run-1")
    assert result.valid is False
    assert result.first_invalid_sequence == 1


def test_payload_is_recursively_redacted_before_it_leaves_the_store() -> None:
    connection = sqlite3.connect(":memory:")
    configured_secret = "FAKE-CONFIGURED-CREDENTIAL"
    store = AuditStore(connection, configured_secret_values=[configured_secret])

    event = store.append(
        "run-redaction",
        "ACTION",
        {
            "ApiKeY": "fake-key-value",
            "ToKeN": "fake-token-value",
            "clientSECRET": "fake-secret-value",
            "nested": [
                {
                    "Password": "fake-password-value",
                    "safe": f"Bearer {configured_secret}",
                    f"header-{configured_secret}": "visible",
                },
                {"enabled": True, "count": 3, "nothing": None},
            ],
            "authorization": "fake-authorization-value",
            "exact": configured_secret,
        },
    )

    expected = {
        "ApiKeY": "[REDACTED]",
        "ToKeN": "[REDACTED]",
        "authorization": "[REDACTED]",
        "clientSECRET": "[REDACTED]",
        "exact": "[REDACTED]",
        "nested": [
            {
                "Password": "[REDACTED]",
                "[REDACTED]": "visible",
                "safe": "[REDACTED]",
            },
            {"count": 3, "enabled": True, "nothing": None},
        ],
    }
    assert event.redacted_payload == expected
    assert store.list_events("run-redaction")[0].redacted_payload == expected
    stored_row = connection.execute(
        "SELECT * FROM audit_events WHERE run_id = ?", ("run-redaction",)
    ).fetchone()
    stored_payload = cast(str, stored_row[3])
    assert stored_payload == json.dumps(expected, sort_keys=True, separators=(",", ":"))
    assert cast(str, stored_row[6]).endswith("Z")
    assert event.event_hash == _event_hash(
        event.run_id,
        event.sequence,
        event.event_type,
        stored_payload,
        cast(str, stored_row[6]),
        event.previous_hash,
    )
    assert configured_secret not in repr(stored_row)
    assert configured_secret not in event.event_hash
    assert configured_secret not in event.previous_hash
    assert configured_secret not in event.event_type
    assert configured_secret not in event.run_id
    assert configured_secret not in repr(event.redacted_payload)
    assert configured_secret not in repr(
        store.list_events("run-redaction")[0].redacted_payload
    )
    assert configured_secret not in repr(event)
    assert configured_secret not in repr(store.list_events("run-redaction"))


def test_redacted_mapping_key_collisions_are_deterministic() -> None:
    first_secret = "FAKE-COLLISION-A"
    second_secret = "FAKE-COLLISION-B"
    first_payload = {
        f"x-{second_secret}": 2,
        "[REDACTED]": 0,
        f"x-{first_secret}": 1,
    }
    second_payload = dict(reversed(list(first_payload.items())))

    first_store = AuditStore(
        sqlite3.connect(":memory:"),
        configured_secret_values=[first_secret, second_secret],
    )
    second_store = AuditStore(
        sqlite3.connect(":memory:"),
        configured_secret_values=[first_secret, second_secret],
    )

    first = first_store.append("run", "ACTION", first_payload).redacted_payload
    second = second_store.append("run", "ACTION", second_payload).redacted_payload
    assert first == second
    assert first == {"[REDACTED]": 0, "[REDACTED]#2": 1, "[REDACTED]#3": 2}


def test_runs_have_independent_ordered_chains_and_frozen_utc_events() -> None:
    store = AuditStore(sqlite3.connect(":memory:"))

    run_a_first = store.append("run-a", "FIRST", {"value": 1})
    run_b_first = store.append("run-b", "FIRST", {"value": 2})
    run_a_second = store.append("run-a", "SECOND", {"value": 3})

    assert [event.sequence for event in store.list_events("run-a")] == [1, 2]
    assert [event.sequence for event in store.list_events("run-b")] == [1]
    assert run_a_first.previous_hash == ""
    assert run_b_first.previous_hash == ""
    assert run_a_second.previous_hash == run_a_first.event_hash
    assert run_a_first.created_at.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        cast(Any, run_a_first).sequence = 99
    with pytest.raises(FrozenInstanceError):
        cast(Any, store.verify_chain("missing")).valid = False
    assert store.verify_chain("missing").valid is True
    assert store.verify_chain("missing").first_invalid_sequence is None


def test_zero_argument_connection_factory_is_supported() -> None:
    connection = sqlite3.connect(":memory:")
    calls = 0

    def factory() -> sqlite3.Connection:
        nonlocal calls
        calls += 1
        return connection

    store = AuditStore(factory)
    store.append("run", "ACTION", {"value": 1})

    assert calls == 1
    assert [event.sequence for event in store.list_events("run")] == [1]
    connection.execute("SELECT 1")


def test_existing_connection_with_row_factory_is_supported() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row

    store = AuditStore(connection)

    assert store.append("run", "ACTION", {"value": 1}).sequence == 1
    assert store.verify_chain("run").valid is True


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {1: "non-string key"},
        {"value": object()},
    ],
    ids=["nan", "infinity", "non-string-key", "not-json-serializable"],
)
def test_invalid_json_payload_fails_closed_without_partial_rows(
    invalid_payload: object,
) -> None:
    connection = sqlite3.connect(":memory:")
    configured_secret = "FAKE-SERIALIZATION-CREDENTIAL"
    store = AuditStore(connection, configured_secret_values=[configured_secret])

    with pytest.raises(AuditUnavailable) as captured:
        store.append(
            "run",
            "ACTION",
            {"payload": invalid_payload, "safe": f"prefix-{configured_secret}"},
        )

    assert str(captured.value) == "Audit storage is unavailable"
    assert configured_secret not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert store.list_events("run") == []
    assert store.append("run", "RECOVERY", {"ok": True}).sequence == 1


def test_append_transaction_failure_rolls_back_and_recovers_sequence() -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    store.append("run", "FIRST", {"value": 1})
    connection.execute(
        """
        CREATE TRIGGER reject_audit_insert
        BEFORE INSERT ON audit_events
        WHEN NEW.event_type = 'FAIL'
        BEGIN
            SELECT RAISE(ABORT, 'raw sqlite failure detail');
        END
        """
    )

    with pytest.raises(AuditUnavailable) as captured:
        store.append("run", "FAIL", {"password": "FAKE-TRANSACTION-CREDENTIAL"})

    assert str(captured.value) == "Audit storage is unavailable"
    assert "sqlite" not in str(captured.value).casefold()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert [event.sequence for event in store.list_events("run")] == [1]
    connection.execute("DROP TRIGGER reject_audit_insert")
    assert store.append("run", "RECOVERY", {"value": 2}).sequence == 2


def test_connection_and_schema_failures_are_stable_and_fail_closed() -> None:
    configured_secret = "FAKE-CONNECTION-CREDENTIAL"

    def failing_factory() -> sqlite3.Connection:
        raise RuntimeError(f"driver exposed {configured_secret}")

    with pytest.raises(AuditUnavailable) as factory_failure:
        AuditStore(failing_factory, configured_secret_values=[configured_secret])
    assert str(factory_failure.value) == "Audit storage is unavailable"
    assert configured_secret not in repr(factory_failure.value)
    assert factory_failure.value.__cause__ is None
    assert factory_failure.value.__context__ is None

    invalid_schema = sqlite3.connect(":memory:")
    invalid_schema.execute("CREATE VIEW audit_events AS SELECT 1 AS wrong_column")
    with pytest.raises(AuditUnavailable) as schema_failure:
        AuditStore(invalid_schema)
    assert str(schema_failure.value) == "Audit storage is unavailable"
    assert schema_failure.value.__cause__ is None
    assert schema_failure.value.__context__ is None

    closed = sqlite3.connect(":memory:")
    closed.close()
    with pytest.raises(AuditUnavailable) as connection_failure:
        AuditStore(closed)
    assert str(connection_failure.value) == "Audit storage is unavailable"
    assert connection_failure.value.__cause__ is None
    assert connection_failure.value.__context__ is None


@pytest.mark.parametrize("operation", ["append", "list", "verify"])
def test_operations_fail_closed_after_connection_is_closed(operation: str) -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    connection.close()

    with pytest.raises(AuditUnavailable) as captured:
        if operation == "append":
            store.append("run", "ACTION", {"value": 1})
        elif operation == "list":
            store.list_events("run")
        else:
            store.verify_chain("run")

    assert str(captured.value) == "Audit storage is unavailable"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    ("column", "target_sequence", "tampered_value", "invalid_sequence"),
    [
        ("event_type", 1, "TAMPERED", 1),
        ("previous_hash", 2, "broken-link", 2),
        ("event_hash", 1, "0" * 64, 1),
        ("created_at", 2, "2000-01-01T00:00:00+00:00", 2),
        ("sequence", 2, 5, 2),
    ],
)
def test_chain_detects_each_tampered_chain_field(
    column: str,
    target_sequence: int,
    tampered_value: object,
    invalid_sequence: int,
) -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    store.append("run", "FIRST", {"value": 1})
    store.append("run", "SECOND", {"value": 2})

    connection.execute(
        f"UPDATE audit_events SET {column} = ? WHERE run_id = ? AND sequence = ?",
        (tampered_value, "run", target_sequence),
    )

    result = store.verify_chain("run")
    assert result.valid is False
    assert result.first_invalid_sequence == invalid_sequence


@pytest.mark.parametrize(
    "tampered_payload",
    ['{"unterminated":', '{ "value": 1 }', "NaN"],
    ids=["malformed", "non-canonical", "non-finite"],
)
def test_chain_rejects_invalid_stored_payload_even_with_matching_hash(
    tampered_payload: str,
) -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    event = store.append("run", "ACTION", {"value": 1})
    created_at = cast(
        str,
        connection.execute(
            "SELECT created_at FROM audit_events WHERE run_id = ?", ("run",)
        ).fetchone()[0],
    )
    forged_hash = _event_hash(
        "run", 1, "ACTION", tampered_payload, created_at, event.previous_hash
    )
    connection.execute(
        "UPDATE audit_events SET payload = ?, event_hash = ? WHERE run_id = ?",
        (tampered_payload, forged_hash, "run"),
    )

    result = store.verify_chain("run")
    assert result.valid is False
    assert result.first_invalid_sequence == 1
    with pytest.raises(AuditUnavailable) as captured:
        store.list_events("run")
    assert str(captured.value) == "Audit storage is unavailable"


def test_chain_rejects_non_utc_timestamp_even_with_matching_hash() -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    event = store.append("run", "ACTION", {"value": 1})
    payload = cast(
        str,
        connection.execute(
            "SELECT payload FROM audit_events WHERE run_id = ?", ("run",)
        ).fetchone()[0],
    )
    non_utc_timestamp = event.created_at.astimezone(
        timezone(timedelta(hours=1))
    ).isoformat()
    forged_hash = _event_hash(
        "run", 1, "ACTION", payload, non_utc_timestamp, event.previous_hash
    )
    connection.execute(
        "UPDATE audit_events SET created_at = ?, event_hash = ? WHERE run_id = ?",
        (non_utc_timestamp, forged_hash, "run"),
    )

    result = store.verify_chain("run")
    assert result.valid is False
    assert result.first_invalid_sequence == 1


@pytest.mark.parametrize("injection_kind", ["value", "sensitive-key", "mapping-key"])
def test_tampered_unredacted_payload_is_never_returned(injection_kind: str) -> None:
    connection = sqlite3.connect(":memory:")
    configured_secret = "FAKE-INJECTED-CREDENTIAL"
    store = AuditStore(connection, configured_secret_values=[configured_secret])
    event = store.append("run", "ACTION", {"value": 1})
    if injection_kind == "value":
        injected = {"safe": f"prefix-{configured_secret}"}
    elif injection_kind == "sensitive-key":
        injected = {"PaSsWoRd": "fake-injected-password"}
    else:
        injected = {f"header-{configured_secret}": "visible"}
    tampered_payload = json.dumps(injected, sort_keys=True, separators=(",", ":"))
    created_at = cast(
        str,
        connection.execute(
            "SELECT created_at FROM audit_events WHERE run_id = ?", ("run",)
        ).fetchone()[0],
    )
    forged_hash = _event_hash(
        "run", 1, "ACTION", tampered_payload, created_at, event.previous_hash
    )
    connection.execute(
        "UPDATE audit_events SET payload = ?, event_hash = ? WHERE run_id = ?",
        (tampered_payload, forged_hash, "run"),
    )

    result = store.verify_chain("run")
    assert result.valid is False
    assert result.first_invalid_sequence == 1
    with pytest.raises(AuditUnavailable) as captured:
        store.list_events("run")
    assert configured_secret not in repr(captured.value)


@pytest.mark.parametrize("signal", [KeyboardInterrupt(), SystemExit()])
def test_process_control_exceptions_are_not_swallowed(signal: BaseException) -> None:
    def factory() -> sqlite3.Connection:
        raise signal

    with pytest.raises(type(signal)):
        AuditStore(factory)


@pytest.mark.parametrize(
    ("field", "value_kind"),
    [
        ("run_id", "exact"),
        ("run_id", "substring"),
        ("event_type", "exact"),
        ("event_type", "substring"),
        ("run_id", "non-string"),
        ("event_type", "non-string"),
    ],
)
def test_append_rejects_unsafe_metadata_without_persisting(
    field: str, value_kind: str
) -> None:
    connection = sqlite3.connect(":memory:")
    configured_secret = "FAKE-METADATA-CREDENTIAL"
    store = AuditStore(connection, configured_secret_values=[configured_secret])
    unsafe: object
    if value_kind == "exact":
        unsafe = configured_secret
    elif value_kind == "substring":
        unsafe = f"prefix-{configured_secret}-suffix"
    else:
        unsafe = 17
    run_id: object = unsafe if field == "run_id" else "run"
    event_type: object = unsafe if field == "event_type" else "ACTION"

    with pytest.raises(AuditUnavailable) as captured:
        store.append(cast(Any, run_id), cast(Any, event_type), {"value": 1})

    assert str(captured.value) == "Audit storage is unavailable"
    assert configured_secret not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


@pytest.mark.parametrize("operation", ["list", "verify"])
@pytest.mark.parametrize("value_kind", ["exact", "substring", "non-string"])
def test_reads_reject_unsafe_run_id(operation: str, value_kind: str) -> None:
    connection = sqlite3.connect(":memory:")
    configured_secret = "FAKE-READ-METADATA-CREDENTIAL"
    store = AuditStore(connection, configured_secret_values=[configured_secret])
    if value_kind == "exact":
        unsafe: object = configured_secret
    elif value_kind == "substring":
        unsafe = f"prefix-{configured_secret}-suffix"
    else:
        unsafe = 23

    with pytest.raises(AuditUnavailable) as captured:
        if operation == "list":
            store.list_events(cast(Any, unsafe))
        else:
            store.verify_chain(cast(Any, unsafe))

    assert configured_secret not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("value_kind", ["exact", "substring"])
def test_injected_secret_event_type_is_never_returned(value_kind: str) -> None:
    connection = sqlite3.connect(":memory:")
    configured_secret = "FAKE-STORED-METADATA-CREDENTIAL"
    store = AuditStore(connection, configured_secret_values=[configured_secret])
    event = store.append("run", "ACTION", {"value": 1})
    injected_type = (
        configured_secret
        if value_kind == "exact"
        else f"prefix-{configured_secret}-suffix"
    )
    payload, created_at = connection.execute(
        "SELECT payload, created_at FROM audit_events WHERE run_id = ?", ("run",)
    ).fetchone()
    forged_hash = _event_hash(
        "run", 1, injected_type, payload, created_at, event.previous_hash
    )
    connection.execute(
        "UPDATE audit_events SET event_type = ?, event_hash = ? WHERE run_id = ?",
        (injected_type, forged_hash, "run"),
    )

    verification = store.verify_chain("run")
    assert verification.valid is False
    assert verification.first_invalid_sequence == 1
    with pytest.raises(AuditUnavailable) as captured:
        store.list_events("run")
    assert configured_secret not in repr(captured.value)


@pytest.mark.parametrize(
    ("configured_secret", "payload"),
    [
        ("REDACTED", {"token": "fake-value"}),
        ("#2", {"[REDACTED]": 0, "key-containing-#2": 1}),
    ],
)
def test_final_canonical_payload_must_not_contain_configured_secret(
    configured_secret: str, payload: object
) -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection, configured_secret_values=[configured_secret])

    with pytest.raises(AuditUnavailable) as captured:
        store.append("run", "ACTION", payload)

    assert configured_secret not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("column", "target_sequence", "tampered_value", "invalid_sequence"),
    [
        ("payload", 1, '{"value":9}', 1),
        ("event_type", 1, "TAMPERED", 1),
        ("previous_hash", 2, "broken-link", 2),
        ("event_hash", 1, "0" * 64, 1),
        ("sequence", 2, 5, 2),
        ("created_at", 2, "2000-01-01T00:00:00.000000Z", 2),
    ],
)
def test_list_and_append_fail_closed_on_any_invalid_existing_chain(
    column: str,
    target_sequence: int,
    tampered_value: object,
    invalid_sequence: int,
) -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    store.append("run", "FIRST", {"value": 1})
    store.append("run", "SECOND", {"value": 2})
    connection.execute(
        f"UPDATE audit_events SET {column} = ? WHERE run_id = ? AND sequence = ?",
        (tampered_value, "run", target_sequence),
    )
    rows_before = connection.execute(
        "SELECT * FROM audit_events WHERE run_id = ? ORDER BY sequence", ("run",)
    ).fetchall()

    verification = store.verify_chain("run")
    assert verification.valid is False
    assert verification.first_invalid_sequence == invalid_sequence
    with pytest.raises(AuditUnavailable):
        store.list_events("run")
    with pytest.raises(AuditUnavailable):
        store.append("run", "THIRD", {"value": 3})

    rows_after = connection.execute(
        "SELECT * FROM audit_events WHERE run_id = ? ORDER BY sequence", ("run",)
    ).fetchall()
    assert rows_after == rows_before


@pytest.mark.parametrize(
    ("column", "tampered_value"),
    [
        ("event_type", sqlite3.Binary(b"ACTION")),
        ("payload", sqlite3.Binary(b'{"value":1}')),
        ("previous_hash", sqlite3.Binary(b"")),
        ("event_hash", sqlite3.Binary(b"0" * 64)),
        ("created_at", sqlite3.Binary(b"2000-01-01T00:00:00.000000Z")),
    ],
)
def test_text_columns_reject_sqlite_blob_storage(
    column: str, tampered_value: object
) -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    store.append("run", "ACTION", {"value": 1})
    connection.execute(
        f"UPDATE audit_events SET {column} = ? WHERE run_id = ?",
        (tampered_value, "run"),
    )
    rows_before = connection.execute("SELECT * FROM audit_events").fetchall()

    assert store.verify_chain("run").valid is False
    with pytest.raises(AuditUnavailable):
        store.list_events("run")
    with pytest.raises(AuditUnavailable):
        store.append("run", "NEXT", {"value": 2})
    assert connection.execute("SELECT * FROM audit_events").fetchall() == rows_before


def test_real_sequence_is_not_coerced_to_integer_even_with_matching_hash() -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    event = store.append("run", "ACTION", {"value": 1})
    payload, created_at = connection.execute(
        "SELECT payload, created_at FROM audit_events WHERE run_id = ?", ("run",)
    ).fetchone()
    coerced_hash = _event_hash(
        "run", 1, "ACTION", payload, created_at, event.previous_hash
    )
    connection.execute(
        "UPDATE audit_events SET sequence = ?, event_hash = ? WHERE run_id = ?",
        (1.5, coerced_hash, "run"),
    )
    rows_before = connection.execute("SELECT * FROM audit_events").fetchall()

    assert store.verify_chain("run").valid is False
    with pytest.raises(AuditUnavailable):
        store.list_events("run")
    with pytest.raises(AuditUnavailable):
        store.append("run", "NEXT", {"value": 2})
    assert connection.execute("SELECT * FROM audit_events").fetchall() == rows_before


def test_blob_run_id_is_detected_as_invalid_for_the_requested_chain() -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    store.append("run", "ACTION", {"value": 1})
    connection.execute(
        "UPDATE audit_events SET run_id = ? WHERE run_id = ?",
        (sqlite3.Binary(b"run"), "run"),
    )
    rows_before = connection.execute("SELECT * FROM audit_events").fetchall()

    assert store.verify_chain("run").valid is False
    with pytest.raises(AuditUnavailable):
        store.list_events("run")
    with pytest.raises(AuditUnavailable):
        store.append("run", "NEXT", {"value": 2})
    assert connection.execute("SELECT * FROM audit_events").fetchall() == rows_before


@pytest.mark.parametrize("store_count", [1, 2], ids=["same-store", "two-stores"])
def test_same_connection_concurrent_successes_are_never_rolled_back(
    store_count: int,
) -> None:
    connection = sqlite3.connect(
        ":memory:", check_same_thread=False, factory=_PausingInsertConnection
    )
    assert isinstance(connection, _PausingInsertConnection)
    first_store = AuditStore(connection)
    stores = (
        [first_store, first_store]
        if store_count == 1
        else [first_store, AuditStore(connection)]
    )
    connection.pause_first_insert = True
    results: dict[str, AuditEvent | AuditUnavailable] = {}

    def append(name: str, store: AuditStore) -> None:
        try:
            results[name] = store.append("run", name, {"name": name})
        except AuditUnavailable as error:
            results[name] = error

    first = threading.Thread(target=append, args=("FIRST", stores[0]))
    second = threading.Thread(target=append, args=("SECOND", stores[1]))
    first.start()
    assert connection.first_insert_waiting.wait(timeout=2)
    second.start()
    second_finished_while_first_waited = connection.second_thread_finished.wait(
        timeout=0.2
    )
    connection.release_first_insert.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert results

    final_events = first_store.list_events("run")
    assert first_store.verify_chain("run").valid is True
    successful = [
        result for result in results.values() if isinstance(result, AuditEvent)
    ]
    assert successful
    for returned in successful:
        assert any(
            stored.sequence == returned.sequence
            and stored.event_hash == returned.event_hash
            for stored in final_events
        ), (second_finished_while_first_waited, returned)


def test_each_append_uses_a_unique_internal_savepoint_name() -> None:
    connection = sqlite3.connect(":memory:", factory=_PausingInsertConnection)
    assert isinstance(connection, _PausingInsertConnection)
    store = AuditStore(connection)

    store.append("run", "FIRST", {"value": 1})
    store.append("run", "SECOND", {"value": 2})

    assert len(connection.savepoint_names) == 2
    assert len(set(connection.savepoint_names)) == 2


def test_different_connections_concurrently_fail_safely_without_breaking_chain(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent-audit.sqlite3"
    first_connection = sqlite3.connect(
        database,
        timeout=1,
        check_same_thread=False,
        factory=_BarrierInsertConnection,
    )
    second_connection = sqlite3.connect(
        database,
        timeout=1,
        check_same_thread=False,
        factory=_BarrierInsertConnection,
    )
    assert isinstance(first_connection, _BarrierInsertConnection)
    assert isinstance(second_connection, _BarrierInsertConnection)
    first_store = AuditStore(first_connection)
    second_store = AuditStore(second_connection)
    barrier = threading.Barrier(2)
    first_connection.insert_barrier = barrier
    second_connection.insert_barrier = barrier
    results: dict[str, AuditEvent | BaseException] = {}

    def append(name: str, store: AuditStore) -> None:
        try:
            results[name] = store.append("run", name, {"name": name})
        except BaseException as error:
            results[name] = error

    threads = [
        threading.Thread(target=append, args=("FIRST", first_store)),
        threading.Thread(target=append, args=("SECOND", second_store)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()

    assert len(results) == 2
    assert all(
        isinstance(result, (AuditEvent, AuditUnavailable))
        for result in results.values()
    )
    verifier = AuditStore(sqlite3.connect(database))
    final_events = verifier.list_events("run")
    assert verifier.verify_chain("run").valid is True
    for returned in results.values():
        if isinstance(returned, AuditEvent):
            assert any(
                stored.sequence == returned.sequence
                and stored.event_hash == returned.event_hash
                for stored in final_events
            )
    first_connection.close()
    second_connection.close()


def test_failed_append_preserves_explicit_outer_transaction_and_sentinel() -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    connection.execute("CREATE TABLE transaction_sentinel (value TEXT NOT NULL)")
    connection.execute(
        """
        CREATE TRIGGER reject_outer_transaction_append
        BEFORE INSERT ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'coordinated append failure');
        END
        """
    )
    connection.commit()
    connection.execute("BEGIN")
    connection.execute("INSERT INTO transaction_sentinel VALUES ('preserve-me')")

    with pytest.raises(AuditUnavailable):
        store.append("run", "ACTION", {"value": 1})

    assert connection.in_transaction is True
    assert connection.execute("SELECT value FROM transaction_sentinel").fetchall() == [
        ("preserve-me",)
    ]
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    connection.commit()
    assert connection.execute("SELECT value FROM transaction_sentinel").fetchall() == [
        ("preserve-me",)
    ]


class _PausingInsertConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pause_first_insert = False
        self.first_insert_waiting = threading.Event()
        self.release_first_insert = threading.Event()
        self.second_thread_finished = threading.Event()
        self.savepoint_names: list[str] = []
        self._pause_claimed = False
        self._pause_lock = threading.Lock()

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        normalized = sql.strip()
        if normalized.startswith("SAVEPOINT "):
            self.savepoint_names.append(normalized.split()[1])
        should_pause = False
        if self.pause_first_insert and normalized.startswith(
            "INSERT INTO audit_events"
        ):
            with self._pause_lock:
                if not self._pause_claimed:
                    self._pause_claimed = True
                    should_pause = True
        if should_pause:
            self.first_insert_waiting.set()
            if not self.release_first_insert.wait(timeout=2):
                raise sqlite3.OperationalError("coordinated test timeout")
        result = super().execute(sql, parameters)
        if (
            self.pause_first_insert
            and normalized.startswith("RELEASE SAVEPOINT")
            and threading.current_thread() is not threading.main_thread()
        ):
            self.second_thread_finished.set()
        return result


class _BarrierInsertConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.insert_barrier: threading.Barrier | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if self.insert_barrier is not None and sql.strip().startswith(
            "INSERT INTO audit_events"
        ):
            self.insert_barrier.wait(timeout=2)
        return super().execute(sql, parameters)


def _event_hash(
    run_id: str,
    sequence: int,
    event_type: str,
    payload: str,
    created_at: str,
    previous_hash: str,
) -> str:
    text = f"{run_id}|{sequence}|{event_type}|{payload}|{created_at}|{previous_hash}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
