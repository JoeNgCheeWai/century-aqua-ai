"""
Document processing for the AI_CAO RAG pipeline.

Pipeline: PDF -> per-page text (PyMuPDF, pdfplumber fallback) -> structure-aware
chunking (~500 tokens, ~50 token overlap) -> chunk dictionaries with metadata.

Design notes
------------
* "Semantic" chunking here means structure-aware, not embedding-based: text is
  split into paragraphs and sentences, chunks are packed from whole sentences,
  and a new chunk always starts at a detected section heading. No model calls
  are needed, so processing is free and works offline.
* Token counts are ESTIMATED (1 token ~ 0.75 words) to avoid a tokenizer
  dependency. Tables and numeric text tokenize denser than prose, so real
  token counts can run above the estimate. Chunks stay far below the embedding
  model's input limit.
* Chunks never span pages, so every chunk has an exact page number. A sentence
  that runs across a page break is split at the break.
* Scanned PDFs (images without a text layer) are NOT supported: there is no OCR.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DOCUMENT_TYPES: tuple[str, ...] = (
    "trial_data",
    "patent",
    "regulation",
    "market_report",
    "other",
)

# DOCUMENT_TYPES is what the manual ingest CLIs accept (our own documents).
# "auto_sourced_literature" is reserved for scripts/auto_fetch_literature.py so
# public papers can never be mislabelled as, or confused with, internal data.
AUTO_SOURCED_TYPE = "auto_sourced_literature"
ALL_DOCUMENT_TYPES: tuple[str, ...] = DOCUMENT_TYPES + (AUTO_SOURCED_TYPE,)
AUTO_SOURCE_APIS: tuple[str, ...] = ("crossref", "pubmed")

_RESERVED_METADATA_KEYS = frozenset(
    {
        "document_id", "chunk_id", "source_file", "page_number",
        "section_heading", "date_ingested", "document_type", "text", "embedding",
    }
)

DEFAULT_CHUNK_SIZE = 500  # tokens (estimated)
DEFAULT_OVERLAP = 50  # tokens (estimated)
MIN_CHUNK_CHARS = 20  # drops page numbers, stray fragments, etc.

_WORDS_PER_TOKEN = 0.75


class DocumentProcessingError(Exception):
    """Raised when a document cannot be read or yields no usable text."""


# --------------------------------------------------------------------------- #
# Token estimate and heading detection
# --------------------------------------------------------------------------- #
def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` (about 0.75 words per token)."""
    words = len(text.split())
    return math.ceil(words / _WORDS_PER_TOKEN) if words else 0


_NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+[A-Za-z]")
_HEADING_KEYWORDS = frozenset(
    {
        "abstract",
        "introduction",
        "background",
        "objectives",
        "methods",
        "materials and methods",
        "results",
        "discussion",
        "conclusion",
        "conclusions",
        "summary",
        "executive summary",
        "references",
        "claims",
        "description",
    }
)


def _is_heading(line: str) -> bool:
    """Heuristic: is this single line a section heading?"""
    s = line.strip()
    if not s or len(s) > 100:
        return False
    words = len(s.split())
    if s.lower().rstrip(":") in _HEADING_KEYWORDS:
        return True
    if (
        _NUMBERED_HEADING.match(s)
        and words <= 10
        and not s.endswith((".", ",", ";"))
    ):
        return True
    if (
        s.isupper()
        and any(c.isalpha() for c in s)
        and len(s) >= 4
        and 1 <= words <= 12
    ):
        return True
    return False


# --------------------------------------------------------------------------- #
# PDF extraction
# --------------------------------------------------------------------------- #
def _clean_page_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\u00ad", "").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return text.strip()


def _extract_with_pymupdf(path: Path) -> list[dict[str, Any]]:
    try:
        import pymupdf  # PyMuPDF (also importable as ``fitz``)

        pages: list[dict[str, Any]] = []
        with pymupdf.open(str(path)) as doc:
            for number, page in enumerate(doc, start=1):
                text = _clean_page_text(page.get_text("text"))
                if text:
                    pages.append({"page_number": number, "text": text})
        return pages
    except Exception as exc:  # corrupt/encrypted file, missing package, ...
        logger.warning("PyMuPDF extraction failed (%s)", type(exc).__name__)
        return []


