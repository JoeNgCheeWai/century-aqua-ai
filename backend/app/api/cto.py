"""
POST /api/cto: the AI_CTO's internal-only endpoint (Phase 3 of the AI Executive Team).

Never public and never reached from /api/chat or the Gatekeeper. It has its OWN key:
INTERNAL_CTO_KEY, sent in the X-Internal-CTO-Key header. The CAO and COO keys do not open it and
the CTO key does not open /api/cao or /api/coo. The key is checked before anything else, so an
unauthenticated caller can never cause a model call, and the endpoint answers 503 (closed) if
INTERNAL_CTO_KEY is not configured on the server.

The response is the CAO's and COO's response PLUS one field, ``authorization_request``. The model
below inherits from the CAO's CAOResponse (imported, not copied), so the shared fields cannot drift
apart. That shared shape is what the future Council Protocol will read:

    response, reply, reasoning_chain, scenarios, pre_consultation_requests,
    constitutional_score, needs_review, audit_id (+ reset_epoch, history_discarded, sources)
    + authorization_request

THE AUTHORIZATION WORKFLOW: this endpoint NEVER executes a change. ``authorization_request`` is a
proposal (action_summary, risk_level, impact, proposed_code_diff / proposed_commands, rollback_plan)
whose ``status`` is always "pending_human_approval" and whose ``executed`` is always false. There is
no approve endpoint in this phase: a human reads the request and acts outside the system.

Every answer is recorded in AgentAuditLog as agent 'cto' (audit.record_response('cto', ...), called by
app/agents/cto_agent.py).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.agents.cto_agent import AGENT_ID, get_cto_result
from app.api.cao import CAORequest, CAOResponse
from app.api.security import require_actor, require_cto_key
from app.governance import audit

router = APIRouter()

# Same request shape as the CAO and COO, under a CTO name for readability and for OpenAPI.
CTORequest = CAORequest


class PlainSummary(BaseModel):
    """Three short plain-language answers for a non-technical decision-maker. Built by app/agents/plain_summary.py."""

    purpose: str  # what this task is for
    problem: str  # what problem it solves
    risk: str  # what could go wrong and what protects us (never a rating: the risk level is computed in code)


class PlanFile(BaseModel):
    path: str
    content: str  # the COMPLETE new file text
    commit_message: str


class ExecutionPlan(BaseModel):
    """What approving would do (Phase 6.6): open a pull request from this branch. Built and checked by execution_plan.py."""

    branch_name: str  # always under agent/
    files_to_update: list[PlanFile]
    pr_title: str
    pr_body: str = ""


class AuthorizationRequest(BaseModel):
    """A proposed change that waits for a human. Built by app/agents/cto_authorization.py."""

    action_summary: str
    executive_summary: PlainSummary | None = None  # Phase 6.5: plain-language purpose / problem / risk; null on older rows
    risk_level: Literal["low", "medium", "high", "critical"]
    impact: str
    proposed_code_diff: str | None = None  # text for review, never applied by the CTO
    proposed_commands: list[str] = Field(default_factory=list)  # text for review, never run by the CTO
    rollback_plan: str | None = None
    execution_plan: ExecutionPlan | None = None  # Phase 6.6; null unless the agent sent a plan that passed validation
    # Forced by code in cto_authorization.py. The Literal types make the API contract refuse anything else.
    status: Literal["pending_human_approval"] = "pending_human_approval"
    requires_human_approval: Literal[True] = True
    executed: Literal[False] = False


class CTOResponse(CAOResponse):
    authorization_request: AuthorizationRequest | None = None  # null unless a change was requested


@router.post("/api/cto", response_model=CTOResponse)
async def cto(
    request: CTORequest,
    x_internal_cto_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    # The key is checked before anything else, so unauthenticated callers can
    # never trigger a model call.
    require_cto_key(x_internal_cto_key)
    user_id = require_actor(x_user_id, "X-User-Id", required=False)

    try:
        history_dicts = [{"role": m.role, "content": m.content} for m in request.history]

        # Kill switch (Clause 15): if the CTO was reset after this caller's history was built,
        # that history is cancelled and must not reach the model.
        epoch = await audit.current_epoch(AGENT_ID)
        history_discarded = False
        if epoch is not None and request.reset_epoch is not None and request.reset_epoch < epoch:
            history_dicts, history_discarded = [], True

        result = await get_cto_result(request.message, history_dicts, user_id=user_id, model_tier=request.model)
        return CTOResponse(
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
            chat_reply=result.chat_reply,
            model=result.model,
        )
    except Exception as exc:
        # Do not log message content: enterprise queries may be sensitive.
        print(f"ERROR IN AI_CTO: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"AI_CTO unavailable: {type(exc).__name__}") from exc
