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
CAO_KEY_HEADER = "X-Internal-CAO-Key"
# The COO has its OWN key: the CAO key does not open /api/coo and the COO key does not open /api/cao.
COO_KEY_ENV = "INTERNAL_COO_KEY"
COO_KEY_HEADER = "X-Internal-COO-Key"
# Same for the CTO (Phase 3): its own key, its own header.
CTO_KEY_ENV = "INTERNAL_CTO_KEY"
CTO_KEY_HEADER = "X-Internal-CTO-Key"
# And for the CMO (Phase 4): its own key, its own header.
CMO_KEY_ENV = "INTERNAL_CMO_KEY"
CMO_KEY_HEADER = "X-Internal-CMO-Key"
_ACTOR = re.compile(r"^[A-Za-z0-9_.:@+\-]{1,100}$")


def require_internal_key(provided: str | None, key_env: str = KEY_ENV, header: str = CAO_KEY_HEADER) -> None:
    """
    Raise 503 if the server has no key, 401 if the caller's key is missing or wrong.

    The defaults are the CAO's key and header, so every existing caller is unchanged.
    """
    expected = os.getenv(key_env, "")
    if not expected:
        raise HTTPException(status_code=503, detail=f"Endpoint disabled: {key_env} is not configured.")
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail=f"Invalid or missing {header} header.")


def require_coo_key(provided: str | None) -> None:
    """The COO endpoint's gate: INTERNAL_COO_KEY in the X-Internal-COO-Key header. Fails closed (503) if unset."""
    require_internal_key(provided, COO_KEY_ENV, COO_KEY_HEADER)


def require_cto_key(provided: str | None) -> None:
    """The CTO endpoint's gate: INTERNAL_CTO_KEY in the X-Internal-CTO-Key header. Fails closed (503) if unset."""
    require_internal_key(provided, CTO_KEY_ENV, CTO_KEY_HEADER)


def require_cmo_key(provided: str | None) -> None:
    """The CMO endpoint's gate: INTERNAL_CMO_KEY in the X-Internal-CMO-Key header. Fails closed (503) if unset."""
    require_internal_key(provided, CMO_KEY_ENV, CMO_KEY_HEADER)


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
