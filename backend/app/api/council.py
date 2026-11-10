"""
POST /api/council and GET /api/council/{session_id}: the AI Executive Council Protocol (Phase 5), internal only.

ASYNC JOB PATTERN. A council session takes minutes (several 50-70 s model calls) and would time out a single
Render request, so POST does NOT wait for it:
  1. check the key and the input, then take a concurrency slot (503 + Retry-After when the council is full);
  2. write a CouncilSessionLog row with status RUNNING;
  3. start the session as a background asyncio task and return 202 {session_id, status: "RUNNING"} at once.
GET /api/council/{session_id} reads that row. While RUNNING it returns only the status; once RESOLVED,
ESCALATED_TO_CEO, NEEDS_CEO_INPUT or FAILED it returns the full result. A RUNNING row older than 5 minutes is
reported as FAILED ('orphaned by restart'), because a Render restart would otherwise leave it RUNNING forever.

KEYS. Both endpoints need INTERNAL_COUNCIL_KEY in the X-Internal-Council-Key header, checked before anything else.
It is a SEPARATE key: no agent key opens the council and the council key opens no agent endpoint. 503 if unset.

THE COUNCIL ADVISES AND NEVER ACTS. Any action it proposes comes back pending_human_approval, executed false.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agents import council
from app.api.security import require_council_key
from app.governance import council_store, db

logger = logging.getLogger(__name__)
router = APIRouter()

# Strong references to running tasks: asyncio keeps only weak ones, and a collected task would vanish mid-session.
_tasks: set[asyncio.Task] = set()


class CouncilRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=council.MAX_QUERY_CHARS)
    agents: list[str] | None = Field(default=None, max_length=4)


async def _run_and_store(session_id: str, query: str, agents: list[str]) -> None:
    """The background task. Always frees the slot and always closes the row, whatever happens."""
    try:
        result = await council.run_council_session(query, agents, session_id=session_id)
        await council_store.finish_session(session_id, result)
    except Exception as exc:  # the council itself does not raise; this is the database or something unforeseen
        logger.error("council %s: could not complete (%s)", session_id, type(exc).__name__)
        try:
            await council_store.fail_session(session_id, f"internal error: {type(exc).__name__}")
        except Exception:
            logger.error("council %s: could not even mark the row FAILED", session_id)
    finally:
        council.release_slot()


@router.post("/api/council", status_code=202)
async def start_council(request: CouncilRequest, x_internal_council_key: str | None = Header(default=None)):
    require_council_key(x_internal_council_key)  # before anything else: no key, no model call
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Council unavailable: the governance database is not configured.")
    try:
        participants, rationale = council.resolve_participants(request.query.strip(), request.agents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not request.query.strip():
        raise HTTPException(status_code=422, detail="query must not be blank")
    if not council.try_acquire_slot():
        return JSONResponse(
            status_code=503,
            content={"detail": f"The council is busy ({council.MAX_CONCURRENT_SESSIONS} sessions running). Retry shortly."},
            headers={"Retry-After": "30"},
        )
    session_id = str(uuid.uuid4())
    try:
        await council_store.create_session(session_id, request.query.strip(), participants, rationale)
    except Exception as exc:
        council.release_slot()
        logger.error("council: could not create the session row (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Council unavailable: could not record the session.") from exc
    task = asyncio.create_task(_run_and_store(session_id, request.query.strip(), participants))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"session_id": session_id, "status": "RUNNING"}


def _json(value: Any) -> Any:
    """A jsonb column comes back parsed from some drivers and as text from others."""
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _payload(row: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    """The session as the caller sees it. Full result only once the status is terminal."""
    status = row["status"]
    body: dict[str, Any] = {"session_id": row["id"], "status": status, "started_at": _iso(row.get("startedAt"))}
    if status == "RUNNING":
        return body
    questions = council_store.split_questions(row.get("ceoQuestions"))
    body.update({
        "query": row["query"],
        "participating_agents": list(row.get("participatingAgents") or []),
        "selection_rationale": row["selectionRationale"],
        "options": _json(row.get("caoSynthesizedOptions")) if status == "RESOLVED" else None,
        "conflict_matrix": _json(row.get("conflictMatrix")) or [],
        "hard_constraint_vetoes": _json(row.get("hardConstraintVetoes")) or [],
        "coo_moderation_notes": row.get("cooModerationNotes"),
        "red_team_findings": _json(row.get("redTeamFindings")),
        "dissent_record": _json(row.get("dissentRecord")) or [],
        # All three come from the one ceoQuestions column (see council_store.question_record):
        "ceo_questions": questions["critical"],  # what the CEO must answer: the COO's ruling (NEEDS_CEO_INPUT)
        "open_questions": questions["open"],  # flagged, ruled non-critical, stated as assumptions in the options
        "agent_flagged_questions": questions["agent_flagged"],  # everything the agents flagged (the audit record)
        "escalation_reason": row.get("escalationReason"),
        "model_calls": row.get("modelCalls", 0),
        "total_tokens": row.get("totalTokens"),
        "completed_at": _iso(row.get("completedAt")),
        "human_action": row.get("humanAction"),
        "turns": [
            {"round": t["round"], "agent_id": t["agentId"], "position": t["position"],
             "hard_constraints": _json(t.get("hardConstraints")), "confidence": t.get("confidence"),
             "sources_cited": list(t.get("sourcesCited") or []), "tokens_used": t.get("tokensUsed")}
            for t in turns
        ],
        # Constants: the council has no execution tools, so nothing it returns has been, or can be, executed.
        "executed": False,
        "requires_human_approval": True,
    })
    return body


@router.get("/api/council/{session_id}")
async def get_council(session_id: str, x_internal_council_key: str | None = Header(default=None)):
    require_council_key(x_internal_council_key)
    try:
        uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Council session not found.") from exc
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Council unavailable: the governance database is not configured.")
    try:
        row, turns = await council_store.get_session(session_id)
    except Exception as exc:
        logger.error("council: could not read session (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Council unavailable: could not read the session.") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Council session not found.")
    return _payload(row, turns)

