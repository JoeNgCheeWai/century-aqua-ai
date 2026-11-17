"""
The COO's AUTHORITY tools: the COO is the SOLE approver of executive tasks. COO ONLY. Four tools:

review_executive_task(task_id, decision, reasoning)   decision is 'approve', 'reject' or 'escalate'. ``task_id`` is the audit entry number. Any
                                                      pending task of the CTO, CAO or CMO (never the COO's own).
  approve   -> the SAME shared approval path a person's click uses (authorization.apply_decision): recorded as AI_COO, atomic and final, the stored plan
               re-validated, run once, a pull request opened and NEVER merged. The executive then proceeds on its own.
  reject    -> the same path with 'reject': the task is cancelled, nothing runs, the reason is on record.
  escalate  -> the task stays Pending for the CEO. If it is CRITICAL (see coo_escalation.critical_reasons) the CEO is ALSO NOTIFIED, by code.
notify_ceo_of_critical_decision(task_id, explanation, recommendation)   tell the CEO, in plain language, why a task needs them (a notification, not a box).
get_all_audit_logs(agent_id, action_type, limit)      read-only: decision METADATA across all executives, never the text of a question or an answer.
generate_audit_report(period)                         code-written findings for a day, week or month (audit_report.py): what the COO approved, what is
                                                      waiting, and anything that looks wrong (unapproved execution, an approval that breaks today's rules).

THE RULES, ALL IN CODE (the COO is a model; it is never the only gate):
  * COO ONLY. Only the COO's tool list holds these names (exec_tools.SPECS), and handle() refuses any other role.
  * ONLY A PENDING EXECUTIVE TASK from the CTO, CAO or CMO. A decision is final: a task someone else already decided is refused.
  * CODE FLOOR ON APPROVAL. coo_escalation.critical_reasons lists what is critical (schema, security, governance, dependencies, billing, new integrations,
    commands, critical risk and, for the CAO and CMO, what code reserves for the CEO). If there is any, 'approve' does NOT approve: the task is escalated
    and the CEO is notified. completeness_reasons (not fully reported, failed self-check) refuse an approval too, but send it BACK: reject it with the reasons.
  * THE SAME OFF SWITCH. approve and reject need COO_AUTO_APPROVAL on. Escalate and notify never need it: they only hand a task to the CEO.
  * EVERYTHING IS ON THE RECORD, automatically (task_events): the executive's own audit log AND the COO's. The decision and its reasoning are also appended
    to the task's own row under ``authorizationRequest.coo_decisions`` (history, never overwritten).
  * BOUNDED per answer: MAX_DECISIONS_PER_TURN decisions, MAX_NOTIFICATIONS_PER_TURN notifications and MAX_AUDIT_QUERIES_PER_TURN audit reads or reports.

What this does NOT do: it does not read the code inside a file, so the COO decides from the summary, impact and the file PATHS, with the code floor
under it. A pull request opened by an approval is not a live change: a person still reviews and merges it. A notification is IN-APP only (no email or push).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents import coo_escalation
from app.agents import execution_plan as plan_rules
from app.governance import admin_store, audit_report, ceo_notifications, db, operations_log, task_events
from app.governance.scoring import REVIEW_THRESHOLD

logger = logging.getLogger(__name__)

REVIEW_TOOL, AUDIT_TOOL = "review_executive_task", "get_all_audit_logs"
NOTIFY_TOOL, REPORT_TOOL = "notify_ceo_of_critical_decision", "generate_audit_report"
TOOL_NAMES = (REVIEW_TOOL, NOTIFY_TOOL, AUDIT_TOOL, REPORT_TOOL)
ROLE = "coo"  # the only role that may use any of these tools
DECISIONS = ("approve", "reject", "escalate")
AGENTS = ("cao", "coo", "cto", "cmo")
DECIDABLE = ("cto", "cao", "cmo")  # the executives whose pending tasks the COO decides; never its own
KIND_LABEL = "executive task"
MAX_DECISIONS_PER_TURN = 5
MAX_NOTIFICATIONS_PER_TURN = 3
MAX_AUDIT_QUERIES_PER_TURN = 3
MAX_REASONING_CHARS = 600
MAX_LIMIT, DEFAULT_LIMIT = 25, 20
MAX_ID = 2_000_000_000  # fits the database's integer column
EXECUTION_FAILED = ("failed", "refused", "unconfigured", "already_claimed")

# action_type -> (SQL condition on AgentAuditLog, parameters). Fixed text only: nothing a model writes is ever put into SQL, only passed as a parameter.
ACTION_TYPES: dict[str, tuple[str, tuple[Any, ...]]] = {
    "pending": ('"humanAction" = CAST(%s AS "HumanAction")', ("Pending",)),
    "approved": ('"humanAction" = CAST(%s AS "HumanAction")', ("Approved",)),
    "rejected": ('"humanAction" = CAST(%s AS "HumanAction")', ("Rejected",)),
    "modified": ('"humanAction" = CAST(%s AS "HumanAction")', ("Modified",)),
    "coo_approved": ("\"authorizationRequest\"->'operational_review'->>'route' = %s", (operations_log.ROUTE_APPROVED,)),
    "coo_escalated": ("\"authorizationRequest\"->'operational_review'->>'route' IN (%s, %s)",
                      (operations_log.ROUTE_DECLINED, operations_log.ROUTE_UNAVAILABLE)),
    "coo_decision": ("jsonb_exists(\"authorizationRequest\", 'coo_decisions')", ()),
    "execution_failed": ("\"authorizationRequest\"->'execution'->>'status' IN (%s, %s, %s, %s)", EXECUTION_FAILED),
    "low_score": ('"constitutionalScore" < %s', (REVIEW_THRESHOLD,)),
}

Result = tuple[str, bool, str, int | None]  # (text for the model, ok, target, rows): the shape every executive tool returns


def _spec(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description, "input_schema": {"type": "object", "properties": props, "required": required}}


SPECS: list[dict[str, Any]] = [
    _spec(REVIEW_TOOL,
          "Decide ONE pending task of the CTO, CAO or CMO (an audit entry from get_all_audit_logs with action_type 'pending'). 'approve' records your approval and, if the task "
          "has a plan, opens a pull request that a person still merges, and the executive then proceeds on its own; 'reject' cancels it; 'escalate' sends it to the CEO. Code "
          "refuses an approval, and escalates instead and notifies the CEO, for anything critical (schema, security, governance, dependencies, billing, new integrations, "
          "commands, critical risk). A decision is final. Give a brief, specific reason.",
          {"task_id": {"type": "string", "description": "The audit entry number of the task, e.g. \"42\"."},
           "decision": {"type": "string", "enum": list(DECISIONS)},
           "reasoning": {"type": "string", "description": "Why, in one or two plain sentences. It is stored on the record."}},
          ["task_id", "decision", "reasoning"]),
    _spec(NOTIFY_TOOL,
          "Tell the CEO about a CRITICAL decision, in plain language, as a notification (not a box). Use it after you escalate a critical task. explanation says WHY it is "
          "critical in words a non-technical CEO understands (no file names or jargon); recommendation says what you advise. The CEO can discuss it with you before deciding.",
          {"task_id": {"type": "string", "description": "The audit entry number of the critical task."},
           "explanation": {"type": "string", "description": "Why this is critical, in plain language."},
           "recommendation": {"type": "string", "description": "What you recommend the CEO decides, and why."}},
          ["task_id", "explanation", "recommendation"]),
    _spec(AUDIT_TOOL,
          "Read the audit log across ALL executives: past actions, decisions and errors. Read-only; decision metadata only (who, when, risk, decision, pull request status, "
          "files a task would change), never the text of a question or an answer. action_type narrows it: " + ", ".join(ACTION_TYPES) + ". Use agent_id 'all' for every executive.",
          {"agent_id": {"type": "string", "enum": [*AGENTS, "all"]},
           "action_type": {"type": "string", "enum": list(ACTION_TYPES), "description": "Optional."},
           "limit": {"type": "integer", "description": f"How many entries, 1-{MAX_LIMIT} (default {DEFAULT_LIMIT}), newest first."}},
          ["agent_id"]),
    _spec(REPORT_TOOL,
          "Generate a code-written audit report for a period: day, week or month. It counts what each executive submitted and what was decided, and flags anything that looks wrong "
          "(a pull request with no approval, an approval nobody is recorded for, an approval of something now critical, a task waiting too long). It also says what it cannot see. "
          "Report only what it says; never add a finding it did not give.",
          {"period": {"type": "string", "enum": list(audit_report.PERIODS)}}, ["period"]),
]


# ------------------------------------------------------------------------------------------------ small helpers
def _fail(text: str, target: str = "") -> Result:
    return text, False, target, None


def parse_task_id(value: Any) -> int | None:
    """The audit entry number from what the model wrote ("42", "#42", 42), or None. A bool, a negative or an absurd number is not an id."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().lstrip("#").isdigit():
        number = int(value.strip().lstrip("#"))
    else:
        return None
    return number if 0 < number <= MAX_ID else None


