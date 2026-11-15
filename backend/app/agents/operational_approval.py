"""
COO-LED OPERATIONAL APPROVAL: where a "Safe to Implement" task goes instead of the CEO.

After the CTO (or CMO) writes an answer with an authorization_request, maybe_route() is called ONCE with the stored audit row. In order:

  0. THE SWITCH. COO_AUTO_APPROVAL must be "on" (it ships OFF). Off: nothing here runs and the task waits for the CEO, exactly as before.
  1. CODE classifies the task (safe_to_implement.classify): an allow-list of small UI, prompt-wording, test, document and strictly additive
     database changes, no commands, a passing self-check. Not on the list: it stays with the CEO. No model is involved in this step.
  2. CODE checks "fully reported" (coo_review.reported_problems): plain summary, rollback plan, impact.
  3. THE COO (a model) judges the CEO's three questions on the plan's actual file text (coo_review.review).
  4. Only if all three are yes, CODE records the approval as AI_COO under the CEO's delegation through the SAME path a person's approval takes
     (authorization.apply_decision): atomic, final, plan re-validated, run once, a pull request that is NEVER merged. A person still merges.

Every outcome is written onto the task's audit row (``operational_review``) so nothing happens off the record, and so the Operations Summary
can count it. ANY failure leaves the task Pending for the CEO. This function NEVER raises and never approves by accident.

Who may approve is also limited here: the actor id is a fixed constant, so the API's decision rule "needs an X-Admin-Id" cannot be satisfied by a
model-chosen name, and the COO can only act on a task that code classified as safe in this same call.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.agents import coo_review, safe_to_implement
from app.governance import admin_store, db, operations_log

logger = logging.getLogger(__name__)

ENV_SWITCH = "COO_AUTO_APPROVAL"
ACTOR = "AI_COO (delegated by the CEO)"
ORIGINATORS = ("cto", "cmo", "cao")  # who may propose a task that goes to the COO. The COO's own tasks never review themselves.


def enabled() -> bool:
    """Is COO-led approval switched on? Only the exact values on/true/1/yes. Anything else, including unset, is OFF. Read at call time."""
    return os.getenv(ENV_SWITCH, "").strip().lower() in ("on", "true", "1", "yes")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_review_sync(audit_id: int, review: dict[str, Any], force: bool) -> bool:
    """Write the review onto the task's audit row. Normally ONCE and only while the task is still Pending (a person may have decided first)."""
    guard = "" if force else ' AND "humanAction"=CAST(%s AS "HumanAction") AND NOT jsonb_exists("authorizationRequest", \'operational_review\')'
    params: tuple[Any, ...] = (db.json_param(review), audit_id) + (() if force else ("Pending",))
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE "AgentAuditLog" SET "authorizationRequest" = jsonb_set("authorizationRequest", \'{operational_review}\', CAST(%s AS jsonb)) '
            f'WHERE "id"=%s AND jsonb_typeof("authorizationRequest") = \'object\'{guard} RETURNING "id"',
            params,
        )
        return bool(db.fetch_all(cur))


async def _record(audit_id: int, route: str, kind: str, reasons: list[str], coo: dict[str, Any] | None, *, force: bool = False) -> bool:
    """Record one outcome. True if it was written. Best-effort: a failure is logged and the task simply stays with the CEO."""
    review = {"route": route, "kind": kind, "reasons": reasons[:8], "coo": coo, "at": _stamp(),
              "approved_by": ACTOR if route == operations_log.ROUTE_APPROVED else None}
    try:
        return await db.run(_write_review_sync, audit_id, review, force)
    except Exception as exc:
        logger.error("operational_approval: could not record the review for audit #%s (%s)", audit_id, type(exc).__name__)
        return False


def _unread_existing(request: dict[str, Any], read: set[str]) -> list[str]:
    """Paths in the plan that exist on main but were not read in full this answer (or could not be checked). Uses the read-only file check."""
    from app.agents import tools

    blocked = []
    for item in (request.get("execution_plan") or {}).get("files_to_update", []):
        path = str(item.get("path", ""))
        if path.strip().lower() in read:
            continue
        if tools.github_file_status(path) != "missing":
            blocked.append(path)
    return blocked


def _outcome(route: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"route": route, "message": message, **extra}


