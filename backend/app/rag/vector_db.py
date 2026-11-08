"""
Pinecone vector store for the AI_CAO RAG pipeline.

Reads PINECONE_API_KEY and PINECONE_INDEX from the environment. The index must
already exist and be created with dimension 1536 and the cosine metric (the
size of OpenAI text-embedding-3-small vectors); this module never creates it.

Pinecone's client is synchronous, so the async functions here run its calls in a
worker thread to keep the FastAPI event loop free. Every failure is raised as a
``VectorDBError`` carrying only the underlying exception type, so callers can
degrade gracefully (the retrieval layer does) without leaking details.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

from app.rag.embeddings import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

UPSERT_BATCH_SIZE = 100


class VectorDBError(Exception):
    """Raised when Pinecone is not configured or a request fails."""


_index: Any = None


def is_configured() -> bool:
    """True if both PINECONE_API_KEY and PINECONE_INDEX are set."""
    return bool(os.getenv("PINECONE_API_KEY") and os.getenv("PINECONE_INDEX"))


def _create_client(api_key: str) -> Any:
    from pinecone import Pinecone  # lazy: keeps backend startup light

    return Pinecone(api_key=api_key)


def init_pinecone() -> Any:
    """
    Connect to the configured Pinecone index (cached after the first success).

    Verifies that the index exists and that its dimension matches the embedding
    model, so a wrong index fails here with a clear message instead of at upsert.

    Raises:
        VectorDBError: not configured, unreachable, index missing, wrong
            dimension, or invalid API key.
    """
    global _index
    if _index is not None:
        return _index

    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX")
    if not api_key or not index_name:
        raise VectorDBError("PINECONE_API_KEY and PINECONE_INDEX must both be set.")

    try:
        client = _create_client(api_key)
        description = client.describe_index(index_name)
        dimension = getattr(description, "dimension", None)
        if dimension is not None and dimension != EMBEDDING_DIMENSIONS:
            raise VectorDBError(
                f"Pinecone index {index_name!r} has dimension {dimension}, but "
                f"text-embedding-3-small needs {EMBEDDING_DIMENSIONS}."
            )
        index = client.Index(index_name)
    except VectorDBError:
        raise
    except Exception as exc:
        raise VectorDBError(
            f"Could not connect to Pinecone index {index_name!r}: {type(exc).__name__}"
        ) from exc

    _index = index
    return index


def _to_metadata(chunk: Mapping[str, Any]) -> dict[str, Any]:
    """Pinecone metadata: scalars only, no nulls, and never the vector itself."""
    metadata: dict[str, Any] = {}
    for key, value in chunk.items():
        if key == "embedding":
            continue
        if value is None:
            value = ""
        metadata[key] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return metadata


async def upsert_chunks(chunks: list[dict[str, Any]]) -> int:
    """
    Upsert embedded chunks into Pinecone.

    Each chunk must contain ``chunk_id`` and ``embedding`` (list of floats);
    every other field is stored as metadata (including ``text``). The vector id
    is the ``chunk_id``, so re-upserting the same chunk overwrites it.

    Returns:
        The number of vectors upserted.

    Raises:
        VectorDBError: a chunk is missing its id or embedding, or Pinecone fails.
    """
    if not chunks:
        return 0

    vectors: list[dict[str, Any]] = []
    for chunk in chunks:
        if not chunk.get("chunk_id") or not chunk.get("embedding"):
            raise VectorDBError("Every chunk needs 'chunk_id' and 'embedding'.")
        vectors.append(
            {
                "id": chunk["chunk_id"],
                "values": list(chunk["embedding"]),
                "metadata": _to_metadata(chunk),
            }
        )

    index = await asyncio.to_thread(init_pinecone)
    try:
        for start in range(0, len(vectors), UPSERT_BATCH_SIZE):
            batch = vectors[start : start + UPSERT_BATCH_SIZE]
            await asyncio.to_thread(index.upsert, vectors=batch)
    except Exception as exc:
        raise VectorDBError(f"Pinecone upsert failed: {type(exc).__name__}") from exc
    return len(vectors)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def query_vectors(embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
    """
    Find the ``top_k`` most similar chunks.

    Returns:
        A list of ``{"id": str, "score": float, "metadata": dict}``, best first.

    Raises:
        VectorDBError: not configured, or Pinecone fails.
    """
    if not embedding:
        raise VectorDBError("Query embedding is empty.")
    top_k = max(1, int(top_k))

    index = await asyncio.to_thread(init_pinecone)
    try:
        response = await asyncio.to_thread(
            index.query, vector=list(embedding), top_k=top_k, include_metadata=True
        )
    except Exception as exc:
        raise VectorDBError(f"Pinecone query failed: {type(exc).__name__}") from exc

    matches = _field(response, "matches", []) or []
    results: list[dict[str, Any]] = []
    for match in matches:
        metadata = _field(match, "metadata", {}) or {}
        results.append(
            {
                "id": _field(match, "id", ""),
                "score": float(_field(match, "score", 0.0) or 0.0),
                "metadata": dict(metadata),
            }
        )
    return results
