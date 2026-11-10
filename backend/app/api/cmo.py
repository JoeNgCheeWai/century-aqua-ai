"""
POST /api/cmo: the AI_CMO's internal-only endpoint (Phase 4 of the AI Executive Team).

Never public and never reached from /api/chat or the Gatekeeper. It has its OWN key: INTERNAL_CMO_KEY, sent
in the X-Internal-CMO-Key header. The CAO, COO and CTO keys do not open it and the CMO key does not open any
of their endpoints. The key is checked before anything else, so an unauthenticated caller can never cause a
model call, and the endpoint answers 503 (closed) if INTERNAL_CMO_KEY is not configured on the server.

The response is the CAO's, COO's and CTO's response shape: it inherits from the CAO's CAOResponse (imported,
not copied), so the shared fields cannot drift apart, and adds ``authorization_request`` as the CTO does:

    response, reply, reasoning_chain, scenarios, pre_consultation_requests,
    constitutional_score, needs_review, audit_id (+ reset_epoch, history_discarded, sources)
    + authorization_request

THE AUTHORIZATION WORKFLOW: this endpoint NEVER publishes anything. The CMO may draft freely (drafts are in
``response``). ``authorization_request`` is a proposal to publish, send, launch or change something public:
action_summary, risk_level, impact, proposed_content, deployment_commands, rollback_plan and claims_to_verify,
with ``status`` always "pending_human_approval" and ``executed`` always false. Its fields differ from the
CTO's (content, not code); both are stored in AgentAuditLog.authorizationRequest, so a reader uses the row's
agentId to know which shape it is. There is no approve endpoint in this phase: a human reads the request and
acts outside the system.

Every answer is recorded in AgentAuditLog as agent 'cmo' (audit.record_response('cmo', ...), called by
app/agents/cmo_agent.py).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.agents.cmo_agent import AGENT_ID, get_cmo_result
from app.api.cao import CAORequest, CAOResponse
from app.api.security import require_actor, require_cmo_key
from app.governance import audit

router = APIRouter()

# Same request shape as the other executives, under a CMO name for readability and for OpenAPI.
CMORequest = CAORequest


class CMOAuthorizationRequest(BaseModel):
    """A proposal to publish or change something public, waiting for a human. Built by app/agents/cmo_authorization.py."""

    action_summary: str
    risk_level: Literal["low", "medium", "high", "critical"]
    impact: str
    proposed_content: str | None = None  # the exact copy or creative, as text for review; never published by the CMO
    deployment_commands: list[str] = Field(default_factory=list)  # steps for a human to carry out; never run by the CMO
    rollback_plan: str | None = None
    claims_to_verify: list[str] = Field(default_factory=list)  # factual claims a human must substantiate first
    # Forced by code in cmo_authorization.py. The Literal types make the API contract refuse anything else.
    status: Literal["pending_human_approval"] = "pending_human_approval"
    requires_human_approval: Literal[True] = True
    executed: Literal[False] = False


class CMOResponse(CAOResponse):
    authorization_request: CMOAuthorizationRequest | None = None  # null unless something would be published or changed


@router.post("/api/cmo", response_model=CMOResponse)
async def cmo(
    request: CMORequest,
    x_internal_cmo_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    # The key is checked before anything else, so unauthenticated callers can
    # never trigger a model call.
    require_cmo_key(x_internal_cmo_key)
    user_id = require_actor(x_user_id, "X-User-Id", required=False)

    try:
        history_dicts = [{"role": m.role, "content": m.content} for m in request.history]

        # Kill switch (Clause 15): if the CMO was reset after this caller's history was built,
        # that history is cancelled and must not reach the model.
        epoch = await audit.current_epoch(AGENT_ID)
        history_discarded = False
        if epoch is not None and request.reset_epoch is not None and request.reset_epoch < epoch:
            history_dicts, history_discarded = [], True

        result = await get_cmo_result(request.message, history_dicts, user_id=user_id)
        return CMOResponse(
            response=result.reply,
            reply=result.reply,
            reasoning_chain=result.reasoning_chain,
            scenarios=result.scenarios,
            pre_consultation_requests=result.pre_consultation_requests,
            authorization_request=result.authorization_request,
            constitutional_score=result.constitutional_score,
            needs_review=result.needs_review,
            audit_id=result.audit_id,
            reset_epoch=epoch,
            history_discarded=history_discarded,
            sources=result.sources,
        )
    except Exception as exc:
        # Do not log message content: enterprise queries may be sensitive.
        print(f"ERROR IN AI_CMO: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"AI_CMO unavailable: {type(exc).__name__}") from exc
