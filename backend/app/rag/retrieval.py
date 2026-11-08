"""
Retrieval for the AI_CAO: query -> embedding -> Pinecone -> formatted context.

The functions here NEVER raise. If the RAG stack is not configured, times out,
or fails for any reason, they return an empty result and log a warning (the
exception type only, never the query text), so the AI_CAO keeps working
without knowledge-base context.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.rag import embeddings, vector_db
from app.rag.embeddings import generate_embedding
from app.rag.vector_db import query_vectors

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
RAG_TIMEOUT_SECONDS = 20.0  # embedding + vector search budget


@dataclass(frozen=True)
class RetrievedSource:
    """Where one retrieved excerpt came from."""

    source_file: str
    document_type: str
    page_number: int | None
    section_heading: str
    score: float


@dataclass
class RetrievalResult:
    """Formatted context for the prompt plus the sources it was built from."""

    context: str = ""
    sources: list[RetrievedSource] = field(default_factory=list)


def _clean_excerpt(text: str) -> str:
    # Stops document text from forging the delimiters that wrap the excerpts.
    return text.strip().replace("<<<", "< < <").replace(">>>", "> > >")


def _format_matches(matches: list[dict[str, Any]]) -> RetrievalResult:
    blocks: list[str] = []
    sources: list[RetrievedSource] = []
    for match in matches:
        metadata = match.get("metadata") or {}
        text = _clean_excerpt(str(metadata.get("text", "")))
        if not text:
            continue
        label = len(blocks) + 1
        source_file = str(metadata.get("source_file", "unknown"))
        document_type = str(metadata.get("document_type", "unknown"))
        heading = str(metadata.get("section_heading", "") or "")
        page = metadata.get("page_number")
        page_number = int(page) if isinstance(page, (int, float)) else None
        score = float(match.get("score", 0.0))

        parts = [f"[{label}] {source_file}", f"type: {document_type}"]
        if page_number is not None:
            parts.append(f"page {page_number}")
        if heading:
            parts.append(f"section: {heading}")
        parts.append(f"relevance {score:.2f}")
        blocks.append(" | ".join(parts) + "\n" + text)
        sources.append(
            RetrievedSource(source_file, document_type, page_number, heading, score)
        )
    return RetrievalResult(context="\n\n".join(blocks), sources=sources)


def format_sources_note(sources: list[RetrievedSource]) -> str:
    """
    One line per source file listing its type and the pages retrieved.

    The note lists documents RETRIEVED for the answer; the model may not have
    relied on every one of them.
    """
    if not sources:
        return ""
    grouped: dict[str, dict[str, Any]] = {}
    for source in sources:
        entry = grouped.setdefault(
            source.source_file, {"type": source.document_type, "pages": set()}
        )
        if source.page_number is not None:
            entry["pages"].add(source.page_number)

    lines = ["Knowledge base documents retrieved for this answer:"]
    for name, entry in grouped.items():
        pages = sorted(entry["pages"])
        page_text = ""
        if pages:
            page_text = ", p. " if len(pages) == 1 else ", pp. "
            page_text += ", ".join(str(p) for p in pages)
        lines.append(f"- {name} ({entry['type']}{page_text})")
    return "\n".join(lines)


async def _search(query: str, top_k: int) -> list[dict[str, Any]]:
    embedding = await generate_embedding(query)
    return await query_vectors(embedding, top_k=top_k)


async def retrieve_context_with_sources(
    query: str, top_k: int = DEFAULT_TOP_K
) -> RetrievalResult:
    """
    Retrieve knowledge-base excerpts for ``query``.

    Returns an empty ``RetrievalResult`` when the query is blank, RAG is not
    configured, nothing matched, or anything at all went wrong.
    """
    if not query or not query.strip():
        return RetrievalResult()
    if not (embeddings.is_configured() and vector_db.is_configured()):
        logger.info("RAG not configured; skipping retrieval.")
        return RetrievalResult()

    try:
        matches = await asyncio.wait_for(
            _search(query, top_k), timeout=RAG_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning("RAG retrieval timed out after %ss; continuing without context.", RAG_TIMEOUT_SECONDS)
        return RetrievalResult()
    except Exception as exc:  # embeddings, Pinecone, anything unexpected
        logger.warning("RAG retrieval failed (%s); continuing without context.", type(exc).__name__)
        return RetrievalResult()

    return _format_matches(matches)


async def retrieve_context(query: str, top_k: int = DEFAULT_TOP_K) -> str:
    """
    Retrieve a formatted context string for ``query``.

    Each excerpt is labelled ``[n] file | type | page | section | relevance``.
    Returns an empty string when no context is available (never raises).
    """
    return (await retrieve_context_with_sources(query, top_k)).context
