"""
The PLAN-APPROVAL tool: submit_plan_for_approval(plan_summary, files_to_change, risk_level). The CTO, CAO and CMO have it (never the COO, who decides them).

WHY. The CTO used to carry its authorization request inside its one big JSON answer. When that answer hit the token limit the JSON was cut off and
the request was lost ("structured JSON was incomplete or invalid"). With this tool the plan is submitted in a call of its own, in a SEPARATE model
round with its own token budget, so a long or cut-off final answer can no longer take the request down with it.

WHAT THE TOOL DOES. It validates the plan in code, builds a normal CTO authorization request from it (status forced to pending_human_approval,
executed forced false, the risk level raised by the same path and text floors the JSON route uses) and STAGES it on the answer's ToolOutcome.
cto_agent.get_cto_result then stores it with the answer in AgentAuditLog.authorizationRequest, exactly where a request in the JSON would go, so the
COO can review it (review_executive_task). The audit row does not exist until the answer is recorded, so the request's number (the audit entry id) is
only known then: code adds it to the reply. The tool itself cannot return it.

WHAT IT DOES NOT DO.
  * It writes NO file and opens NO pull request. A plan submitted here has no execution_plan (no file contents), so approving it records the CEO's
    decision and nothing runs. The code comes later, in the "show me the code" turn, through the existing execution_plan path (read-before-write,
    self-check and all). This tool is for approving the APPROACH.
  * It is not a way around any rule. A path an agent may not write (the guardrail files) is refused here as it is in a plan, at most 8 files,
    and the summary may not exceed 500 words.
  * It gives the CTO no authority. It only records a proposal for a human; a person (or the COO under delegation) still decides.

BOUNDED: one plan per answer, at most MAX_ATTEMPTS calls (a refused call can be corrected once or twice). Pure: no database, no network.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents import cto_authorization as ca
from app.agents import execution_plan as plan_rules

TOOL_NAME = "submit_plan_for_approval"
MAX_WORDS = 500
MAX_CHARS = 6000  # a hard backstop on the summary whatever its word count
MAX_FILES = plan_rules.MAX_FILES
MAX_ATTEMPTS = 3  # calls per answer, accepted or refused
MAX_ACTION_CHARS = 280  # the one-line action summary taken from the start of the plan
MAX_IMPACT_PLAN_CHARS = 1400  # how much of the plan the decision pages show under "Impact"

SPEC: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Submit your PLAN to the COO for approval. Use it AFTER you have written a concise plan in your answer, when the plan needs approval. Give the plan summary "
        "(the approach in under 500 words, no file contents), the repository paths of the files you would change, and the risk. It records a proposal "
        "for review: it writes no file and opens no pull request, and nothing is applied until the COO approves (or, if it is critical, the CEO). Do NOT also write the authorization "
        "metadata in your answer. Submit once per answer. Full file contents belong in a later turn, only if asked for the code."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan_summary": {"type": "string", "description": "The approach, concise, under 500 words, no file contents or code blocks."},
            "files_to_change": {"type": "array", "items": {"type": "string"},
                                "description": f"Repository paths of the files the plan would change or create (at most {MAX_FILES})."},
            "risk_level": {"type": "string", "enum": list(ca.RISK_LEVELS), "description": "Be honest; lean high when unsure. The system may raise it, never lower it."},
        },
        "required": ["plan_summary", "files_to_change", "risk_level"],
    },
}

Result = tuple[str, bool]  # (text for the model, ok)


def word_count(text: str) -> int:
    return len(text.split())


def _paths(raw: Any) -> tuple[list[str], str]:
    """The clean, de-duplicated paths, or ([], why not). Accepts plain strings and {"path": ...} objects (models sometimes send those)."""
    if not isinstance(raw, list):
        return [], "files_to_change must be a list of repository paths (it may be empty if no file changes)"
    if len(raw) > MAX_FILES:
        return [], f"files_to_change has {len(raw)} paths; the most a plan may change is {MAX_FILES}. Submit the first safe step and list the rest in your answer"
    clean: list[str] = []
    seen: set[str] = set()
    for item in raw:
        path = item.get("path") if isinstance(item, dict) else item
        try:
            checked = plan_rules.check_path(path)
        except plan_rules.PlanError as exc:
            return [], str(exc)
        if checked.lower() not in seen:
            seen.add(checked.lower())
            clean.append(checked)
    return clean, ""


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    match = re.search(r"(?<=[.!?])\s", flat)
    head = flat[: match.start()] if match and match.start() <= MAX_ACTION_CHARS else flat
    return head if len(head) <= MAX_ACTION_CHARS else head[: MAX_ACTION_CHARS - 1].rstrip() + "…"


def build_request(plan_summary: Any, files_to_change: Any, risk_level: Any) -> tuple[dict[str, Any] | None, str]:
    """
    (the authorization request, a note for the model) or (None, why it was refused). Pure. Nothing here can mark a request approved or executed:
    status, requires_human_approval and executed are forced exactly as cto_authorization does for the JSON route.
    """
    if not isinstance(plan_summary, str) or not plan_summary.strip():
        return None, "plan_summary is empty. Give the approach in a few sentences."
    summary = plan_summary.strip()
    if word_count(summary) > MAX_WORDS:
        return None, f"plan_summary is {word_count(summary)} words; the limit is {MAX_WORDS}. Shorten it to the approach and the files, with no file contents."
    if len(summary) > MAX_CHARS:
        return None, f"plan_summary is {len(summary)} characters; the limit is {MAX_CHARS}. Shorten it."
    if plan_rules.redact_secrets(summary) != summary:
        return None, "plan_summary contains something that looks like a key or secret. Remove it and submit again."
    paths, why = _paths(files_to_change)
    if why:
        return None, f"files_to_change was refused: {why}."
    declared = ca._risk(risk_level)
    notes: list[str] = []
    if declared is None:
        declared = "high"
        notes.append("risk_level was missing or unknown, so it was set to high")
    floor = ca.risk_floor([], summary)  # the same text patterns the JSON route uses (a secret, a destructive command written into the plan)
    if paths:  # the same path floor: a plan touching the backend, dependencies or the database schema is at least high
        floor = plan_rules.higher_risk(floor, "high" if plan_rules.plan_risk_floor({"files_to_update": [{"path": p} for p in paths]}) == "high" else "low")
    level = declared if ca._ORDER[declared] >= ca._ORDER[floor] else floor
    if level != declared:
        notes.append(f"risk_level was raised from {declared} to {level} because the plan touches something that warrants it")
    action = _first_sentence(summary)
    listing = ("Files the plan would change: " + ", ".join(paths) + ". ") if paths else "No files are named. "
    impact = (listing + "Plan: " + " ".join(summary.split()))[: ca.MAX_IMPACT_CHARS - 40].rstrip()
    if len(listing) + 6 + len(" ".join(summary.split())) > ca.MAX_IMPACT_CHARS - 40:
        impact += " (cut here; the full plan is in the CTO's answer)"
    request = {
        "action_summary": action,
        "executive_summary": None,
        "risk_level": level,
        "impact": impact,
        "proposed_code_diff": None,
        "proposed_commands": [],
        "rollback_plan": "Nothing is changed by approving this plan. The code is only written in a later step, as a pull request that is reviewed before anything is merged.",
        "execution_plan": None,
        "plan_summary": summary,
        "files_to_change": paths,
        "submitted_via": TOOL_NAME,
        "status": ca.STATUS_PENDING,
        "requires_human_approval": True,
        "executed": False,
    }
    return request, "; ".join(notes)


def submit(args: dict[str, Any], outcome: Any) -> Result:
    """
    Run one submit_plan_for_approval call. Stages the request on ``outcome.plan_request`` (the caller stores it with the answer). NEVER RAISES.
    Bounded: one accepted plan per answer, at most MAX_ATTEMPTS calls.
    """
    try:
        outcome.plan_attempts = getattr(outcome, "plan_attempts", 0) + 1
        if getattr(outcome, "plan_request", None) is not None:
            return "A plan was already submitted in this answer. Only one plan per answer; list any further steps in your answer as follow-ups.", False
        if outcome.plan_attempts > MAX_ATTEMPTS:
            return f"Submission limit reached ({MAX_ATTEMPTS} per answer). Describe the plan in your answer instead.", False
        request, note = build_request(args.get("plan_summary"), args.get("files_to_change"), args.get("risk_level"))
        if request is None:
            return f"NOT SUBMITTED: {note}", False
        outcome.plan_request = request
        extra = f" Note: {note}." if note else ""
        return (f"Plan staged for the COO's review (risk {request['risk_level']}, {len(request['files_to_change'])} file(s) named). The system records it with "
                f"this answer and adds its number to your reply. It is NOT approved: the COO decides (a critical plan goes to the CEO), and nothing is applied until then. Do not write any "
                f"authorization metadata in your answer, and do not say it is approved.{extra}"), True
    except Exception as exc:  # a tool must never take the app down
        return f"ERROR: the plan could not be submitted ({type(exc).__name__}). Describe the plan in your answer.", False

def staged_plan_note(audit_id: int | None) -> str:
    """The line CODE adds under a plan submitted with submit_plan_for_approval: its number, and the truth that it is waiting, not approved."""
    if audit_id is None:
        return ("**Plan not recorded:** I prepared this plan for approval, but the audit log was unavailable, so nobody can review it yet. "
                "Ask me again in a moment.")
    return (f"**Plan submitted for approval (request #{audit_id}):** it is waiting for the COO's review. The COO approves routine plans, and a critical one goes to you with an "
            "explanation. Approving a plan records the decision only; no code is written until it is asked for.")