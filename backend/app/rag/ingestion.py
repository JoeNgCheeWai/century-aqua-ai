"""
End-to-end ingestion used by the CLI scripts in backend/scripts/.

process -> embed -> upsert. Unlike retrieval, ingestion fails loudly: the
operator needs to know when a document was not stored.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.rag import embeddings, vector_db
from app.rag.document_processor import process_document
from app.rag.embeddings import generate_embeddings_batch
from app.rag.vector_db import init_pinecone, upsert_chunks


@dataclass
class IngestResult:
    source_file: str
    document_id: str
    pages: int
    chunks: int
    upserted: int  # 0 for a dry run


async def ingest_file(
    file_path: str | Path,
    document_type: str,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
    extra_metadata: Mapping[str, Any] | None = None,
) -> IngestResult:
    """
    Ingest one PDF into the knowledge base.

    ``extra_metadata`` is stored on every chunk (see ``process_document``).

    With ``dry_run=True`` the PDF is extracted and chunked and a summary is
    logged, but OpenAI and Pinecone are never called (no keys needed).

    Raises:
        FileNotFoundError, ValueError, DocumentProcessingError, EmbeddingError,
        VectorDBError: see the underlying modules.
    """
    path = Path(file_path)

    if not dry_run:
        # Fail fast, before spending time and money on embeddings.
        if not embeddings.is_configured():
            raise embeddings.EmbeddingError("OPENAI_API_KEY is not configured.")
        if not vector_db.is_configured():
            raise vector_db.VectorDBError(
                "PINECONE_API_KEY and PINECONE_INDEX must both be set."
            )
        log("  connecting to Pinecone ...")
        await asyncio.to_thread(init_pinecone)

    log(f"  extracting and chunking {path.name} ...")
    chunks = await asyncio.to_thread(
        process_document, path, document_type, extra_metadata=extra_metadata
    )
    pages = len({chunk["page_number"] for chunk in chunks})
    document_id = chunks[0]["document_id"]
    log(f"  {len(chunks)} chunks from {pages} page(s) with text (document_id {document_id})")

    if dry_run:
        preview = chunks[0]["text"][:200].replace("\n", " ")
        log(f"  dry run: nothing sent. First chunk preview: {preview}")
        return IngestResult(path.name, document_id, pages, len(chunks), 0)

    log(f"  generating embeddings for {len(chunks)} chunks ...")
    vectors = await generate_embeddings_batch([chunk["text"] for chunk in chunks])
    embedded = [{**chunk, "embedding": vector} for chunk, vector in zip(chunks, vectors)]

    log("  upserting to Pinecone ...")
    upserted = await upsert_chunks(embedded)
    log(f"  done: {upserted} vectors stored.")
    return IngestResult(path.name, document_id, pages, len(chunks), upserted)
