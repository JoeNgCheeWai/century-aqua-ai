"""
GET /api/audit-logs: the audit-log browser's data source (Phase 6, Mission Control). Internal only.

Reads AgentAuditLog, the accountability record of every executive-agent answer (Clause 6), newest first.

KEY. Gated by INTERNAL_COUNCIL_KEY in the X-Internal-Council-Key header, checked before anything else: the council key
doubles as the admin key for Mission Control. It is still a different key from every agent key, so no agent key opens
this endpoint and the council key opens no agent endpoint. 503 if the key is unset on the server.

QUERY PARAMETERS
    agent_id            exactly one agent ('cao', 'coo', 'cto', 'cmo', ...)
    start_date          inclusive. A date (2026-11-01 = from 00:00 UTC) or an ISO datetime (no timezone = UTC)
    end_date            a DATE (2026-11-10) includes that whole day; a datetime is exact
    limit, offset       paging. limit 1-200, default 50
    human_action        Pending | Approved | Modified | Rejected
    has_authorization   true = only rows with an authorization request, false = only rows without
    search              case-insensitive text in the query or the response

RETURNS {items, total, limit, offset}. ``total`` counts every row matching the filters, so a UI can page. Each item is
the full record: query, response, constitutional score, sources, reasoning chain, scenarios, pre-consultation requests,
the authorization request (its shape depends on agent_id: 'cto' has proposed_code_diff, 'cmo' has proposed_content),
the human's decision (with when and by whom once decided) and the time the agent answered.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.security import require_council_key
from app.governance import admin_store, audit, db
from app.governance.scoring import REVIEW_THRESHOLD

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_SEARCH_CHARS = 200


def parse_bound(value: str | None, name: str, *, end: bool) -> datetime | None:
    """
    A start_date or end_date as the naive UTC datetime the database compares against, or None.

    The database's upper bound is EXCLUSIVE, so an end_date that is a plain date becomes the start of the NEXT day (that
    whole day is included), and an end_date that is a full datetime gets one microsecond added (it is inclusive).
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:  # YYYY-MM-DD
            day = date.fromisoformat(text)
            return datetime.combine(day + timedelta(days=1) if end else day, time.min)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be a date (YYYY-MM-DD) or an ISO 8601 datetime.") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed + timedelta(microseconds=1) if end else parsed


def audit_entry(row: dict[str, Any]) -> dict[str, Any]:
    """One AgentAuditLog row as the API returns it (snake_case, JSON columns parsed, times as UTC with a Z)."""
    request = admin_store.parse_json(row.get("authorizationRequest"))
    return {
        "id": row["id"],
        "agent_id": row["agentId"],
        "user_id": row["userId"],
        "query": row["query"],
        "response": row["response"],
        "constitutional_score": row["constitutionalScore"],
        "needs_review": row["constitutionalScore"] < REVIEW_THRESHOLD,
        "sources_cited": list(row.get("sourcesCited") or []),
        "reasoning_chain": list(row.get("reasoningChain") or []),
        "scenarios": admin_store.parse_json(row.get("scenarios")),
        "pre_consultation_requests": admin_store.parse_json(row.get("preConsultationRequests")),
        "authorization_request": request if isinstance(request, dict) else None,
        "human_action": row["humanAction"],
        # Absent on a database that has not had the add_human_action_audit migration yet.
        "human_action_at": admin_store.iso_utc(row.get("humanActionAt")),
        "human_action_by": row.get("humanActionBy"),
        "timestamp": admin_store.iso_utc(row.get("timestamp")),
    }


@router.get("/api/audit-logs")
async def list_audit_logs(
    agent_id: str | None = Query(default=None, max_length=32),
    start_date: str | None = Query(default=None, max_length=40),
    end_date: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    human_action: str | None = Query(default=None, max_length=16),
    has_authorization: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=MAX_SEARCH_CHARS),
    x_internal_council_key: str | None = Header(default=None),
):
    require_council_key(x_internal_council_key)  # before anything else
    if agent_id is not None and not db.valid_agent_id(agent_id):
        raise HTTPException(status_code=422, detail="agent_id is not a valid agent id.")
    if human_action is not None and human_action not in audit.HUMAN_ACTIONS:
        raise HTTPException(status_code=422, detail=f"human_action must be one of {list(audit.HUMAN_ACTIONS)}.")
    start = parse_bound(start_date, "start_date", end=False)
    end = parse_bound(end_date, "end_date", end=True)
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="start_date must be before end_date.")
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Audit log unavailable: the governance database is not configured.")
    try:
        rows, total = await admin_store.list_audit_logs(
            agent_id=agent_id, start=start, end=end, human_action=human_action, has_authorization=has_authorization,
            search=(search or "").strip() or None, limit=limit, offset=offset,
        )
    except Exception as exc:
        logger.error("audit-logs: could not read (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Audit log unavailable: could not read the database.") from exc
    return {"items": [audit_entry(r) for r in rows], "total": total, "limit": limit, "offset": offset}

