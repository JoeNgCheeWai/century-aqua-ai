"""
POST /api/cao: the AI_CAO's internal-only endpoint.

Never public and never reached from /api/chat or the Gatekeeper. Protected by the
X-Internal-CAO-Key header (INTERNAL_CAO_KEY env var), checked before any model call.

Phase 1 (Enhanced Reasoning & Prediction) response, in addition to the governance facts:

    response                   the final recommendation (``reply`` is the same text, kept for old callers)
    reasoning_chain            step-by-step logic, data and sources, assumptions, limitations
    scenarios                  {optimistic, baseline, pessimistic}, or null for a non-strategic question
    pre_consultation_requests  {coo_query, cto_query}: structure for the future Council Protocol (nulls now)
    constitutional_score       1-100, a surface check (see app/governance/scoring.py)
    needs_review, audit_id
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.agents.cao_agent import get_cao_result
from app.agents.model_tiers import ModelTier
from app.api.security import require_actor, require_internal_key
from app.governance import audit

router = APIRouter()


class CAOMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CAORequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    history: list[CAOMessage] = Field(default_factory=list)
    # The reset epoch the caller last saw (returned as `resetEpoch`). If the kill switch
    # was used since, the epoch is stale and the history the caller holds is ignored.
    reset_epoch: int | None = Field(default=None, ge=0)
    # Phase 6.5: which model TIER answers. Optional: left out, the agent uses its own configured model (as before).
    # A tier, never a model id: app/agents/model_tiers.py maps it from an allow-list, so a caller cannot pick any model.
    model: ModelTier | None = None


class CAOScenario(BaseModel):
    """One predicted future. Built by app/agents/cao_reasoning.py, which guarantees every field."""

    description: str
    timeline: str  # e.g. "6-12 months"
    resource_requirements: str  # budget, staff and infrastructure, with figures labelled as sourced or illustrative
    confidence_percentage: int = Field(..., ge=0, le=100)  # the CAO's judgement, NOT a statistical probability
    key_assumptions: list[str]
    risks: list[str]


class CAOScenarios(BaseModel):
    optimistic: CAOScenario
    baseline: CAOScenario
    pessimistic: CAOScenario


class CAOPreConsultation(BaseModel):
    """Structure for the future Council Protocol. Always null in Phase 1 (no COO or CTO exists yet)."""

    coo_query: str | None = None  # financial feasibility question for the future COO
    cto_query: str | None = None  # technical feasibility question for the future CTO


class CAOResponse(BaseModel):
    response: str  # the final recommendation
    reply: str  # same text as `response`; kept so existing callers keep working
    reasoning_chain: list[str] = Field(default_factory=list)  # empty if the model did not give a chain
    scenarios: CAOScenarios | None = None  # null for a question that is not strategic
    pre_consultation_requests: CAOPreConsultation = Field(default_factory=CAOPreConsultation)
    # Governance facts about this answer (Clause 6). Optional so old callers keep working.
    constitutional_score: int | None = None  # 1-100
    needs_review: bool | None = None
    audit_id: int | None = None  # None if the audit log was unavailable
    reset_epoch: int | None = None
    history_discarded: bool = False
    sources: list[str] = Field(default_factory=list)
    # Phase 6.5: the short conversational answer the chat shows first (null if the model gave none, or it was vetted
    # out), and the id of the model that wrote this answer.
    chat_reply: str | None = None
    model: str | None = None


@router.post("/api/cao", response_model=CAOResponse)
async def cao(
    request: CAORequest,
    x_internal_cao_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    # The key is checked before anything else, so unauthenticated callers can
    # never trigger a model call.
    require_internal_key(x_internal_cao_key)
    user_id = require_actor(x_user_id, "X-User-Id", required=False)

    try:
        history_dicts = [{"role": m.role, "content": m.content} for m in request.history]

        # Kill switch (Clause 15): if the agent was reset after this caller's history
        # was built, that history is cancelled and must not reach the model.
        epoch = await audit.current_epoch("cao")
        history_discarded = False
        if epoch is not None and request.reset_epoch is not None and request.reset_epoch < epoch:
            history_dicts, history_discarded = [], True

        result = await get_cao_result(request.message, history_dicts, user_id=user_id, model_tier=request.model)
        return CAOResponse(
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
            chat_reply=result.chat_reply,
            model=result.model,
        )
    except Exception as exc:
        # Do not log message content: enterprise queries may be sensitive.
        print(f"ERROR IN AI_CAO: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"AI_CAO unavailable: {type(exc).__name__}") from exc