def clean_reasoning(value: Any) -> str:
    """The COO's reason, trimmed, capped and with anything that looks like a key masked. '' if there is none (a decision needs a reason)."""
    return plan_rules.redact_secrets(" ".join(value.split()))[:MAX_REASONING_CHARS] if isinstance(value, str) else ""


def _clamp(value: Any, default: int, high: int) -> int:
    return default if not isinstance(value, int) or isinstance(value, bool) else max(1, min(high, value))


def _coo_verdict(decision: str, reasoning: str) -> dict[str, Any]:
    """The same shape coo_review.CooVerdict.as_dict() stores, so the diagnostic and the Operations Summary read it unchanged."""
    return {"approved": decision == "approve", "reasons": {"decision": reasoning}, "summary": reasoning, "failure": None}


# ------------------------------------------------------------------------------------------------ the database
def _read_task_sync(audit_id: int) -> dict[str, Any] | None:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT "id","agentId","humanAction","humanActionBy","authorizationRequest" FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))
        rows = db.fetch_all(cur)
    return rows[0] if rows else None


def _append_decision_sync(audit_id: int, entry: dict[str, Any]) -> bool:
    """Append one decision to the task's own history (``coo_decisions``). Append only: an earlier decision is never overwritten or removed."""
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE "AgentAuditLog" SET "authorizationRequest" = jsonb_set("authorizationRequest", \'{coo_decisions}\', '
            'COALESCE("authorizationRequest"->\'coo_decisions\', CAST(\'[]\' AS jsonb)) || CAST(%s AS jsonb)) '
            'WHERE "id"=%s AND jsonb_typeof("authorizationRequest") = \'object\' RETURNING "id"',
            (db.json_param([entry]), audit_id))
        return bool(db.fetch_all(cur))


