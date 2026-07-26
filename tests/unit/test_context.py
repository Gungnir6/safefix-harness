from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from safefix.config import MemorySettings
from safefix.context import ContextBuilder
from safefix.domain import BudgetState, RunSnapshot, RunStatus, ToolResult
from safefix.memory import MemoryStore


def _snapshot() -> RunSnapshot:
    now = datetime.now(UTC)
    return RunSnapshot(
        run_id="run-1",
        task_id="task-1",
        project_id="project-1",
        workspace_root="C:/workspace",
        description="fix failing tests",
        status=RunStatus.RUNNING,
        repair_round=1,
        step_count=2,
        budget=BudgetState(
            max_steps=10,
            remaining_steps=8,
            max_repair_rounds=3,
            remaining_repairs=2,
        ),
        version=1,
        latest_tool_result=ToolResult.failure("a1", "TEST", "one failure"),
        created_at=now,
        updated_at=now,
    )


def test_context_is_provider_neutral_bounded_and_contains_current_state() -> None:
    memory = MemoryStore(sqlite3.connect(":memory:"))
    memory.add("project-1", "convention", "Use pytest for tests", ("pytest",))
    builder = ContextBuilder(
        memory,
        MemorySettings(retrieval_limit=3, character_budget=256),
        section_char_budget=300,
    )

    messages = builder.build(_snapshot())

    assert messages[0].role == "system"
    assert "action" in messages[0].content.casefold()
    assert any("fix failing tests" in item.content for item in messages)
    assert any("Use pytest" in item.content for item in messages)
    assert all(len(item.content) <= 300 for item in messages)


def test_context_never_includes_approval_tokens_or_raw_audit_payloads() -> None:
    messages = ContextBuilder(None, MemorySettings()).build(_snapshot())
    rendered = "\n".join(message.content for message in messages).casefold()

    assert "one_time_token_hash" not in rendered
    assert "frozen_action_json" not in rendered
    assert "audit payload" not in rendered


def test_context_system_message_requires_inspection_feedback_and_validation() -> None:
    messages = ContextBuilder(None, MemorySettings()).build(_snapshot())
    system = messages[0].content

    assert "Inspect relevant files before editing" in system
    assert "Use run_validation" in system
    assert "Use the latest tool result and feedback" in system
    assert "finish only after validation succeeds" in system

