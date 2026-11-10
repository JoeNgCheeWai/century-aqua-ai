"""
GET /api/council/sessions: the list of council sessions (Phase 6, Mission Control). Internal only.

Summary rows from CouncilSessionLog, newest first. The full result of one session (options, red team, turns, ...) is still
GET /api/council/{session_id} (Phase 5); this list is for browsing and for the dashboard.

ROUTE ORDER MATTERS. ``/api/council/sessions`` would otherwise be read as ``/api/council/{session_id}`` with the id
"sessions". main.py therefore registers THIS router before the council router, and a test pins it.

KEY. INTERNAL_COUNCIL_KEY in X-Internal-Council-Key, checked before anything else (the council key is also the admin key).

QUERY PARAMETERS  status (RUNNING | RESOLVED | ESCALATED_TO_CEO | NEEDS_CEO_INPUT | FAILED), limit 1-100 (default 20), offset.
RETURNS {items, total, limit, offset}. Each item: session_id, query (cut at 400 characters), status, participating_agents,
started_at, completed_at, model_calls, total_tokens, option_count, human_action, escalation_reason.

A RUNNING row older than 5 minutes is reported as FAILED ('orphaned by restart'), the same rule the single-session GET applies.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.security import require_council_key
from app.governance import admin_store, db

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_LIMIT = 100
DEFAULT_LIMIT = 20
QUERY_PREVIEW_CHARS = 400


@router.get("/api/council/sessions")
async def list_council_sessions(
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    x_internal_council_key: str | None = Header(default=None),
):
    require_council_key(x_internal_council_key)  # before anything else
    if status is not None and status not in admin_store.COUNCIL_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(admin_store.COUNCIL_STATUSES)}.")
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Council unavailable: the governance database is not configured.")
    try:
        rows, total = await admin_store.list_council_sessions(status=status, limit=limit, offset=offset)
    except Exception as exc:
        logger.error("council sessions: could not read (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Council unavailable: could not read the sessions.") from exc
    items = [
        {
            "session_id": r["id"],
            "query": r["query"] if len(r["query"]) <= QUERY_PREVIEW_CHARS else r["query"][:QUERY_PREVIEW_CHARS] + "…",
            "status": r["status"],
            "participating_agents": list(r.get("participatingAgents") or []),
            "started_at": admin_store.iso_utc(r.get("startedAt")),
            "completed_at": admin_store.iso_utc(r.get("completedAt")),
            "model_calls": r.get("modelCalls") or 0,
            "total_tokens": r.get("totalTokens"),
            "option_count": int(r.get("optionCount") or 0),
            "human_action": r.get("humanAction"),
            "escalation_reason": r.get("escalationReason"),
        }
        for r in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
