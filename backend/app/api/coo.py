"""
POST /api/coo: the AI_COO's internal-only endpoint (Phase 2 of the AI Executive Team).

Never public and never reached from /api/chat or the Gatekeeper. It has its OWN key:
INTERNAL_COO_KEY, sent in the X-Internal-COO-Key header. The CAO key does not open it and
the COO key does not open /api/cao. The key is checked before anything else, so an
unauthenticated caller can never cause a model call, and the endpoint answers 503 (closed)
if INTERNAL_COO_KEY is not configured on the server.

The response has EXACTLY the same shape as POST /api/cao, because the models below are the
CAO's own (imported, not copied), so the two cannot drift apart. That shared shape is what
the future Council Protocol will read:

    response, reply, reasoning_chain, scenarios, pre_consultation_requests,
    constitutional_score, needs_review, audit_id (+ reset_epoch, history_discarded, sources)

Every answer is recorded in AgentAuditLog as agent 'coo' (audit.record_response('coo', ...),
called by app/agents/coo_agent.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.agents.coo_agent import AGENT_ID, get_coo_result
from app.api.cao import CAORequest, CAOResponse
from app.api.security import require_actor, require_coo_key
from app.governance import audit

router = APIRouter()

# Same request and response shapes as the CAO, under COO names for readability and for OpenAPI.
COORequest = CAORequest
COOResponse = CAOResponse


@router.post("/api/coo", response_model=COOResponse)
async def coo(
    request: COORequest,
    x_internal_coo_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    # The key is checked before anything else, so unauthenticated callers can
    # never trigger a model call.
    require_coo_key(x_internal_coo_key)
    user_id = require_actor(x_user_id, "X-User-Id", required=False)

    try:
        history_dicts = [{"role": m.role, "content": m.content} for m in request.history]

        # Kill switch (Clause 15): if the COO was reset after this caller's history was
        # built, that history is cancelled and must not reach the model.
        epoch = await audit.current_epoch(AGENT_ID)
        history_discarded = False
        if epoch is not None and request.reset_epoch is not None and request.reset_epoch < epoch:
            history_dicts, history_discarded = [], True

        result = await get_coo_result(request.message, history_dicts, user_id=user_id)
        return COOResponse(
            response=result.reply,
            reply=result.reply,
            reasoning_chain=result.reasoning_chain,
            scenarios=result.scenarios,
            pre_consultation_requests=result.pre_consultation_requests,
            constitutional_score=result.constitutional_score,
            needs_review=result.needs_review,
            audit_id=result.audit_id,
            reset_epoch=epoch,
            history_discarded=history_discarded,
            sources=result.sources,
        )
    except Exception as exc:
        # Do not log message content: enterprise queries may be sensitive.
        print(f"ERROR IN AI_COO: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"AI_COO unavailable: {type(exc).__name__}") from exc
