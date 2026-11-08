"""
Helper shared by the setup scripts: find an API key that is already saved.

Looks in the environment first, then in backend/.env. The key is only returned
to the caller; nothing here ever prints it.
"""

from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def find_saved_key(
    var_name: str, prefix: str, env_file: Path | None = None
) -> str | None:
    """
    Return the saved value of ``var_name`` if it looks like a real key.

    Quotes and surrounding spaces are stripped. Returns None when the variable
    is missing, empty, or does not start with ``prefix`` (for example the
    placeholder text from .env.example).
    """
    value = os.environ.get(var_name, "")
    if not value:
        try:
            from dotenv import dotenv_values

            value = dotenv_values(env_file or BACKEND_DIR / ".env").get(var_name) or ""
        except Exception:
            value = ""
    value = value.strip().strip('"').strip("'").strip()
    if value.startswith(prefix) and len(value) > len(prefix):
        return value
    return None
