"""
Interactive check of your OpenAI API key for the AI_CAO knowledge base.

Run from the backend folder, with the project's virtual environment:
    venv\\Scripts\\python.exe scripts\\setup_openai.py

The script asks for your OpenAI API key (typing is hidden; just paste and press
Enter) and tests it with one tiny embedding request (the word "test") using the
same model the app uses. That costs a fraction of a cent.

The key is NEVER saved or printed. Add OPENAI_API_KEY to backend/.env yourself.

Exit codes: 0 key works, 1 failed, 2 invalid key format, 130 cancelled.
"""

from __future__ import annotations

import getpass
import sys
from typing import Any, Callable

from _keys import find_saved_key

EMBEDDING_MODEL = "text-embedding-3-small"  # same as app/rag/embeddings.py
EXPECTED_DIMENSIONS = 1536
KEY_PREFIX = "sk-"  # also covers project keys such as sk-proj-...
MAX_ATTEMPTS = 3


def _safe_output() -> None:
    """Never crash on a console that cannot display emoji."""
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def _load_openai() -> Any:
    import openai  # imported lazily so a missing package gives a friendly message

    return openai


def prompt_api_key(getpass_fn: Callable[[str], str] = getpass.getpass) -> str | None:
    """Ask for the key with hidden input. Returns None after too many bad tries."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        key = getpass_fn("Enter your OpenAI API key (starts with sk-): ").strip()
        if key.startswith(KEY_PREFIX) and len(key) > len(KEY_PREFIX):
            return key
        print(f"❌ An OpenAI API key must start with {KEY_PREFIX}")
        if attempt < MAX_ATTEMPTS:
            print("   Copy it again from platform.openai.com (API keys) and retry.")
    return None


def _redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def main(
    *,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    client_factory: Callable[[str], Any] | None = None,
    saved_key_fn: Callable[[], str | None] | None = None,
) -> int:
    _safe_output()
    print("Century Aqua AI: OpenAI key check")
    try:
        openai = _load_openai()
    except ImportError:
        print("❌ The 'openai' package is not installed in this Python.")
        print("   Run this script with the project's environment instead:")
        print("       venv\\Scripts\\python.exe scripts\\setup_openai.py")
        return 1

    saved = (
        saved_key_fn() if saved_key_fn else find_saved_key("OPENAI_API_KEY", KEY_PREFIX)
    )
    if saved:
        key = saved
        print("Using OPENAI_API_KEY from backend\\.env (not shown).")
    else:
        print("Tip: if you cannot paste at the prompt below, press Ctrl+C, add this line")
        print("     to backend\\.env in VS Code, and run the script again:")
        print("         OPENAI_API_KEY=sk-your_key_here")
        try:
            key = prompt_api_key(getpass_fn)
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 130
        if key is None:
            print("Too many invalid attempts.")
            return 2

    factory = client_factory or (
        lambda k: openai.OpenAI(api_key=k, max_retries=1, timeout=30.0)
    )
    print(f'Testing the key with one embedding request ("test", {EMBEDDING_MODEL})...')
    try:
        client = factory(key)
        response = client.embeddings.create(model=EMBEDDING_MODEL, input="test")
        dimensions = len(response.data[0].embedding)
    except openai.AuthenticationError:
        print("❌ OpenAI rejected the API key (invalid, revoked, or mistyped).")
        print("   Create a new key at platform.openai.com and retry.")
        return 1
    except openai.PermissionDeniedError:
        print("❌ This key is not allowed to use embeddings (permissions or region).")
        print("   Use a key with access to the embeddings endpoint.")
        return 1
    except openai.RateLimitError as exc:
        if getattr(exc, "code", None) == "insufficient_quota":
            print("❌ The key is valid but the account has no credit left.")
            print("   Add a payment method or credit at platform.openai.com (Billing).")
        else:
            print("❌ OpenAI is rate-limiting this key. Wait a minute and retry.")
        return 1
    except openai.APIConnectionError:  # includes timeouts
        print("❌ Could not reach OpenAI.")
        print("   Check your internet connection (and any VPN or firewall) and try again.")
        return 1
    except openai.APIError as exc:
        print(f"❌ OpenAI error ({type(exc).__name__}): {_redact(str(exc), key)}")
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130

    print("✅ OpenAI key works!")
    print(f"   {EMBEDDING_MODEL} returned a {dimensions}-dimension vector.")
    if dimensions != EXPECTED_DIMENSIONS:
        print(f"⚠️  Expected {EXPECTED_DIMENSIONS} dimensions; the Pinecone index would not match.")
        return 1
    print("\nNext: add this line to backend\\.env (use your real key):")
    print("      OPENAI_API_KEY=<your OpenAI API key>")
    print("This script did not save your API key anywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
