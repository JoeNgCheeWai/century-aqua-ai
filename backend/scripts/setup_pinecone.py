"""
Interactive setup for the AI_CAO knowledge-base index on Pinecone.

Run from the backend folder, with the project's virtual environment:
    venv\\Scripts\\python.exe scripts\\setup_pinecone.py

No arguments: the script asks for your Pinecone API key (typing is hidden, so
nothing is echoed; just paste and press Enter), then creates the index if it
does not already exist. Safe to run again.

The API key is NEVER saved or printed. Add it to backend/.env yourself.

Index: century-aqua-rag, 1536 dimensions (OpenAI text-embedding-3-small),
cosine metric, serverless on AWS us-east-1.

Exit codes: 0 index ready, 1 failed, 2 invalid key format, 130 cancelled.
"""

from __future__ import annotations

import getpass
import sys
from typing import Any, Callable

from _keys import find_saved_key

INDEX_NAME = "century-aqua-rag"
DIMENSION = 1536  # must match OpenAI text-embedding-3-small (app/rag/embeddings.py)
METRIC = "cosine"
CLOUD = "aws"
REGION = "us-east-1"
KEY_PREFIX = "pcsk_"
MAX_ATTEMPTS = 3

NEXT_STEPS = f"""
Next steps:
  1. Open backend\\.env and add these lines (use your real keys):
         PINECONE_API_KEY=<your Pinecone API key>
         PINECONE_INDEX={INDEX_NAME}
         OPENAI_API_KEY=<your OpenAI API key>
  2. Check your OpenAI key:
         venv\\Scripts\\python.exe scripts\\setup_openai.py
  3. Preview a document without sending anything:
         venv\\Scripts\\python.exe scripts\\ingest_document.py FILE.pdf --type trial_data --dry-run
  4. Ingest it for real (the same command without --dry-run).
  5. Add the same three variables in the Render dashboard.
This script did not save your API key anywhere."""


def _safe_output() -> None:
    """Never crash on a console that cannot display emoji."""
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def _load_pinecone() -> Any:
    import pinecone  # imported lazily so a missing package gives a friendly message

    return pinecone


def prompt_api_key(getpass_fn: Callable[[str], str] = getpass.getpass) -> str | None:
    """Ask for the key with hidden input. Returns None after too many bad tries."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        key = getpass_fn("Enter your Pinecone API key (starts with pcsk_): ").strip()
        if key.startswith(KEY_PREFIX) and len(key) > len(KEY_PREFIX):
            return key
        print(f"❌ A Pinecone API key must start with {KEY_PREFIX}")
        if attempt < MAX_ATTEMPTS:
            print("   Copy it again from the Pinecone console (API Keys) and retry.")
    return None


def _redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def ensure_index(pc: Any, pinecone: Any) -> str:
    """Create the index if needed. Returns 'exists' or 'created'."""
    if INDEX_NAME in pc.list_indexes().names():
        return "exists"
    print("Creating index... (this can take up to a minute)")
    try:
        pc.create_index(
            name=INDEX_NAME,
            dimension=DIMENSION,
            metric=METRIC,
            spec=pinecone.ServerlessSpec(cloud=CLOUD, region=REGION),
        )
    except pinecone.ConflictError:
        return "exists"  # created by someone else in the meantime
    return "created"


def _existing_dimension_ok(pc: Any) -> bool:
    """An existing index with the wrong size would fail later, so say so now."""
    try:
        dimension = getattr(pc.describe_index(INDEX_NAME), "dimension", None)
    except Exception:
        return True  # cannot check; do not block
    if dimension is not None and dimension != DIMENSION:
        print(
            f"❌ Index '{INDEX_NAME}' exists but has {dimension} dimensions; "
            f"this project needs {DIMENSION}."
        )
        print("   Delete it in the Pinecone console and run this script again.")
        return False
    return True


def main(
    *,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    client_factory: Callable[[str], Any] | None = None,
    saved_key_fn: Callable[[], str | None] | None = None,
) -> int:
    _safe_output()
    print("Century Aqua AI: Pinecone setup")
    try:
        pinecone = _load_pinecone()
    except ImportError:
        print("❌ The 'pinecone' package is not installed in this Python.")
        print("   Run this script with the project's environment instead:")
        print("       venv\\Scripts\\python.exe scripts\\setup_pinecone.py")
        return 1

    saved = (
        saved_key_fn() if saved_key_fn else find_saved_key("PINECONE_API_KEY", KEY_PREFIX)
    )
    if saved:
        key = saved
        print("Using PINECONE_API_KEY from backend\\.env (not shown).")
    else:
        print("Tip: if you cannot paste at the prompt below, press Ctrl+C, add this line")
        print("     to backend\\.env in VS Code, and run the script again:")
        print("         PINECONE_API_KEY=pcsk_your_key_here")
        try:
            key = prompt_api_key(getpass_fn)
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 130
        if key is None:
            print("Too many invalid attempts. Nothing was changed.")
            return 2

    factory = client_factory or (lambda k: pinecone.Pinecone(api_key=k))
    try:
        pc = factory(key)
        result = ensure_index(pc, pinecone)
        if result == "exists":
            if not _existing_dimension_ok(pc):
                return 1
            print(f"✅ Index '{INDEX_NAME}' already exists. Nothing to do.")
            print(f"   Make sure backend\\.env has PINECONE_INDEX={INDEX_NAME}")
            return 0
    except pinecone.UnauthorizedError:
        print("❌ Pinecone rejected the API key (invalid or revoked).")
        print("   Create or copy a key from the Pinecone console (API Keys) and retry.")
        return 1
    except pinecone.PaymentRequiredError:
        print("❌ Pinecone says this plan cannot create the index (billing or plan limit).")
        print("   Check your plan in the Pinecone console.")
        return 1
    except pinecone.ForbiddenError:
        print("❌ The key is not allowed to do this (permissions or plan limit).")
        print("   Use a key with full access, or check your project's index limit.")
        return 1
    except (pinecone.PineconeConnectionError, pinecone.PineconeTimeoutError):
        print("❌ Could not reach Pinecone.")
        print("   Check your internet connection (and any VPN or firewall) and try again.")
        return 1
    except pinecone.PineconeError as exc:
        print(f"❌ Pinecone error ({type(exc).__name__}): {_redact(str(exc), key)}")
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130

    print("✅ Index created successfully!")
    print(f"   {INDEX_NAME}: {DIMENSION} dimensions, {METRIC}, {CLOUD} {REGION}")
    print(NEXT_STEPS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