async def maybe_route(audit_id: int, agent_id: str, request: dict[str, Any] | None, *, originals: dict[str, str] | None = None,
                      reviewer: coo_review.Reviewer | None = None) -> dict[str, Any] | None:
    """
    Decide where one stored authorization_request goes. Returns None when COO-led approval is off or the request is not one it handles (the task
    simply waits for the CEO, as always), else a dict {"route", "message", ...}. ``originals`` maps a path (lower-case) to the text the agent READ.
    NEVER RAISES. The only way to "coo_approved" is: switch on, code says safe, code says fully reported, the COO says yes to all three, and the
    shared approval path succeeds.
    """
    try:
        return await _route(audit_id, agent_id, request, originals or {}, reviewer)
    except Exception as exc:  # whatever went wrong, nothing was approved by accident
        logger.error("operational_approval: failed for audit #%s (%s)", audit_id, type(exc).__name__)
        return _outcome("ceo_required", "The COO review could not complete, so this one is yours to approve.")


async def _route(audit_id: int, agent_id: str, request: dict[str, Any] | None, originals: dict[str, str], reviewer: coo_review.Reviewer | None) -> dict[str, Any] | None:
    from app.agents import plan_checks
    from app.api import authorization

    if not enabled() or agent_id not in ORIGINATORS or not isinstance(request, dict) or not db.is_configured():
        return None
    plan = request.get("execution_plan")
    if not isinstance(plan, dict):
        return None  # nothing a pull request could contain: not an operational task
    check = plan_checks.check_plan(plan, originals)
    verdict = safe_to_implement.classify(request, check.passed)
    if not verdict.safe:
        return _outcome("ceo_required", "This is not one of the small safe tasks the COO approves for you, so it is yours to approve.", reasons=verdict.reasons)
    unread = await asyncio.to_thread(_unread_existing, request, set(originals))
    if unread:
        return _outcome("ceo_required", "It replaces a file that was not read first, so it is yours to approve once that is checked.", reasons=unread)
    problems = coo_review.reported_problems(request, check.passed)
    if problems:
        await _record(audit_id, operations_log.ROUTE_DECLINED, verdict.kind, problems, None)
        return _outcome("ceo_required", "It is missing part of its report (see the reasons), so it is yours to approve.", reasons=problems)
    task = coo_review.build_task_text(request, verdict.kind, verdict.reasons, check.checks, agent_id)
    coo = await coo_review.review(task, reviewer)
    if not coo.approved:
        why = [coo.failure] if coo.failure else [f"{k}: {v}" for k, v in coo.reasons.items() if v]
        route = operations_log.ROUTE_UNAVAILABLE if coo.failure else operations_log.ROUTE_DECLINED
        await _record(audit_id, route, verdict.kind, why, coo.as_dict())
        return _outcome("ceo_required", "The COO had a concern and passed it to you, so it is yours to approve.", reasons=why, coo=coo.as_dict())
    try:
        decided = await authorization.apply_decision(audit_id, "approve", ACTOR, remember=False)
    except Exception as exc:  # a person decided first, or the database failed: either way nothing was approved here
        detail = getattr(exc, "detail", None) or type(exc).__name__
        return _outcome("ceo_required", "The task was decided by someone else or could not be recorded, so nothing was approved by the COO.", reasons=[str(detail)[:200]])
    await _record(audit_id, operations_log.ROUTE_APPROVED, verdict.kind, verdict.reasons, coo.as_dict(), force=True)
    execution = decided.get("execution") or {}
    return _outcome("coo_approved", "The COO approved this under your delegation.", kind=verdict.kind, coo=coo.as_dict(),
                    pr_url=execution.get("pr_url"), execution_status=execution.get("status"))


def approval_note(outcome: dict[str, Any] | None) -> str:
    """One plain sentence for the CEO about where the task went (written by code). '' when COO-led approval did not apply."""
    if not outcome:
        return ""
    if outcome["route"] == "coo_approved":
        link = f" It opened a pull request: {outcome['pr_url']}." if outcome.get("pr_url") else " The pull request could not be opened, so nothing was created."
        return ("**COO-approved under your delegation:** this was a small, safe task, so the COO reviewed and approved it for you." + link
                + " It is not live: you (or a developer) still review and merge the pull request.")
    # "Ready for your approval", not "Waiting for your decision": the CEO found the old label robotic. Same meaning, collaborative words.
    return "**Ready for your approval:** " + str(outcome.get("message", "")).rstrip(".") + "."
