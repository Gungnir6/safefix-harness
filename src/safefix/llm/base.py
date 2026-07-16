from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    provider_request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    async def complete(
        self,
        messages: Sequence[ModelMessage],
        settings: Mapping[str, object],
    ) -> ModelResponse: ...
