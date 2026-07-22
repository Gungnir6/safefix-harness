from __future__ import annotations

from safefix.config import MemorySettings
from safefix.domain import RunSnapshot
from safefix.llm.base import ModelMessage
from safefix.memory import MemoryStore


_SYSTEM_MESSAGE = """You are the SafeFix repair agent. Return exactly one JSON action object.
Allowed action types: list_files, read_file, search_text, apply_patch,
run_validation, run_process, finish. Include id and reason. Never return shell
syntax outside structured run_process program and args fields."""


class ContextBuilder:
    def __init__(
        self,
        memory: MemoryStore | None,
        memory_settings: MemorySettings,
        *,
        section_char_budget: int = 4000,
    ) -> None:
        self._memory = memory
        self._memory_settings = memory_settings
        self._section_budget = section_char_budget

    def build(self, snapshot: RunSnapshot) -> list[ModelMessage]:
        messages = [ModelMessage("system", self._bounded(_SYSTEM_MESSAGE))]
        messages.append(
            ModelMessage(
                "user",
                self._bounded(
                    f"Task: {snapshot.description}\n"
                    f"Workspace: {snapshot.workspace_root}\n"
                    f"Changed files: {', '.join(snapshot.changed_files) or 'none'}"
                ),
            )
        )
        if self._memory is not None:
            records = self._memory.search(
                snapshot.project_id,
                snapshot.description,
                limit=self._memory_settings.retrieval_limit,
                char_budget=self._memory_settings.character_budget,
            )
            if records:
                memory_text = "\n".join(f"- {record.content}" for record in records)
                messages.append(ModelMessage("user", self._bounded(memory_text)))

        state_lines = [
            f"Remaining steps: {snapshot.budget.remaining_steps}",
            f"Remaining repairs: {snapshot.budget.remaining_repairs}",
        ]
        if snapshot.latest_tool_result is not None:
            result = snapshot.latest_tool_result
            state_lines.extend(
                (
                    f"Latest tool success: {result.success}",
                    f"Latest stdout: {result.stdout_summary}",
                    f"Latest stderr: {result.stderr_summary}",
                    f"Latest error: {result.error_type or 'none'}",
                )
            )
        if snapshot.feedback_history:
            latest = snapshot.feedback_history[-1]
            state_lines.append(
                f"Latest feedback: {latest.category.value}: {latest.summary}"
            )
        messages.append(ModelMessage("user", self._bounded("\n".join(state_lines))))
        return messages

    def _bounded(self, value: str) -> str:
        return value[: self._section_budget]

