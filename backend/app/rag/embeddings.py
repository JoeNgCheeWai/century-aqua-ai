"""
Embeddings for the AI_CAO RAG pipeline (OpenAI text-embedding-3-small).

* Reads OPENAI_API_KEY from the environment when first used.
* The ``openai`` package is imported lazily, so the backend starts even if the
  package or key is missing; callers get an ``EmbeddingError`` instead.
* Error messages carry only the exception type, never its text, so API keys or
  request content cannot leak into logs.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536  # the Pinecone index must be created with this size
MAX_BATCH_SIZE = 100  # inputs per request (the API allows up to 2048)


class EmbeddingError(Exception):
    """Raised when an embedding cannot be generated."""


_client: Any = None


def is_configured() -> bool:
    """True if OPENAI_API_KEY is set."""
    return bool(os.getenv("OPENAI_API_KEY"))


def _get_client() -> Any:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EmbeddingError("OPENAI_API_KEY is not configured.")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise EmbeddingError("The 'openai' package is not installed.") from exc
        _client = AsyncOpenAI(api_key=api_key, max_retries=3, timeout=30.0)
    return _client


async def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed many texts, batching requests for efficiency.

    Batches are sent one after another (not in parallel) to stay friendly to
    rate limits. The result is in the same order as ``texts``.

    Raises:
        EmbeddingError: empty/blank input text, missing key or package, or any
            failure of the OpenAI request.
    """
    if not texts:
        return []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingError("Cannot embed empty text.")

    client = _get_client()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[start : start + MAX_BATCH_SIZE]
        try:
            response = await client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                f"OpenAI embeddings request failed: {type(exc).__name__}"
            ) from exc

        data = sorted(response.data, key=lambda item: item.index)
        if len(data) != len(batch):
            raise EmbeddingError("OpenAI returned an unexpected number of embeddings.")
        vectors.extend(list(item.embedding) for item in data)
    return vectors


async def generate_embedding(text: str) -> list[float]:
    """Embed a single text. See ``generate_embeddings_batch`` for errors."""
    return (await generate_embeddings_batch([text]))[0]
