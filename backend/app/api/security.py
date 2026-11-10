"""
Shared access checks for the internal executive-agent endpoints.

Both the cross-check and the kill switch are gated by INTERNAL_CAO_KEY (sent in
the X-Internal-CAO-Key header), exactly like /api/cao. Fail closed: if the key
is not configured on the server the endpoints answer 503 and never run.

A shared key identifies the CALLER SYSTEM, not a person. To record WHO acted, the
trusted caller (the Next.js server, after its own staff check) sends the person's
id in the X-Admin-Id header (kill switch) or X-User-Id header (agent queries).
Those headers are only as trustworthy as the key that accompanies them: anyone
holding the key can claim any id. That is a known limit of a shared-key design,
documented in PROJECT_CONTEXT.md, and the reason the key must stay server-side.
"""

from __future__ import annotations

import os
import re
import secrets

from fastapi import HTTPException

KEY_ENV = "INTERNAL_CAO_KEY"
_ACTOR = re.compile(r"^[A-Za-z0-9_.:@+\-]{1,100}$")


def require_internal_key(provided: str | None) -> None:
    """Raise 503 if the server has no key, 401 if the caller's key is missing or wrong."""
    expected = os.getenv(KEY_ENV, "")
    if not expected:
        raise HTTPException(status_code=503, detail=f"Endpoint disabled: {KEY_ENV} is not configured.")
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-CAO-Key header.")


def require_actor(value: str | None, header: str, *, required: bool) -> str:
    """
    Validate an actor id header. Required for actions that change state (the
    kill switch must say who did it); optional for queries (falls back to 'unknown').
    """
    if not value:
        if required:
            raise HTTPException(status_code=400, detail=f"{header} header is required so the action can be attributed.")
        return "unknown"
    if not _ACTOR.match(value):
        raise HTTPException(status_code=400, detail=f"{header} header has an invalid format.")
    return value
