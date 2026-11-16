"""
ingest_to_rag: put ONE public web page into the knowledge base (the COO's knowledge queue, Phase 1).

``ingest_to_rag(title, text, source_url)`` cleans the text, embeds it and saves it to the vector database. It is NOT offered to any model: only the
COO's ``manage_fetch_queue`` (app/agents/exec_tools.py) calls it, with text that came from the search tool, so a model can never write free text of
its own choosing into the knowledge base every executive reads.

DEDUPLICATION (the same URL is never ingested twice). The URL is normalised to a key (lower-case host, no ``www.``, no fragment, no tracking
parameters, sorted query, no trailing slash), and the key is CLAIMED with one atomic ``INSERT ... ON CONFLICT DO NOTHING`` on the UNIQUE
IngestedSource.urlKey. Only the caller that wins the claim embeds anything, so two runs at once, or a retry, cannot double-ingest. A failure after the
claim releases it, so a page that failed can be tried again. The check happens BEFORE any money is spent on embeddings.

LABELLING. Every chunk is stored as document_type ``auto_sourced_literature`` (source_api ``web_search``), so retrieval shows it as PUBLIC LITERATURE,
not Century Aqua data, and the executives' prompts already say never to present it as Century Aqua's own result. It carries no publication year.

SAFETY. Only http(s) URLs with a real public-looking host (no credentials, no IP literal, no localhost) are accepted; the page is never fetched by us,
so there is nothing to redirect. Text is stripped of control characters, secret-looking strings are masked, short pages are rejected, long ones cut.
Retrieved text reaches the models only inside the existing <<<EXCERPTS delimiters, which they are told to treat as data, never instructions.
What this does NOT do: it cannot tell that a page is TRUE or relevant. A person decides what to ask for; the label says it is public web text.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

from app.agents.execution_plan import redact_secrets
from app.governance import db
from app.rag import document_processor, embeddings, vector_db

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200
MAX_TEXT_CHARS = 40_000
MAX_TITLE_CHARS = 200
MAX_URL_CHARS = 500
SOURCE_API = "web_search"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TRACKING = re.compile(r"(?i)^(utm_.*|gclid|fbclid|msclkid|mc_cid|mc_eid|ref|ref_src|source|igshid|_hsenc|_hsmi|yclid)$")


@dataclass
class IngestOutcome:
    status: str  # ingested | duplicate | rejected | failed
    reason: str = ""
    chunks: int = 0
    url_key: str = ""


def url_key(url: str) -> str:
    """The identity of a page, or ValueError. Two spellings of the same page give the same key."""
    if not isinstance(url, str) or not url.strip() or len(url.strip()) > MAX_URL_CHARS:
        raise ValueError("the URL is missing or too long")
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("only http and https page addresses are accepted")
    if parts.username or parts.password:
        raise ValueError("a URL with credentials in it is refused")
    if host == "localhost" or "." not in host:
        raise ValueError("the host is not a public website")
    try:
        ipaddress.ip_address(host)
        raise ValueError("an IP address is not accepted as a source")
    except ValueError as exc:
        if "IP address" in str(exc):
            raise
    host = host[4:] if host.startswith("www.") else host
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)))
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or ""
    return f"{netloc}{path}" + (f"?{query}" if query else "")


def clean_text(text: str) -> str:
    """The page text as it will be stored: no control characters, secrets masked, whitespace collapsed, cut to MAX_TEXT_CHARS."""
    if not isinstance(text, str):
        return ""
    lines = (" ".join(line.split()) for line in _CONTROL.sub(" ", text).splitlines())
    return redact_secrets("\n".join(line for line in lines if line))[:MAX_TEXT_CHARS].strip()


def _claim_sync(key: str, url: str, title: str, request_id: int | None) -> bool:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('INSERT INTO "IngestedSource" ("urlKey","url","title","chunks","fetchRequestId") VALUES (%s,%s,%s,0,%s) '
                    'ON CONFLICT ("urlKey") DO NOTHING RETURNING "id"', (key, url, title, request_id))
        return bool(db.fetch_all(cur))


def _finish_sync(key: str, chunks: int, release: bool) -> None:
    with db.transaction() as conn:
        cur = conn.cursor()
        if release:
            cur.execute('DELETE FROM "IngestedSource" WHERE "urlKey" = %s AND "chunks" = 0', (key,))
        else:
            cur.execute('UPDATE "IngestedSource" SET "chunks" = %s WHERE "urlKey" = %s', (chunks, key))


def _known_sync(key: str) -> bool:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM "IngestedSource" WHERE "urlKey" = %s', (key,))
        return bool(db.fetch_all(cur))


async def already_ingested(source_url: str) -> bool:
    """Is this page already in the knowledge base? False for an unusable URL or when the database is not configured. Never raises."""
    try:
        return db.is_configured() and await db.run(_known_sync, url_key(source_url))
    except Exception:
        return False


def build_chunks(title: str, text: str, source_url: str, key: str) -> list[dict]:
    """The vector records for one page. Ids come from the URL key, so the same page always maps to the same ids."""
    doc_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()
    pieces = [c for c in document_processor.chunk_text(text) if len(c.strip()) >= document_processor.MIN_CHUNK_CHARS]
    return [{"document_id": doc_id, "chunk_id": f"web-{doc_id}-{i}", "source_file": source_url, "page_number": 1, "section_heading": "",
             "date_ingested": now, "document_type": document_processor.AUTO_SOURCED_TYPE, "text": piece,
             "source_api": SOURCE_API, "paper_title": title, "source_url": source_url} for i, piece in enumerate(pieces)]


async def ingest_to_rag(title: str, text: str, source_url: str, *, fetch_request_id: int | None = None) -> IngestOutcome:
    """Ingest one public page. Returns an IngestOutcome and NEVER raises (a problem is a 'rejected' or 'failed' outcome with a plain reason)."""
    try:
        try:
            key = url_key(source_url)
        except ValueError as exc:
            return IngestOutcome("rejected", str(exc))
        title = " ".join(str(title or "").split())[:MAX_TITLE_CHARS]
        body = clean_text(text)
        if not title:
            return IngestOutcome("rejected", "the page has no title", url_key=key)
        if len(body) < MIN_TEXT_CHARS:
            return IngestOutcome("rejected", f"too little text to be useful (under {MIN_TEXT_CHARS} characters)", url_key=key)
        if not db.is_configured():
            return IngestOutcome("failed", "the database is not configured, so duplicates cannot be checked", url_key=key)
        if not embeddings.is_configured() or not vector_db.is_configured():
            return IngestOutcome("failed", "OPENAI_API_KEY, PINECONE_API_KEY and PINECONE_INDEX must all be set", url_key=key)
        chunks = build_chunks(title, body, source_url.strip(), key)
        if not chunks:
            return IngestOutcome("rejected", "no usable text after cleaning", url_key=key)
        if not await db.run(_claim_sync, key, source_url.strip()[:MAX_URL_CHARS], title, fetch_request_id):
            return IngestOutcome("duplicate", "this page is already in the knowledge base", url_key=key)
        try:
            vectors = await embeddings.generate_embeddings_batch([c["text"] for c in chunks])
            stored = await vector_db.upsert_chunks([{**c, "embedding": v} for c, v in zip(chunks, vectors)])
        except Exception as exc:  # release the claim so the page can be tried again; say only the type, never request text
            await db.run(_finish_sync, key, 0, True)
            return IngestOutcome("failed", f"could not store it ({type(exc).__name__})", url_key=key)
        await db.run(_finish_sync, key, stored, False)
        return IngestOutcome("ingested", "", stored, key)
    except Exception as exc:
        logger.error("web_ingest: unexpected failure (%s)", type(exc).__name__)
        return IngestOutcome("failed", f"unexpected error ({type(exc).__name__})")
