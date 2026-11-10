"""
POST /api/authorization/{audit_id}/approve and /reject: record a human's decision on an agent's authorization request
(Phase 6, Mission Control). Internal only.

WHAT THIS IS. The CTO (code or system changes) and the CMO (publishing or changing something public) never act: each
answer that proposes an action stores an ``authorizationRequest`` in AgentAuditLog and leaves ``humanAction`` Pending.
These two endpoints are where a person decides. They set AgentAuditLog.humanAction to Approved or Rejected and record WHEN
(humanActionAt) and BY WHOM (humanActionBy, from the X-Admin-Id header).

WHAT THIS IS NOT. Approving does NOT execute anything. The backend has no execution tools: no code is applied, no command is
run, nothing is published. Approval is a recorded decision, and a human still carries the change out. Every response says
``executed: false``. The agent's original ``timestamp`` is never rewritten (Clause 6), which is why the decision time has its
own column.

RULES (enforced inside one atomic UPDATE, so two people acting at once cannot both win)
    only a row whose authorizationRequest is not null          otherwise 409
    only while humanAction is Pending                          otherwise 409 (a decision is final here)
    404 for an id that does not exist

KEY. INTERNAL_COUNCIL_KEY in X-Internal-Council-Key, checked first. X-Admin-Id (who is deciding) is REQUIRED, exactly like
the kill switch: a state change must be attributable.

503 if the database is not configured or the add_human_action_audit migration (humanActionAt, humanActionBy) is not applied.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from app.api.security import require_actor, require_council_key
from app.governance import admin_store, db

logger = logging.getLogger(__name__)
router = APIRouter()

_NOTE = "Decision recorded. Nothing was executed: a human must carry out the proposed change."


async def _decide(audit_id: int, action: str, x_internal_council_key: str | None, x_admin_id: str | None) -> dict[str, Any]:
    require_council_key(x_internal_council_key)  # before anything else
    actor = require_actor(x_admin_id, "X-Admin-Id", required=True)
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Authorization unavailable: the governance database is not configured.")
    try:
        outcome, row = await admin_store.decide_authorization(audit_id, action, actor)
    except Exception as exc:
        # Most likely the add_human_action_audit migration has not been applied (the two new columns are missing).
        logger.error("authorization: could not record the decision (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Authorization unavailable: could not record the decision. Has the add_human_action_audit migration been applied?",
        ) from exc
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Audit entry not found.")
    if outcome == "no_request":
        raise HTTPException(status_code=409, detail="This audit entry has no authorization request to decide.")
    if outcome == "already_decided":
        raise HTTPException(status_code=409, detail=f"Already decided: this request is {row['humanAction']}, not Pending.")
    assert row is not None
    return {
        "audit_id": row["id"],
        "agent_id": row["agentId"],
        "human_action": row["humanAction"],
        "human_action_at": admin_store.iso_utc(row["humanActionAt"]),
        "human_action_by": row["humanActionBy"],
        "executed": False,
        "note": _NOTE,
    }


@router.post("/api/authorization/{audit_id}/approve")
async def approve(
    audit_id: int,
    x_internal_council_key: str | None = Header(default=None),
    x_admin_id: str | None = Header(default=None),
):
    return await _decide(audit_id, "approve", x_internal_council_key, x_admin_id)


@router.post("/api/authorization/{audit_id}/reject")
async def reject(
    audit_id: int,
    x_internal_council_key: str | None = Header(default=None),
    x_admin_id: str | None = Header(default=None),
):
    return await _decide(audit_id, "reject", x_internal_council_key, x_admin_id)
