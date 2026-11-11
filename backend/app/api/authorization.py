"""
POST /api/authorization/{audit_id}/approve and /reject: record a human's decision on an agent's authorization request
(Phase 6, Mission Control). Internal only.

WHAT THIS IS. The CTO (code or system changes) and the CMO (publishing or changing something public) never act: each
answer that proposes an action stores an ``authorizationRequest`` in AgentAuditLog and leaves ``humanAction`` Pending.
These two endpoints are where a person decides. They set AgentAuditLog.humanAction to Approved or Rejected and record WHEN
(humanActionAt) and BY WHOM (humanActionBy, from the X-Admin-Id header).

WHAT APPROVING DOES (Phase 6.6). Approving always records the decision first, and that record stands on its own. Then, ONLY if
the request carries a valid ``execution_plan`` (branch, files, PR title and body, written by the CTO or CMO), the tool router
(app/agents/tools.py) opens a PULL REQUEST: create branch, write the files, open the PR. That is all it can do. It never
merges, never writes to the default branch and never deploys: a human merges the pull request (or does not), and CI checks
that it builds first. A request WITHOUT a plan behaves exactly as before: nothing is executed.

THE EXECUTION RULES (enforced here and in the tool router, not left to the agent)
    * the decision is recorded BEFORE anything runs; a failure to execute never undoes or blocks the decision
    * ONCE ONLY: execution is claimed with one atomic UPDATE, so two clicks, a retry or a crash cannot open two pull requests
    * the plan is re-validated from the stored copy right before it runs (the database copy is not trusted on its own)
    * the first failing step stops the run and is reported with the steps that did finish; nothing is retried
    * the outcome (status, steps, PR URL, error) is written back into the audit row's authorizationRequest under
      ``execution``, so Mission Control shows it. ``executed`` stays false: the CHANGE is not live until a human merges the PR
    * a rejection never executes anything

The agent's original ``timestamp`` is never rewritten (Clause 6), which is why the decision time has its own column.

RULES (enforced inside one atomic UPDATE, so two people acting at once cannot both win)
    only a row whose authorizationRequest is not null          otherwise 409
    only while humanAction is Pending                          otherwise 409 (a decision is final here)
    404 for an id that does not exist

KEY. INTERNAL_COUNCIL_KEY in X-Internal-Council-Key, checked first. X-Admin-Id (who is deciding) is REQUIRED, exactly like
the kill switch: a state change must be attributable.

503 if the database is not configured or the add_human_action_audit migration (humanActionAt, humanActionBy) is not applied.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from app.agents import tools
from app.api.security import require_actor, require_council_key
from app.governance import admin_store, db

logger = logging.getLogger(__name__)
router = APIRouter()

_NOTE = "Decision recorded. Nothing was executed: a human must carry out the proposed change."
_NOTE_PR = "Decision recorded and a pull request was opened. It is NOT merged: a human must review and merge it."
_NOTE_PR_FAILED = "Decision recorded, but the pull request could not be completed. Nothing is merged. See the details."


async def _execute_if_planned(audit_id: int, actor: str, agent_id: str) -> dict[str, Any] | None:
    """
    After an APPROVAL: run the stored execution_plan, if there is one, and store the outcome. Returns the outcome (shown in the
    API response), or None when the request had no plan. NEVER raises: whatever happens here, the decision already stands.
    """
    try:
        request = await admin_store.read_authorization_request(audit_id)
        plan = request.get("execution_plan") if request else None
        if not isinstance(plan, dict):
            return None  # no plan: exactly the old behaviour, nothing executed
        if not await admin_store.claim_execution(audit_id):
            return {"status": "already_claimed", "steps": [], "pr_url": None, "branch": None,
                    "error": "this approval has already been executed (or is being executed); nothing was run again"}
        footer = f"Opened by the AI_{agent_id.upper()} workflow after {actor} approved audit entry #{audit_id}. Not merged."
        outcome = await asyncio.to_thread(tools.run_execution_plan, plan, footer)
    except Exception as exc:
        logger.error("authorization: execution failed unexpectedly (%s)", type(exc).__name__)
        outcome = {"status": "failed", "steps": [], "pr_url": None, "branch": None, "error": f"unexpected error ({type(exc).__name__})"}
    try:
        await admin_store.record_execution(audit_id, outcome)
    except Exception as exc:
        # The pull request (if any) exists on GitHub; only the record of it failed. Say so, and log the URL for the operator.
        logger.error("authorization: could not store the execution result (%s); pr_url=%s", type(exc).__name__, outcome.get("pr_url"))
        outcome = {**outcome, "error": ((outcome.get("error") or "") + " The result could not be stored in the audit log.").strip()}
    return outcome


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
    body: dict[str, Any] = {
        "audit_id": row["id"],
        "agent_id": row["agentId"],
        "human_action": row["humanAction"],
        "human_action_at": admin_store.iso_utc(row["humanActionAt"]),
        "human_action_by": row["humanActionBy"],
        # Always false: even after a pull request is opened, the CHANGE is not live until a human merges it.
        "executed": False,
        "note": _NOTE,
    }
    if action == "approve":  # a rejection never executes anything
        outcome = await _execute_if_planned(audit_id, actor, str(row["agentId"]))
        if outcome is not None:
            body["execution"] = outcome
            body["note"] = _NOTE_PR if outcome.get("status") == "succeeded" else _NOTE_PR_FAILED
    return body


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
