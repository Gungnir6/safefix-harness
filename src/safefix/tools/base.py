from __future__ import annotations

from typing import Protocol

from safefix.domain import Action, ToolResult


class Tool(Protocol):
    @property
    def action_type(self) -> type[object]:
        raise NotImplementedError

    async def execute(self, action: Action) -> ToolResult:
        raise NotImplementedError
