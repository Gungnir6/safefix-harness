from __future__ import annotations

import sqlite3

import pytest

from safefix.memory import MemoryStore


def test_memory_is_project_scoped_relevant_and_bounded() -> None:
    store = MemoryStore(sqlite3.connect(":memory:"))
    store.add(
        "project-a",
        "convention",
        "Use pytest and keep functions pure",
        ("pytest", "pure"),
    )
    store.add("project-b", "convention", "Use jest", ("jest",))

    results = store.search("project-a", "pytest failure", limit=3, char_budget=80)

    assert [item.project_id for item in results] == ["project-a"]
    assert sum(len(item.content) for item in results) <= 80


def test_memory_search_ranks_keyword_overlap_and_has_stable_ids() -> None:
    store = MemoryStore(sqlite3.connect(":memory:"))
    unrelated = store.add("p", "convention", "Use black", ("format",))
    relevant = store.add("p", "failure", "pytest timeout fix", ("pytest", "timeout"))

    results = store.search("p", "pytest timeout", limit=5, char_budget=200)

    assert results[0].id == relevant.id
    assert unrelated.id != relevant.id


def test_memory_delete_project_removes_only_that_project() -> None:
    store = MemoryStore(sqlite3.connect(":memory:"))
    store.add("a", "convention", "alpha", ("alpha",))
    store.add("b", "convention", "beta", ("beta",))

    assert store.delete_project("a") == 1
    assert store.list("a") == []
    assert len(store.list("b")) == 1


def test_memory_rejects_configured_secrets_and_raw_transcripts() -> None:
    store = MemoryStore(
        sqlite3.connect(":memory:"), configured_secret_values=("secret-value",)
    )

    with pytest.raises(ValueError, match="sensitive"):
        store.add("p", "convention", "token=secret-value", ("token",))
    with pytest.raises(ValueError, match="not allowed"):
        store.add("p", "transcript", "full chat", ("chat",))

