from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from safefix.llm.base import ModelMessage, ModelResponse
from safefix.llm.mock import ScriptedMockLLM


@pytest.mark.asyncio
async def test_mock_returns_script_in_order_and_then_fails() -> None:
    client = ScriptedMockLLM(
        ['{"type":"finish","id":"a1","reason":"done","summary":"ok"}']
    )

    first = await client.complete([], {})

    assert '"type":"finish"' in first.text
    with pytest.raises(AssertionError, match="^script exhausted$"):
        await client.complete([], {})


@pytest.mark.asyncio
async def test_mock_copies_script_to_an_immutable_tuple() -> None:
    source = ["first", "second"]
    client = ScriptedMockLLM(source)
    source[0] = "changed"

    assert client.script == ("first", "second")
    assert (await client.complete([], {})).text == "first"
    assert client.call_index == 1


def test_model_boundary_values_are_immutable() -> None:
    message = ModelMessage(role="user", content="fix it")
    response = ModelResponse(
        text="answer",
        provider_request_id="req-1",
        input_tokens=3,
        output_tokens=5,
    )

    assert (message.role, message.content) == ("user", "fix it")
    assert response == ModelResponse("answer", "req-1", 3, 5)
    with pytest.raises(FrozenInstanceError):
        message.content = "mutated"
