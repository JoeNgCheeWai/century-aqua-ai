"""POST /api/agents/cross-check: one executive agent reviews another's recommendation."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.agents import cross_validation as cv
from app.api.security import require_actor, require_internal_key

router = APIRouter()


class CrossCheckRequest(BaseModel):
    agent_a_id: str = Field(..., min_length=2, max_length=32, description="The agent whose recommendation is reviewed")
    agent_b_id: str = Field(..., min_length=2, max_length=32, description="The reviewing agent")
    query: str = Field(..., min_length=1, max_length=cv.MAX_QUERY_CHARS)
    context: str = Field(..., min_length=1, max_length=cv.MAX_CONTEXT_CHARS, description="Agent A's recommendation")


class CrossCheckResponse(BaseModel):
    valid: bool
    reasoning: str
    confidence: float
    constitutional_alignment: str
    logged: bool


@router.post("/api/agents/cross-check", response_model=CrossCheckResponse)
async def cross_check_endpoint(
    request: CrossCheckRequest,
    x_internal_cao_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    # The key is checked first, so an unauthenticated caller can never cause a model call.
    require_internal_key(x_internal_cao_key)
    require_actor(x_user_id, "X-User-Id", required=False)  # validates the format if sent

    try:
        result = await cv.cross_check(request.agent_a_id, request.agent_b_id, request.query, request.context)
    except cv.UnknownAgent as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except cv.AgentUnavailable as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except cv.RateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(max(1, int(exc.retry_after + 0.999)))},
        ) from exc
    except cv.CrossCheckTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except cv.CrossCheckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CrossCheckResponse(**result.as_dict(), logged=result.logged)
