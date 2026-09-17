import json
import time
from dataclasses import dataclass
from typing import TypeVar

import httpx
from django.conf import settings
from pydantic import BaseModel, ValidationError


class OpenRouterError(RuntimeError):
    pass


class RetryableOpenRouterError(OpenRouterError):
    pass


@dataclass(frozen=True)
class OpenRouterStructuredResult:
    response_id: str
    output: BaseModel
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class OpenRouterEmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    prompt_tokens: int = 0


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _bounded_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:500]
        if isinstance(error, str):
            return error[:500]
    return f"HTTP {response.status_code}"


class OpenRouterClient:
    def __init__(self, *, client: httpx.Client | None = None) -> None:
        if not settings.OPENROUTER_API_KEY:
            raise OpenRouterError("OPENROUTER_API_KEY is required for hosted AI requests.")
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        if settings.OPENROUTER_HTTP_REFERER:
            headers["HTTP-Referer"] = settings.OPENROUTER_HTTP_REFERER
        if settings.OPENROUTER_APP_TITLE:
            headers["X-OpenRouter-Title"] = settings.OPENROUTER_APP_TITLE
        if client is not None:
            client.headers.update(headers)
            self._client = client
        else:
            self._client = httpx.Client(
                base_url=settings.OPENROUTER_BASE_URL.rstrip("/") + "/",
                headers=headers,
                timeout=settings.OPENROUTER_TIMEOUT_SECONDS,
                follow_redirects=False,
            )

    @staticmethod
    def _provider_preferences() -> dict:
        preferences = {
            "require_parameters": True,
            "allow_fallbacks": settings.OPENROUTER_ALLOW_PROVIDER_FALLBACKS,
        }
        if settings.OPENROUTER_ZDR:
            preferences["zdr"] = True
        if settings.OPENROUTER_DATA_COLLECTION:
            preferences["data_collection"] = settings.OPENROUTER_DATA_COLLECTION
        return preferences

    def _post(self, path: str, payload: dict) -> dict:
        attempts = settings.OPENROUTER_MAX_RETRIES + 1
        for attempt in range(attempts):
            try:
                response = self._client.post(path, json=payload)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt + 1 == attempts:
                    raise RetryableOpenRouterError(str(error)) from error
                time.sleep(min(0.25 * (2**attempt), 2.0))
                continue
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt + 1 == attempts:
                    raise RetryableOpenRouterError(_bounded_error_message(response))
                time.sleep(min(0.25 * (2**attempt), 2.0))
                continue
            if response.is_error:
                raise OpenRouterError(_bounded_error_message(response))
            try:
                result = response.json()
            except ValueError as error:
                raise OpenRouterError("OpenRouter returned an invalid JSON response.") from error
            if not isinstance(result, dict):
                raise OpenRouterError("OpenRouter returned an unexpected response shape.")
            return result
        raise RetryableOpenRouterError("OpenRouter request exhausted its retry budget.")

    def generate_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        input_payload: dict,
        output_model: type[StructuredModel],
        schema_name: str,
        reasoning_effort: str,
        max_output_tokens: int,
    ) -> OpenRouterStructuredResult:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(input_payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": output_model.model_json_schema(),
                },
            },
            "reasoning": {"effort": reasoning_effort},
            "max_completion_tokens": max_output_tokens,
            "provider": self._provider_preferences(),
        }
        response = self._post("chat/completions", payload)
        try:
            message = response["choices"][0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise OpenRouterError("OpenRouter returned no structured message content.") from error
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterError("OpenRouter returned empty structured message content.")
        try:
            output = output_model.model_validate_json(content)
        except ValidationError as error:
            message = "OpenRouter returned output that failed schema validation."
            raise OpenRouterError(message) from error
        usage = response.get("usage") or {}
        return OpenRouterStructuredResult(
            response_id=str(response.get("id") or ""),
            output=output,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    def create_embeddings(
        self,
        *,
        model: str,
        inputs: list[str],
        dimensions: int,
    ) -> OpenRouterEmbeddingResult:
        response = self._post(
            "embeddings",
            {
                "model": model,
                "input": inputs,
                "dimensions": dimensions,
                "encoding_format": "float",
                "provider": self._provider_preferences(),
            },
        )
        data = response.get("data")
        if not isinstance(data, list):
            raise OpenRouterError("OpenRouter returned no embedding data.")
        try:
            ordered = sorted(data, key=lambda item: item["index"])
            indexes = [item["index"] for item in ordered]
            vectors = tuple(tuple(float(value) for value in item["embedding"]) for item in ordered)
        except (KeyError, TypeError, ValueError) as error:
            raise OpenRouterError("OpenRouter returned malformed embedding data.") from error
        if indexes != list(range(len(inputs))):
            raise OpenRouterError("OpenRouter returned invalid embedding response indexes.")
        usage = response.get("usage") or {}
        return OpenRouterEmbeddingResult(
            vectors=vectors,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
        )
