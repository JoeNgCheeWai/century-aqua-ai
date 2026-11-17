"""
GET /api/coo-decisions: what the COO decided, for Mission Control's "COO Decisions" dashboard. Internal only (INTERNAL_COUNCIL_KEY, checked first).

QUERY  days (1-90, default 30), period (day|week|month, default week: the audit summary's window)
RETURNS {"items": [...], "audit_summary": "<code-written report>", "audit_ok": bool, "days": n}
  each item: audit_id, agent_id, action_summary, risk_level, human_action, human_action_by, at, group (approved | rejected | escalated: the COO's LAST
  decision), coo_decisions (every decision with its reasoning, oldest first) and notification ({id, status} when the CEO was notified, else null).

READ-ONLY. The text is the COO's recorded reasoning and the code-written audit report (audit_report.py), never a model's summary. A database problem is a
503, never an empty list. There is NO discussion thread: the CEO discusses a critical item with the COO in the COO's chat, which is not stored here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.security import require_council_key
from app.governance import admin_store, audit_report, db

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_ITEMS = 100


def _read_sync(since: datetime) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT "id","agentId","humanAction","humanActionBy","timestamp","authorizationRequest" FROM "AgentAuditLog" '
                    'WHERE "timestamp">=%s AND jsonb_typeof("authorizationRequest")=%s AND jsonb_exists("authorizationRequest", %s) '
                    'ORDER BY "timestamp" DESC,"id" DESC LIMIT %s', (db.naive_utc(since), "object", "coo_decisions", MAX_ITEMS))
        rows = db.fetch_all(cur)
        notes: dict[int, dict[str, Any]] = {}
        if rows:
            cur.execute('SELECT "id","auditId","status" FROM "CeoNotification" WHERE "auditId" = ANY(%s) ORDER BY "id"', ([r["id"] for r in rows],))
            for n in db.fetch_all(cur):
                notes[int(n["auditId"])] = {"id": n["id"], "status": n["status"]}  # the newest wins
    return rows, notes


def present(row: dict[str, Any], notes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """One task as the dashboard reads it. PURE. The group is the COO's LAST decision, so a task it escalated and a person then decided still shows as escalated."""
    request = admin_store.parse_json(row.get("authorizationRequest"))
    request = request if isinstance(request, dict) else {}
    history = [d for d in request.get("coo_decisions") or [] if isinstance(d, dict)]
    last = history[-1] if history else {}
    outcome = str(last.get("outcome") or "")
    return {
        "audit_id": row["id"], "agent_id": row["agentId"], "action_summary": str(request.get("action_summary") or "")[:300], "risk_level": request.get("risk_level"),
        "human_action": str(row["humanAction"]), "human_action_by": row.get("humanActionBy"), "at": admin_store.iso_utc(row.get("timestamp")),
        "group": outcome if outcome in ("approved", "rejected", "escalated") else "escalated",
        "coo_decisions": [{"decision": d.get("decision"), "outcome": d.get("outcome"), "reasoning": str(d.get("reasoning") or "")[:700], "at": d.get("at")} for d in history],
        "notification": notes.get(int(row["id"])),
    }


@router.get("/api/coo-decisions")
async def coo_decisions(
    days: int = Query(default=30, ge=1, le=90),
    period: str = Query(default="week", max_length=8),
    x_internal_council_key: str | None = Header(default=None),
):
    require_council_key(x_internal_council_key)  # before anything else
    if audit_report.period_days(period) is None:
        raise HTTPException(status_code=422, detail=f"period must be one of {list(audit_report.PERIODS)}.")
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="COO decisions unavailable: the governance database is not configured.")
    try:
        rows, notes = await db.run(_read_sync, datetime.now(timezone.utc) - timedelta(days=days))
    except Exception as exc:
        logger.error("coo-decisions: could not read (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="COO decisions unavailable: could not read the audit log.") from exc
    summary, ok = await audit_report.generate(period)
    return {"items": [present(r, notes) for r in rows], "audit_summary": summary, "audit_ok": ok, "days": days}
