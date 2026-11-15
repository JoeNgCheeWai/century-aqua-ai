"""
GET /api/operations/summary: the COO's Operations Summary for the CEO. Internal only.

What the COO approved on the CEO's behalf (COO-led approval, see app/agents/operational_approval.py), counted from the audit log.

KEY. INTERNAL_COUNCIL_KEY in X-Internal-Council-Key, checked before anything else (the council key is also the admin key).
QUERY  days (1-90, default 7).
RETURNS {"summary": "<a short paragraph for the CEO>", "facts": {...exact counts...}, "auto_approval_enabled": bool}

THE TEXT IS WRITTEN BY CODE from the counts (operations_log.summary_text), not by a model, so every number in it is exact. It never says
"resolved" or "zero disruptions": an approval only opens a pull request that a person merges, and nothing here monitors production.
503 if the database is not configured or cannot be read.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from app.agents import operational_approval
from app.api.security import require_council_key
from app.governance import db, operations_log

router = APIRouter()


@router.get("/api/operations/summary")
async def operations_summary(
    days: int = Query(default=operations_log.DEFAULT_DAYS, ge=1, le=operations_log.MAX_DAYS),
    x_internal_council_key: str | None = Header(default=None),
):
    require_council_key(x_internal_council_key)  # before anything else
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Operations summary unavailable: the governance database is not configured.")
    facts = await operations_log.collect(days)
    if facts is None:
        raise HTTPException(status_code=503, detail="Operations summary unavailable: could not read the audit log.")
    return {"summary": operations_log.summary_text(facts), "facts": facts, "auto_approval_enabled": operational_approval.enabled()}
