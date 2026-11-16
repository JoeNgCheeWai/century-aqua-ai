"""
APPROVING A CAO OR COO RECOMMENDATION. This is the missing 'Approve' for the two executives whose ApprovedStrategies stayed empty.

It adds NO new approval path. A person approves through the same endpoint as for the CTO and CMO (authorization.apply_decision): the decision is
atomic and final, it is audited, and the approved strategy is saved by the same strategy_store call, so the learning loop now fills for the CAO and COO.
The only difference is that there is nothing to execute: no pull request, no GitHub access.

DELEGATION TO THE COO (only when COO_AUTO_APPROVAL is on, same switch as for code tasks), in this order, any failure leaves it with the CEO:
  1. CODE: exec_requests.sensitivity() finds animals, chemicals, statutory reporting or money at or above the threshold -> the CEO, ALWAYS.
  2. CODE: the risk level is low or medium (a high or critical recommendation is the CEO's) and it is fully reported (coo_review.reported_problems).
  3. The COO's OWN recommendations never review themselves; the CAO's and CMO's may be reviewed by the COO.
  4. THE COO (a model) answers the CEO's three questions on the recommendation text (coo_review.review, the same verdict parser).
  5. CODE records the approval as the delegate through apply_decision, which RE-CHECKS sensitivity itself (ceo_gate) so no caller can skip step 1.
"""

from __future__ import annotations

from typing import Any

from app.agents import coo_review, exec_requests
from app.governance import operations_log

KIND_LABEL = "recommendation"
ROUTABLE_ORIGINATORS = ("cao",)  # recommendations the COO may review. The COO's own never do; the CMO's content goes to a person.
DELEGABLE_RISK = ("low", "medium")


def is_recommendation(request: Any) -> bool:
    return isinstance(request, dict) and request.get("kind") == exec_requests.KIND


def ceo_gate(request: dict[str, Any] | None, actor: str) -> str | None:
    """
    The reason ``actor`` may NOT approve this request, or None. Re-computed from the stored text every time (the stored ceo_only flag is not trusted).
    Only the delegate is ever refused: a person with the council key is the CEO.
    """
    from app.agents import operational_approval

    if actor != operational_approval.ACTOR or not is_recommendation(request):
        return None
    reasons = exec_requests.sensitivity(request)
    if reasons:
        return "this recommendation is the CEO's decision: " + "; ".join(reasons)
    if request.get("risk_level") not in DELEGABLE_RISK:
        return f"a {request.get('risk_level')}-risk recommendation is the CEO's decision"
    return None


def task_text(request: dict[str, Any], agent_id: str) -> str:
    """What the COO is shown for a recommendation (delimiters made unforgeable by coo_review._clean)."""
    summary = request.get("executive_summary") or {}
    parts = [
        f"Proposed by: AI_{agent_id.upper()}", "Kind: a RECOMMENDATION, not a code change. Approving only records the decision and remembers the strategy: "
        "no pull request is opened and nothing is run.", f"Action: {request.get('action_summary')}", f"Risk level (computed in code): {request.get('risk_level')}",
        f"Impact: {request.get('impact')}", f"Rollback plan: {request.get('rollback_plan')}",
        f"Plain summary: purpose={summary.get('purpose')} | problem={summary.get('problem')} | risk={summary.get('risk')}",
        "Code checked: no animals, chemicals, statutory reporting or money at or above the threshold are involved.",
        f"--- THE RECOMMENDATION ---\n{str(request.get('proposed_content') or '')[:coo_review.MAX_FILE_CHARS]}",
    ]
    if request.get("deployment_commands"):
        parts.append("Steps for a person: " + " | ".join(request["deployment_commands"][:10]))
    return coo_review._clean("\n".join(parts))


async def route(audit_id: int, agent_id: str, request: dict[str, Any], reviewer: coo_review.Reviewer | None = None) -> dict[str, Any]:
    """Where one stored recommendation goes. Always returns an outcome dict (never None): the caller already knows the switch is on."""
    from app.agents import operational_approval as oa
    from app.api import authorization

    if agent_id not in ROUTABLE_ORIGINATORS:
        why = "The COO's own recommendations are never reviewed by the COO, so this one is yours to approve." if agent_id == "coo" else \
              "This kind of recommendation is decided by you, not delegated to the COO."
        return oa._outcome("ceo_required", why)
    reasons = exec_requests.sensitivity(request)
    if reasons:
        await oa._record(audit_id, operations_log.ROUTE_DECLINED, KIND_LABEL, reasons, None)
        return oa._outcome("ceo_required", "It involves animals, chemicals, statutory reporting or money, which are always yours to decide.", reasons=reasons)
    if request.get("risk_level") not in DELEGABLE_RISK:
        return oa._outcome("ceo_required", f"It is rated {request.get('risk_level')} risk, so it is yours to approve.")
    problems = coo_review.reported_problems(request, True)
    if problems:
        await oa._record(audit_id, operations_log.ROUTE_DECLINED, KIND_LABEL, problems, None)
        return oa._outcome("ceo_required", "It is missing part of its report (see the reasons), so it is yours to approve.", reasons=problems)
    coo = await coo_review.review(task_text(request, agent_id), reviewer)
    if not coo.approved:
        why = [coo.failure] if coo.failure else [f"{k}: {v}" for k, v in coo.reasons.items() if v]
        await oa._record(audit_id, operations_log.ROUTE_UNAVAILABLE if coo.failure else operations_log.ROUTE_DECLINED, KIND_LABEL, why, coo.as_dict())
        return oa._outcome("ceo_required", "The COO had a concern and passed it to you, so it is yours to approve.", reasons=why, coo=coo.as_dict())
    try:
        await authorization.apply_decision(audit_id, "approve", oa.ACTOR, remember=False)
    except Exception as exc:  # a person decided first, the gate refused, or the database failed: nothing was approved here
        detail = getattr(exc, "detail", None) or type(exc).__name__
        return oa._outcome("ceo_required", "It was decided by someone else or could not be recorded, so nothing was approved by the COO.", reasons=[str(detail)[:200]])
    await oa._record(audit_id, operations_log.ROUTE_APPROVED, KIND_LABEL, ["low-risk recommendation with no animals, chemicals, statutory reporting or money"], coo.as_dict(), force=True)
    return oa._outcome("coo_approved", "The COO approved this recommendation under your delegation.", kind=KIND_LABEL, coo=coo.as_dict())


def approval_note(outcome: dict[str, Any] | None) -> str:
    """One sentence for the CEO, written by code. A recommendation has no pull request, so it never mentions one."""
    if not outcome:
        return ""
    if outcome.get("route") == "coo_approved":
        return ("**COO-approved under your delegation:** this was a low-risk recommendation with no animals, chemicals, statutory reporting or money, "
                "so the COO reviewed and approved it for you. Approving only records the decision. Nothing was run.")
    return "**Ready for your approval:** " + str(outcome.get("message", "")).rstrip(".") + "."