def _note_sync(audit_id: int, entry: dict[str, Any]) -> None:
    with db.transaction() as conn:
        conn.cursor().execute(
            'INSERT INTO "AdminAuditLog" ("actorId","action","targetType","targetId","details") VALUES (%s,%s,%s,%s,CAST(%s AS jsonb))',
            ("ai_coo", "coo_task_decision", "AgentAuditLog", str(audit_id)[:60], db.json_param(entry)))


def _audit_sync(agent_id: str | None, action_type: str | None, limit: int) -> list[dict[str, Any]]:
    clauses, params = [], []
    if agent_id:
        clauses.append('"agentId" = %s')
        params.append(agent_id)
    if action_type:
        condition, extra = ACTION_TYPES[action_type]
        clauses.append(condition)
        params.extend(extra)
    sql = ('SELECT "id","agentId","timestamp","humanAction","humanActionBy","constitutionalScore","authorizationRequest" FROM "AgentAuditLog"'
           + (" WHERE " + " AND ".join(clauses) if clauses else "") + ' ORDER BY "timestamp" DESC, "id" DESC LIMIT %s')
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(sql, (*params, limit))
        return db.fetch_all(cur)


async def _best_effort(function: Any, *args: Any) -> bool:
    """Run one logging write without ever raising or hanging the answer. True if it ran."""
    if not db.is_configured():
        return False
    try:
        await asyncio.wait_for(db.run(function, *args), timeout=2.0)
        return True
    except Exception as exc:
        logger.error("coo_tools: could not write a record (%s)", type(exc).__name__)
        return False