def _extract_with_pdfplumber(path: Path) -> list[dict[str, Any]]:
    try:
        import pdfplumber

        pages: list[dict[str, Any]] = []
        with pdfplumber.open(str(path)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                text = _clean_page_text(page.extract_text() or "")
                if text:
                    pages.append({"page_number": number, "text": text})
        return pages
    except Exception as exc:
        logger.warning("pdfplumber extraction failed (%s)", type(exc).__name__)
        return []


def extract_text_from_pdf(file_path: str | Path) -> list[dict[str, Any]]:
    """
    Extract text from a PDF, page by page.

    Uses PyMuPDF first and falls back to pdfplumber if PyMuPDF fails or finds
    no text.

    Returns:
        A list of ``{"page_number": int (1-based), "text": str}``. Pages with
        no text are omitted.

    Raises:
        FileNotFoundError: the file does not exist.
        DocumentProcessingError: neither library found any text (for example a
            scanned PDF, which would need OCR, or an encrypted file).
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    pages = _extract_with_pymupdf(path)
    if not pages:
        logger.info("Falling back to pdfplumber for %s", path.name)
        pages = _extract_with_pdfplumber(path)
    if not pages:
        raise DocumentProcessingError(
            f"No extractable text in {path.name}. It may be a scanned PDF "
            "(OCR is not supported), encrypted, or corrupt."
        )
    return pages


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def _split_sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(paragraph) if s.strip()]


def _hard_split(sentence: str, chunk_size: int) -> list[str]:
    """Split a single over-long 'sentence' into word windows that fit a chunk."""
    words = sentence.split()
    max_words = max(1, int(chunk_size * _WORDS_PER_TOKEN))
    return [
        " ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)
    ]


def _parse_units(
    text: str, heading: str, chunk_size: int
) -> tuple[list[tuple[str, str]], str]:
    """
    Turn raw page text into ``(heading, sentence)`` units.

    Returns the units and the heading in force at the end of the text, so the
    next page can continue under the same heading.
    """
    units: list[tuple[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        paragraph = " ".join(buffer)
        buffer.clear()
        for sentence in _split_sentences(paragraph):
            if estimate_tokens(sentence) > chunk_size:
                pieces = _hard_split(sentence, chunk_size)
            else:
                pieces = [sentence]
            for piece in pieces:
                units.append((heading, piece))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if _is_heading(line):
            flush()
            heading = line.rstrip(":").strip()
            continue
        buffer.append(line)
    flush()
    return units, heading


def _overlap_tail(sentences: list[str], overlap: int) -> list[str]:
    """Trailing whole sentences totalling at most ``overlap`` tokens."""
    if overlap <= 0 or not sentences:
        return []
    tail: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        tokens = estimate_tokens(sentence)
        if total + tokens > overlap:
            break
        tail.insert(0, sentence)
        total += tokens
    if not tail:
        # The last sentence alone is longer than the overlap: keep its tail words.
        words = sentences[-1].split()
        keep = max(1, int(overlap * _WORDS_PER_TOKEN))
        tail = [" ".join(words[-keep:])]
    return tail


def _pack_units(
    units: list[tuple[str, str]], chunk_size: int, overlap: int
) -> list[tuple[str, str]]:
    """Pack sentences into ``(heading, chunk_text)`` pairs."""
    chunks: list[tuple[str, str]] = []
    current: list[str] = []
    tokens = 0
    fresh = 0  # sentences added since the last chunk was emitted (excludes overlap)
    chunk_heading = units[0][0] if units else ""

    for heading, sentence in units:
        sentence_tokens = estimate_tokens(sentence)
        if heading != chunk_heading:
            # A new section starts a new chunk, with no overlap across sections.
            if fresh:
                chunks.append((chunk_heading, " ".join(current)))
            current, tokens, fresh = [], 0, 0
            chunk_heading = heading
        elif fresh and tokens + sentence_tokens > chunk_size:
            chunks.append((chunk_heading, " ".join(current)))
            current = _overlap_tail(current, overlap)
            tokens = estimate_tokens(" ".join(current))
            if tokens + sentence_tokens > chunk_size:
                # Never let carried-over overlap push a chunk past chunk_size.
                current, tokens = [], 0
            fresh = 0
        current.append(sentence)
        tokens += sentence_tokens
        fresh += 1

    if fresh:
        chunks.append((chunk_heading, " ".join(current)))
    return chunks


def _validate_chunk_params(chunk_size: int, overlap: int) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")


def _chunk_with_headings(
    text: str, chunk_size: int, overlap: int, heading: str
) -> tuple[list[tuple[str, str]], str]:
    units, final_heading = _parse_units(text, heading, chunk_size)
    return _pack_units(units, chunk_size, overlap), final_heading


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """
    Split ``text`` into chunks of roughly ``chunk_size`` tokens.

    Chunks are built from whole sentences, start a new chunk at each detected
    section heading, and repeat roughly ``overlap`` tokens (whole trailing
    sentences) from the previous chunk within the same section. If the last
    sentence alone is longer than ``overlap``, only its trailing words are
    repeated, so such a chunk can start mid-sentence. The overlap is dropped
    when carrying it over would push the next chunk past ``chunk_size``. A
    single sentence longer than ``chunk_size`` is split into word windows.

    Token counts are estimates (see module docstring).

    Raises:
        ValueError: ``chunk_size`` <= 0, or ``overlap`` negative or >= chunk_size.
    """
    _validate_chunk_params(chunk_size, overlap)
    chunks, _ = _chunk_with_headings(text, chunk_size, overlap, "")
    return [chunk for _, chunk in chunks]



# --------------------------------------------------------------------------- #
# Full document processing
# --------------------------------------------------------------------------- #
def _document_id(path: Path) -> str:
    """Stable id from the file's bytes: re-ingesting the same file overwrites."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _validate_extra_metadata(
    document_type: str, extra: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Check and normalise caller-supplied chunk metadata (scalars only)."""
    cleaned: dict[str, Any] = {}
    for key, value in (extra or {}).items():
        if key in _RESERVED_METADATA_KEYS:
            raise ValueError(f"extra_metadata may not override {key!r}")
        if value is None:
            value = ""
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"extra_metadata[{key!r}] must be a string, number or bool")
        cleaned[key] = value

    if document_type == AUTO_SOURCED_TYPE:
        if cleaned.get("source_api") not in AUTO_SOURCE_APIS:
            raise ValueError(
                f"auto_sourced_literature needs source_api in {AUTO_SOURCE_APIS}"
            )
        title = cleaned.get("paper_title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("auto_sourced_literature needs a non-empty paper_title")
        year = cleaned.get("year")
        this_year = datetime.now(timezone.utc).year
        if isinstance(year, bool) or not isinstance(year, int) or not 1800 <= year <= this_year + 1:
            raise ValueError("auto_sourced_literature needs an integer publication year")
    return cleaned


def process_document(
    file_path: str | Path,
    document_type: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    extra_metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Extract, chunk and annotate a PDF.

    ``extra_metadata`` (optional scalar fields) is copied onto every chunk and
    cannot override the core fields. For document_type ``auto_sourced_literature``
    it MUST include ``source_api`` ("crossref" or "pubmed"), ``paper_title`` and
    an integer ``year``, so public literature is always identifiable.

    Each returned chunk dictionary has:
        document_id, chunk_id, source_file, page_number, section_heading,
        date_ingested (UTC ISO-8601), document_type, text.

    ``document_id`` is derived from the file contents and ``chunk_id`` from the
    document id and chunk position, so re-ingesting an unchanged file produces
    the same ids and overwrites rather than duplicates. A changed file gets a
    new document id; its old chunks are NOT removed automatically.

    Raises:
        ValueError: unknown ``document_type`` or invalid chunk parameters.
        FileNotFoundError: the file does not exist.
        DocumentProcessingError: no extractable text, or no usable chunks.
    """
    if document_type not in ALL_DOCUMENT_TYPES:
        raise ValueError(
            f"Unknown document_type {document_type!r}. "
            f"Expected one of: {', '.join(ALL_DOCUMENT_TYPES)}"
        )
    _validate_chunk_params(chunk_size, overlap)
    extra = _validate_extra_metadata(document_type, extra_metadata)

    path = Path(file_path)
    pages = extract_text_from_pdf(path)  # raises FileNotFoundError / ProcessingError
    document_id = _document_id(path)
    date_ingested = datetime.now(timezone.utc).isoformat(timespec="seconds")

    chunks: list[dict[str, Any]] = []
    heading = ""
    index = 0
    for page in pages:
        page_chunks, heading = _chunk_with_headings(
            page["text"], chunk_size, overlap, heading
        )
        for chunk_heading, text in page_chunks:
            if len(text) < MIN_CHUNK_CHARS:
                continue
            chunks.append(
                {
                    **extra,
                    "document_id": document_id,
                    "chunk_id": f"{document_id}-{index:05d}",
                    "source_file": path.name,
                    "page_number": page["page_number"],
                    "section_heading": chunk_heading,
                    "date_ingested": date_ingested,
                    "document_type": document_type,
                    "text": text,
                }
            )
            index += 1

    if not chunks:
        raise DocumentProcessingError(f"No usable text chunks in {path.name}.")
    return chunks

