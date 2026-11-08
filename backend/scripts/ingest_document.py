"""
Ingest one PDF into the AI_CAO knowledge base (Pinecone).

Usage (run from the backend folder):
    venv\\Scripts\\python.exe scripts\\ingest_document.py PATH.pdf --type trial_data
    venv\\Scripts\\python.exe scripts\\ingest_document.py PATH.pdf --type patent --dry-run

--dry-run extracts and chunks the PDF and prints a summary without calling
OpenAI or Pinecone, so no API keys are needed.

Requires in backend/.env (or the environment) for a real run:
    OPENAI_API_KEY, PINECONE_API_KEY, PINECONE_INDEX

Exit codes: 0 success, 1 ingestion failed, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.rag.document_processor import DOCUMENT_TYPES  # noqa: E402
from app.rag.ingestion import ingest_file  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest a PDF into the AI_CAO knowledge base."
    )
    parser.add_argument("file", type=Path, help="Path to the PDF file.")
    parser.add_argument(
        "--type",
        dest="document_type",
        required=True,
        choices=DOCUMENT_TYPES,
        help="Document type stored in each chunk's metadata.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and chunk only; do not call OpenAI or Pinecone.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.file.is_file():
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2
    if args.file.suffix.lower() != ".pdf":
        print("error: only .pdf files are supported.", file=sys.stderr)
        return 2

    print(f"Ingesting {args.file.name} as '{args.document_type}'" + (" (dry run)" if args.dry_run else ""))
    try:
        result = asyncio.run(
            ingest_file(args.file, args.document_type, dry_run=args.dry_run)
        )
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    action = "would store" if args.dry_run else "stored"
    print(f"OK: {action} {result.chunks} chunks from {result.pages} page(s) of {result.source_file}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