async def _record(audit_id: int, decision: str, reasoning: str, outcome: str, agent_id: str | None = None, risk: str | None = None) -> bool:
    """
    Put the COO's decision on the record. Always APPENDS to ``coo_decisions`` (the full history, never overwritten). Also writes ``operational_review``
    so the Operations Summary counts it: an approval overwrites it (as the shared automatic path does), an escalation writes it only if the task has no
    review yet (the write is guarded, so an earlier review is never replaced). And it writes the AUTOMATIC task event (task_events) to the executive's
    own audit log and to the COO's, so no one has to log anything by hand. Returns True if the history was written.
    """
    from app.agents import operational_approval as oa

    entry = {"decision": decision, "reasoning": reasoning, "outcome": outcome, "by": oa.ACTOR, "at": oa._stamp(), "via": REVIEW_TOOL}
    written = await _best_effort(_append_decision_sync, audit_id, entry)
    if outcome == "approved":
        await oa._record(audit_id, operations_log.ROUTE_APPROVED, KIND_LABEL, [reasoning], _coo_verdict("approve", reasoning), force=True)
    elif outcome == "escalated":
        await oa._record(audit_id, operations_log.ROUTE_DECLINED, KIND_LABEL, [reasoning], _coo_verdict("escalate", reasoning))
    await _best_effort(_note_sync, audit_id, {"decision": decision, "outcome": outcome})
    await task_events.record(audit_id, agent_id, "coo_decision", decision=decision, outcome=outcome, reasoning=reasoning, risk=risk)
    print(f"COO decision: task={audit_id} decision={decision} outcome={outcome}", flush=True)
    return written


# ------------------------------------------------------------------------------------------------ review_cto_task
async def _apply(audit_id: int, decision: str) -> dict[str, Any] | str:
    """The shared approval path, as the delegate. The decision dict on success, or a plain error string (nothing was decided here)."""
    from app.agents import operational_approval as oa
    from app.api import authorization

    try:
        return await authorization.apply_decision(audit_id, decision, oa.ACTOR, remember=False)
    except Exception as exc:  # a person decided first, the gate refused, or the database failed: nothing was decided here
        detail = getattr(exc, "detail", None) or type(exc).__name__
        logger.error("coo_tools: could not %s audit #%s (%s)", decision, audit_id, type(exc).__name__)
        return f"NOT DECIDED: task #{audit_id} could not be {decision}d ({str(detail)[:200]}). Nothing was changed by you."


async def _load_pending_task(audit_id: int) -> tuple[dict[str, Any] | None, str, str]:
    """(the stored request, the executive's id, "") for a pending task of the CTO, CAO or CMO, else (None, "", a plain reason). Never raises past the caller's guard."""
    row = await db.run(_read_task_sync, audit_id)
    if row is None:
        return None, "", f"there is no audit entry #{audit_id}."
    agent = str(row["agentId"])
    if agent not in DECIDABLE:
        return None, "", f"audit entry #{audit_id} is a {agent.upper()} entry. You decide the CTO's, CAO's and CMO's tasks, never your own."
    request = admin_store.parse_json(row.get("authorizationRequest"))
    if not isinstance(request, dict):
        return None, agent, f"audit entry #{audit_id} has no authorization request, so there is nothing to decide."
    if str(row["humanAction"]) != "Pending":
        who = f" by {row['humanActionBy']}" if row.get("humanActionBy") else ""
        return None, agent, f"task #{audit_id} was already {row['humanAction']}{who}. A decision is final; nothing was changed."
    return request, agent, ""


def _reserved_for_ceo(request: dict[str, Any], agent: str = "") -> list[str]:
    """
    What code reserves for the CEO that the path and word rules do not see, for a CAO or CMO item: animals, chemicals, statutory reporting or money
    (exec_requests.sensitivity), and any risk above low or medium (exec_approval.DELEGABLE_RISK). For a RECOMMENDATION this MIRRORS exec_approval.ceo_gate,
    which authorization.apply_decision enforces on the COO whoever calls it. A plan a CAO or CMO submitted through submit_plan_for_approval has no ``kind``,
    so apply_decision would not see it: the same rule is applied here, keyed on WHO submitted it. A CTO plan is not covered (a CTO interface task may
    legitimately say "feed"): its critical list is coo_escalation.critical_reasons.
    """
    from app.agents import exec_approval, exec_requests

    if request.get("kind") != exec_requests.KIND and not (agent in ("cao", "cmo") and request.get("submitted_via")):
        return []
    reasons = list(exec_requests.sensitivity(request))
    if request.get("risk_level") not in exec_approval.DELEGABLE_RISK:
        reasons.append(f"it is rated {request.get('risk_level')} risk, and code reserves a {agent.upper() or 'CAO or CMO'} item above medium risk for the CEO")
    return reasons

