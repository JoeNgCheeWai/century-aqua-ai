"""
POST /api/admin/agents/{agent_id}/reset: the kill switch (Clause 15).

Gated by INTERNAL_CAO_KEY. The X-Admin-Id header is REQUIRED: a reset must say who
did it. The reset itself is one database transaction (see governance/kill_switch.py):
it either fully happens or nothing changes.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.api.security import require_actor, require_internal_key
from app.governance import db, kill_switch

router = APIRouter()


class ResetResponse(BaseModel):
    agentId: str
    clearedEntries: int
    resetEpoch: int
    lastReset: str
    detail: str


@router.post("/api/admin/agents/{agent_id}/reset", response_model=ResetResponse)
async def reset_agent_endpoint(
    agent_id: str,
    request: Request,
    x_internal_cao_key: str | None = Header(default=None),
    x_admin_id: str | None = Header(default=None),
):
    require_internal_key(x_internal_cao_key)
    admin_id = require_actor(x_admin_id, "X-Admin-Id", required=True)
    if not db.valid_agent_id(agent_id):
        raise HTTPException(status_code=404, detail="Unknown agent.")

    ip = request.client.host if request.client else None
    try:
        result = await kill_switch.reset_agent(agent_id, admin_id, ip)
    except kill_switch.UnknownAgentError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}") from exc
    except db.GovernanceDBError as exc:
        # Nothing was changed (the transaction rolled back or never started).
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ResetResponse(
        **result,
        detail="Agent reset. Stored history cleared and any caller-held history is now invalid. "
        "The audit log was kept. The agent restarts from the base constitution.",
    )
