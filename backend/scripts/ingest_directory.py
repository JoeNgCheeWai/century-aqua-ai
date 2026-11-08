"""
Ingest every PDF in a directory into the AI_CAO knowledge base (Pinecone).

Usage (run from the backend folder):
    venv\\Scripts\\python.exe scripts\\ingest_directory.py DIR --type market_report
    venv\\Scripts\\python.exe scripts\\ingest_directory.py DIR --type trial_data --recursive --dry-run

All files in one run get the same document type. Files that fail are reported
and skipped; the rest continue.

Requires in backend/.env (or the environment) for a real run:
    OPENAI_API_KEY, PINECONE_API_KEY, PINECONE_INDEX

Exit codes: 0 all succeeded, 1 at least one file failed (or no PDFs found),
2 bad arguments.
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


def find_pdfs(directory: Path, recursive: bool) -> list[Path]:
    """PDFs in ``directory`` (case-insensitive extension), sorted by path."""
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in directory.glob(pattern) if p.is_file() and p.suffix.lower() == ".pdf"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest all PDFs in a directory into the AI_CAO knowledge base."
    )
    parser.add_argument("directory", type=Path, help="Directory containing PDFs.")
    parser.add_argument(
        "--type",
        dest="document_type",
        required=True,
        choices=DOCUMENT_TYPES,
        help="Document type applied to every file.",
    )
    parser.add_argument(
        "--recursive", action="store_true", help="Include PDFs in subdirectories."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and chunk only; do not call OpenAI or Pinecone.",
    )
    return parser


async def run(files: list[Path], document_type: str, dry_run: bool) -> tuple[int, list[str]]:
    total_chunks = 0
    failures: list[str] = []
    for number, path in enumerate(files, start=1):
        print(f"[{number}/{len(files)}] {path.name}")
        try:
            result = await ingest_file(path, document_type, dry_run=dry_run)
            total_chunks += result.chunks
        except Exception as exc:
            message = f"{path.name}: {type(exc).__name__}: {exc}"
            print(f"  FAILED: {message}", file=sys.stderr)
            failures.append(message)
    return total_chunks, failures


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.directory.is_dir():
        print(f"error: not a directory: {args.directory}", file=sys.stderr)
        return 2

    files = find_pdfs(args.directory, args.recursive)
    if not files:
        print(f"No PDF files found in {args.directory}.", file=sys.stderr)
        return 1

    print(f"Found {len(files)} PDF(s) in {args.directory}; type '{args.document_type}'" + (" (dry run)" if args.dry_run else ""))
    total_chunks, failures = asyncio.run(run(files, args.document_type, args.dry_run))

    succeeded = len(files) - len(failures)
    verb = "would store" if args.dry_run else "stored"
    print(f"\nSummary: {succeeded}/{len(files)} file(s) ok, {verb} {total_chunks} chunks.")
    if failures:
        print("Failed files:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