def _approved_text(audit_id: int, done: dict[str, Any]) -> str:
    execution = done.get("execution") if isinstance(done.get("execution"), dict) else None
    if execution is None:
        tail = "It had no execution plan, so approving recorded the decision and nothing was run."
    elif execution.get("status") == "succeeded":
        tail = f"It opened a pull request: {execution.get('pr_url')}. It is NOT merged or live: a person still reviews and merges it."
    else:
        tail = f"The pull request could not be completed ({execution.get('status')}): {str(execution.get('error') or 'no detail')[:200]}. Nothing was merged."
    return f"Task #{audit_id} was approved by the COO. The executive may now proceed. {tail}"


def _plain_title(request: dict[str, Any], agent: str) -> str:
    return f"{agent.upper()} task needs your decision: {str(request.get('action_summary') or 'a task')[:140]}"


async def _notify(audit_id: int, agent: str, request: dict[str, Any], why: str, advice: str, risk: str | None) -> tuple[int | None, str]:
    """Create the CEO's notification and the event for it. Returns (notification id or None, plain message). NEVER RAISES."""
    new_id, message = await ceo_notifications.create(audit_id, agent, _plain_title(request, agent), why, advice, risk)
    if new_id is not None:
        await task_events.record(audit_id, agent, "ceo_notified", notification_id=new_id, risk=risk, explanation=why)
    return new_id, message


async def review_executive_task(task_id: Any, decision: Any, reasoning: Any) -> Result:
    """Decide one pending task of the CTO, CAO or CMO: approve, reject or escalate. NEVER RAISES. Returns (text for the model, ok, target, rows)."""
    from app.agents import operational_approval as oa

    audit_id = parse_task_id(task_id)
    target = f"task:{audit_id}" if audit_id else "task"
    if audit_id is None:
        return _fail('ERROR: task_id must be the audit entry number of the task, for example "42".', target)
    if decision not in DECISIONS:
        return _fail(f"ERROR: decision must be one of {', '.join(DECISIONS)}.", target)
    reason = clean_reasoning(reasoning)
    if not reason:
        return _fail("ERROR: give a reason. Every decision is stored with the COO's reasoning.", target)
    if not db.is_configured():
        return _fail("ERROR: the audit log is not available, so nothing was decided.", target)
    request, agent, problem = await _load_pending_task(audit_id)
    if request is None:
        return _fail(f"ERROR: {problem}", target)
    risk = request.get("risk_level") if request.get("risk_level") in ("low", "medium", "high", "critical") else None
    critical = [*coo_escalation.critical_reasons(request), *_reserved_for_ceo(request, agent)]

    if decision == "escalate":
        await _record(audit_id, decision, reason, "escalated", agent, risk)
        if critical:  # a critical escalation reaches the CEO as a notification made by code, so it cannot be forgotten
            notified, how = await _notify(audit_id, agent, request, "; ".join(dict.fromkeys(critical)) + ". " + reason, reason, "critical")
            tail = (f" The CEO was notified (notification #{notified}). Now call {NOTIFY_TOOL} to give them the plain-language explanation and your recommendation."
                    if notified else f" The CEO could NOT be notified ({how}); say so plainly.")
        else:
            tail = f" It is not critical, so no notification was sent; it waits in the CEO's decision list. Use {NOTIFY_TOOL} if the CEO should hear about it now."
        return f"Task #{audit_id} was escalated to the CEO and stays Pending with your reasoning on its record. Nothing was approved or run.{tail}", True, target, 1
    if not oa.enabled():
        return _fail(f"NOT DECIDED: COO approval authority is switched OFF ({oa.ENV_SWITCH} is not on), so task #{audit_id} stays Pending. "
                     "Escalate it instead, and tell the CEO plainly that you could not approve or reject it.", target)
    if decision == "approve":
        if critical:  # the code floor: a critical task is escalated whatever the COO decided, and the CEO is told
            why = "; ".join(dict.fromkeys(critical))
            await _record(audit_id, "approve", f"{reason} [Code escalated it instead: {why}]"[:MAX_REASONING_CHARS + 400], "escalated", agent, risk)
            notified, how = await _notify(audit_id, agent, request, why + ". " + reason, reason, "critical")
            note = f"The CEO was notified (notification #{notified})." if notified else f"The CEO could NOT be notified ({how}); say so plainly."
            return (f"NOT APPROVED: code escalated task #{audit_id} to the CEO because {why}. {note} Now call {NOTIFY_TOOL} to explain it in plain language. "
                    "Never describe it as approved.", False, target, 1)
        problems = coo_escalation.completeness_reasons(request)
        if problems:  # not critical, but not ready: it goes back, it does not go to the CEO
            return _fail(f"NOT APPROVED: task #{audit_id} is not ready: {'; '.join(problems)}. Reject it with these reasons so the executive fixes it and resubmits. "
                         "It is not critical, so the CEO is not involved.", target)
    done = await _apply(audit_id, decision)
    if isinstance(done, str):
        return _fail(done, target)
    await _record(audit_id, decision, reason, "approved" if decision == "approve" else "rejected", agent, risk)
    if decision == "reject":  # (an execution is logged by apply_decision itself, for every decider)
        return f"Task #{audit_id} was rejected by the COO and cancelled. Nothing was run. The decision is final and your reasoning is on its record.", True, target, 1
    return _approved_text(audit_id, done), True, target, 1


