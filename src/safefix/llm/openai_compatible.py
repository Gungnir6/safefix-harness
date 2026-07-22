from __future__ import annotations

from collections.abc import Mapping, Sequence

import httpx

from safefix.credentials import CredentialService
from safefix.llm.base import ModelMessage, ModelResponse


class ProviderError(RuntimeError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class OpenAICompatibleClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        credentials: CredentialService,
        *,
        endpoint: str,
        model: str,
        provider: str = "openai-compatible",
    ) -> None:
        self._http = http
        self._credentials = credentials
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._provider = provider

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        settings: Mapping[str, object],
    ) -> ModelResponse:
        del settings
        credential = self._credentials.get_for_request(self._provider)
        try:
            response = await self._http.post(
                f"{self._endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {credential}"},
                json={
                    "model": self._model,
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderError("provider request failed") from exc

        if response.status_code == 401:
            raise ProviderAuthenticationError("provider authentication failed")
        if response.status_code == 429:
            raise ProviderRateLimitError("provider rate limit exceeded")
        if response.is_error:
            raise ProviderError("provider request failed")

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError
            usage = body.get("usage", {})
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderResponseError(
                "provider returned a malformed response"
            ) from exc

        return ModelResponse(
            text=text,
            provider_request_id=response.headers.get("x-request-id"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
