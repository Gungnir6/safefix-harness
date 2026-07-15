from __future__ import annotations

from collections.abc import Mapping, Sequence

from safefix.llm.base import ModelMessage, ModelResponse


class ScriptedMockLLM:
    def __init__(self, script: Sequence[str]) -> None:
        self._script = tuple(script)
        self._call_index = 0

    @property
    def script(self) -> tuple[str, ...]:
        return self._script

    @property
    def call_index(self) -> int:
        return self._call_index

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        settings: Mapping[str, object],
    ) -> ModelResponse:
        del messages, settings
        if self._call_index >= len(self._script):
            raise AssertionError("script exhausted")
        text = self._script[self._call_index]
        self._call_index += 1
        return ModelResponse(text=text)