async def notify_ceo_of_critical_decision(task_id: Any, explanation: Any, recommendation: Any) -> Result:
    """Tell the CEO, in plain language, why a task is critical. Refreshes the notification code already made at escalation. NEVER RAISES."""
    audit_id = parse_task_id(task_id)
    target = f"task:{audit_id}" if audit_id else "task"
    if audit_id is None:
        return _fail('ERROR: task_id must be the audit entry number of the task, for example "42".', target)
    why = ceo_notifications.clean(explanation, ceo_notifications.MAX_EXPLANATION)
    advice = ceo_notifications.clean(recommendation, ceo_notifications.MAX_RECOMMENDATION)
    if not why or not advice:
        return _fail("ERROR: give both a plain-language explanation of WHY it is critical and your recommendation.", target)
    if not db.is_configured():
        return _fail("ERROR: the database is not available, so the CEO could not be notified.", target)
    request, agent, problem = await _load_pending_task(audit_id)
    if request is None:
        return _fail(f"ERROR: {problem}", target)
    notified, how = await _notify(audit_id, agent, request, why, advice, "critical")
    if notified is None:
        return _fail(f"NOT NOTIFIED: the CEO could not be notified ({how}). Say so plainly and do not claim they were told.", target)
    return (f"The CEO was notified (notification #{notified}, {how}): it shows in their Mission Control bell and decision list. It is in-app only, so it reaches them "
            "when Mission Control is open. They can discuss it with you before deciding, and the task stays Pending until they decide.", True, target, 1)


async def generate_audit_report(period: Any) -> Result:
    """The code-written audit report for a day, week or month. NEVER RAISES. Returns (text for the model, ok, target, rows)."""
    text, ok = await audit_report.generate(period)
    return text, ok, "audit_report", None


# ------------------------------------------------------------------------------------------------ get_audit_logs
def _row_text(row: dict[str, Any]) -> str:
    """One audit entry as a few lines of METADATA. Never a question or an answer."""
    request = admin_store.parse_json(row.get("authorizationRequest"))
    request = request if isinstance(request, dict) else {}
    review = request.get("operational_review") if isinstance(request.get("operational_review"), dict) else {}
    execution = request.get("execution") if isinstance(request.get("execution"), dict) else {}
    line = (f"[{row['id']}] {admin_store.iso_utc(row.get('timestamp'))} | {row['agentId']} | decision: {row['humanAction']} by {row.get('humanActionBy') or 'nobody'} "
            f"| score {row.get('constitutionalScore')} | risk: {request.get('risk_level') or 'n/a'} | route: {review.get('route') or 'none'} "
            f"| pull request: {execution.get('status') or 'none'} | {str(request.get('action_summary') or 'no request')[:160]}")
    plan = request.get("execution_plan") if isinstance(request.get("execution_plan"), dict) else None
    if plan:
        files = [f"{i.get('path')} ({len(str(i.get('content', '')))} chars)" for i in plan.get("files_to_update", []) if isinstance(i, dict)]
        line += "\n    changes: " + ", ".join(files[:6]) + (f" and {len(files) - 6} more" if len(files) > 6 else "")
    if request.get("files_to_change"):  # a plan submitted through submit_plan_for_approval names its files here (it has no file contents)
        names = [str(p) for p in request["files_to_change"] if isinstance(p, str)]
        line += "\n    would change: " + ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
    if str(row["agentId"]) in DECIDABLE and str(row["humanAction"]) == "Pending" and request:
        critical = [*coo_escalation.critical_reasons(request), *_reserved_for_ceo(request, str(row["agentId"]))]
        problems = coo_escalation.completeness_reasons(request)
        line += "\n    impact: " + str(request.get("impact") or "not stated")[:200]
        line += "\n    code check: " + (("CRITICAL, so it is the CEO's: " + "; ".join(dict.fromkeys(critical))) if critical
                                         else (("not ready: " + "; ".join(problems)) if problems else "no critical reason found; the COO may decide it"))
    decisions = request.get("coo_decisions") if isinstance(request.get("coo_decisions"), list) else []
    if decisions and isinstance(decisions[-1], dict):
        last = decisions[-1]
        line += f"\n    COO decision: {last.get('decision')} -> {last.get('outcome')} ({str(last.get('reasoning') or '')[:160]})"
    return line


