import hashlib
import json
import sqlite3
from dataclasses import FrozenInstanceError
from datetime import UTC, timedelta, timezone
from typing import Any, cast

import pytest

from safefix.governance.audit import AuditStore, AuditUnavailable


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
