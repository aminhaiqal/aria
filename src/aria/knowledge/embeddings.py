import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from aria.openrouter import OpenRouterClient, OpenRouterError, RetryableOpenRouterError

VECTOR_DIMENSIONS = 384
OPENROUTER_EMBEDDING_INPUT_VERSION = "aria-document-section-v2"
TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
SUPPORTED_PROVIDERS = frozenset({"local_hash", "openrouter"})


class EmbeddingError(RuntimeError):
    pass


class RetryableEmbeddingError(EmbeddingError):
    pass


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    prompt_tokens: int = 0


class EmbeddingProvider(Protocol):
    provider_name: str
    model: str
    dimensions: int

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch: ...


def embedding_configuration(provider_name: str | None = None) -> tuple[str, str, int]:
    provider = provider_name or settings.EMBEDDING_PROVIDER
    if provider not in SUPPORTED_PROVIDERS:
        raise ImproperlyConfigured(
            f"Unsupported embedding provider '{provider}'. Expected local_hash or openrouter."
        )
    dimensions = settings.EMBEDDING_DIMENSIONS
    if dimensions != VECTOR_DIMENSIONS:
        raise ImproperlyConfigured(
            f"ARIA_EMBEDDING_DIMENSIONS must be {VECTOR_DIMENSIONS} for the current schema."
        )
    if provider == "local_hash":
        model = settings.LOCAL_EMBEDDING_MODEL
    else:
        model = (
            f"{settings.OPENROUTER_EMBEDDING_MODEL}@"
            f"{OPENROUTER_EMBEDDING_INPUT_VERSION}"
        )
    return provider, model, dimensions


def _validate_vectors(
    vectors: list[list[float]] | tuple[tuple[float, ...], ...],
    *,
    expected_count: int,
    dimensions: int,
) -> tuple[tuple[float, ...], ...]:
    if len(vectors) != expected_count:
        raise EmbeddingError(
            f"Embedding provider returned {len(vectors)} vectors for {expected_count} inputs."
        )
    validated: list[tuple[float, ...]] = []
    for vector in vectors:
        if len(vector) != dimensions:
            raise EmbeddingError(
                f"Embedding provider returned {len(vector)} dimensions; expected {dimensions}."
            )
        converted = tuple(float(value) for value in vector)
        if not all(math.isfinite(value) for value in converted):
            raise EmbeddingError("Embedding provider returned a non-finite vector value.")
        validated.append(converted)
    return tuple(validated)


class LocalHashEmbeddingProvider:
    provider_name = "local_hash"

    def __init__(self) -> None:
        _, self.model, self.dimensions = embedding_configuration(self.provider_name)

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        vectors: list[list[float]] = []
        for text in texts:
            tokens = TOKEN_PATTERN.findall(text.casefold())
            features = tokens + [
                f"{left}\x1f{right}" for left, right in zip(tokens, tokens[1:], strict=False)
            ]
            vector = [0.0] * self.dimensions
            for feature in features:
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
                index = int.from_bytes(digest[:8], "big") % self.dimensions
                sign = 1.0 if digest[8] & 1 else -1.0
                vector[index] += sign

            magnitude = math.sqrt(sum(value * value for value in vector))
            if magnitude:
                vector = [value / magnitude for value in vector]
            vectors.append(vector)
        return EmbeddingBatch(
            vectors=_validate_vectors(
                vectors,
                expected_count=len(texts),
                dimensions=self.dimensions,
            )
        )


class OpenRouterEmbeddingProvider:
    provider_name = "openrouter"

    def __init__(self, *, client=None) -> None:
        _, self.model, self.dimensions = embedding_configuration(self.provider_name)
        self.request_model = settings.OPENROUTER_EMBEDDING_MODEL
        if client is not None:
            self.client = client
            return
        try:
            self.client = OpenRouterClient()
        except OpenRouterError as error:
            raise EmbeddingError(str(error)) from error

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=())
        try:
            response = self.client.create_embeddings(
                model=self.request_model,
                inputs=[text if text.strip() else " " for text in texts],
                dimensions=self.dimensions,
            )
        except RetryableOpenRouterError as error:
            raise RetryableEmbeddingError(str(error)) from error
        except OpenRouterError as error:
            raise EmbeddingError(str(error)) from error
        vectors = _validate_vectors(
            response.vectors,
            expected_count=len(texts),
            dimensions=self.dimensions,
        )
        return EmbeddingBatch(
            vectors=vectors,
            prompt_tokens=response.prompt_tokens,
        )


def get_embedding_provider(
    provider_name: str | None = None,
    *,
    client=None,
) -> EmbeddingProvider:
    provider, _, _ = embedding_configuration(provider_name)
    if provider == "local_hash":
        return LocalHashEmbeddingProvider()
    return OpenRouterEmbeddingProvider(client=client)


def embed_text(text: str, *, provider_name: str | None = None) -> list[float]:
    provider = get_embedding_provider(provider_name)
    result = provider.embed_texts([text])
    return list(result.vectors[0])
