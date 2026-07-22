from __future__ import annotations

import json

import httpx
import pytest

from safefix.credentials import CredentialService
from safefix.llm.base import ModelMessage
from safefix.llm.openai_compatible import (
    OpenAICompatibleClient,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from tests.unit.test_credentials import FakeKeyring


def _credentials(secret: str = "sk-SECRET") -> CredentialService:
    backend = FakeKeyring()
    service = CredentialService(backend)
    service.set("openai-compatible", secret)
    return service


@pytest.mark.asyncio
async def test_complete_performs_one_request_and_parses_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "req-1"},
            json={
                "choices": [{"message": {"content": "assistant text"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAICompatibleClient(
            http,
            _credentials(),
            endpoint="https://provider.test/v1",
            model="model-1",
        )
        result = await client.complete(
            [ModelMessage("user", "hello")], {"ignored": True}
        )

    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer sk-SECRET"
    assert json.loads(requests[0].content) == {
        "model": "model-1",
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert result.text == "assistant text"
    assert result.provider_request_id == "req-1"
    assert result.input_tokens == 4
    assert result.output_tokens == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(401, ProviderAuthenticationError), (429, ProviderRateLimitError)],
)
async def test_http_errors_are_typed_and_do_not_leak_key(
    status_code: int, error_type: type[Exception]
) -> None:
    secret = "sk-DO-NOT-LEAK"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code, json={"error": secret})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = OpenAICompatibleClient(
            http, _credentials(secret), endpoint="https://provider.test", model="m"
        )
        with pytest.raises(error_type) as captured:
            await client.complete([ModelMessage("user", "hello")], {})

    assert secret not in repr(captured.value)


@pytest.mark.asyncio
async def test_timeout_and_malformed_response_are_typed() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler)
    ) as http:
        client = OpenAICompatibleClient(
            http, _credentials(), endpoint="https://provider.test", model="m"
        )
        with pytest.raises(ProviderTimeoutError):
            await client.complete([ModelMessage("user", "hello")], {})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": []})
        )
    ) as http:
        client = OpenAICompatibleClient(
            http, _credentials(), endpoint="https://provider.test", model="m"
        )
        with pytest.raises(ProviderResponseError):
            await client.complete([ModelMessage("user", "hello")], {})

