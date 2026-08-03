import hashlib
import math
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

VECTOR_DIMENSIONS = 384
TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


def embedding_configuration() -> tuple[str, str, int]:
    provider = settings.EMBEDDING_PROVIDER
    model = settings.EMBEDDING_MODEL
    dimensions = settings.EMBEDDING_DIMENSIONS
    if provider != "local_hash":
        raise ImproperlyConfigured(
            "Phase 3A supports only the self-hosted 'local_hash' embedding provider."
        )
    if dimensions != VECTOR_DIMENSIONS:
        raise ImproperlyConfigured(
            f"ARIA_EMBEDDING_DIMENSIONS must be {VECTOR_DIMENSIONS} for the current schema."
        )
    return provider, model, dimensions


def embed_text(text: str) -> list[float]:
    """Create a deterministic local lexical projection for pgvector retrieval.

    This is deliberately not presented as a semantic model. It provides a private,
    dependency-light vector baseline and a stable provider boundary for a later
    self-hosted embedding model.
    """

    _, _, dimensions = embedding_configuration()
    tokens = TOKEN_PATTERN.findall(text.casefold())
    features = tokens + [
        f"{left}\x1f{right}" for left, right in zip(tokens, tokens[1:], strict=False)
    ]
    vector = [0.0] * dimensions
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
        index = int.from_bytes(digest[:8], "big") % dimensions
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[index] += sign

    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude:
        vector = [value / magnitude for value in vector]
    return vector