async def get_all_audit_logs(agent_id: Any, action_type: Any = None, limit: Any = None) -> Result:
    """Read recent audit entries across ALL the executives, newest first. NEVER RAISES. Returns (text, ok, target, rows)."""
    who = agent_id.strip().lower() if isinstance(agent_id, str) else ""
    if who not in (*AGENTS, "all"):
        return _fail(f"ERROR: agent_id must be one of {', '.join([*AGENTS, 'all'])}.", "audit_log")
    kind = action_type if isinstance(action_type, str) and action_type.strip() else None
    if kind is not None and kind not in ACTION_TYPES:
        return _fail(f"ERROR: action_type must be one of {', '.join(ACTION_TYPES)} (or left out).", "audit_log")
    if not db.is_configured():
        return _fail("ERROR: the audit log is not available", "audit_log")
    rows = await db.run(_audit_sync, None if who == "all" else who, kind, _clamp(limit, DEFAULT_LIMIT, MAX_LIMIT))
    if not rows:
        return "The audit log has no matching entries.", True, "audit_log", 0
    return f"{len(rows)} audit entr(ies), newest first (metadata only):\n" + "\n".join(_row_text(r) for r in rows), True, "audit_log", len(rows)


# ------------------------------------------------------------------------------------------------ the entry point exec_tools calls
async def handle(role: str, name: str, args: dict[str, Any], outcome: Any) -> Result:
    """Run one of the four tools for ``role``. Refuses every role but the COO. Bounded per answer. NEVER RAISES."""
    if role != ROLE or name not in TOOL_NAMES:
        return _fail(f"{name} is not one of your tools")
    try:
        if name == REVIEW_TOOL:
            if getattr(outcome, "coo_decisions", 0) >= MAX_DECISIONS_PER_TURN:
                return _fail(f"Decision limit reached ({MAX_DECISIONS_PER_TURN} per answer). Tell the CEO which tasks are still waiting.", "task")
            outcome.coo_decisions = getattr(outcome, "coo_decisions", 0) + 1
            return await review_executive_task(args.get("task_id"), args.get("decision"), args.get("reasoning"))
        if name == NOTIFY_TOOL:
            if getattr(outcome, "ceo_notifications", 0) >= MAX_NOTIFICATIONS_PER_TURN:
                return _fail(f"Notification limit reached ({MAX_NOTIFICATIONS_PER_TURN} per answer). Tell the CEO plainly which critical tasks you could not announce.", "task")
            outcome.ceo_notifications = getattr(outcome, "ceo_notifications", 0) + 1
            return await notify_ceo_of_critical_decision(args.get("task_id"), args.get("explanation"), args.get("recommendation"))
        if getattr(outcome, "audit_queries", 0) >= MAX_AUDIT_QUERIES_PER_TURN:
            return _fail(f"Audit-read limit reached ({MAX_AUDIT_QUERIES_PER_TURN} per answer). Work with what you have already read.", "audit_log")
        outcome.audit_queries = getattr(outcome, "audit_queries", 0) + 1
        if name == REPORT_TOOL:
            return await generate_audit_report(args.get("period"))
        return await get_all_audit_logs(args.get("agent_id"), args.get("action_type"), args.get("limit"))
    except Exception as exc:  # a tool must never take the app down
        logger.error("coo_tools: %s failed (%s)", name, type(exc).__name__)
        return _fail(f"ERROR: {name} could not be completed ({type(exc).__name__}). Nothing was changed.")
