"""
THE COO'S AUDIT REPORT: findings for a day, a week or a month, WRITTEN BY CODE from the audit log, so a model can never invent a number or a finding.

generate_audit_report(period) reads, read-only and from existing tables: AgentAuditLog (every executive answer that carried an authorization request,
with its decision and what executing it did) and AdminAuditLog (the automatic task events, task_events.py). It counts them and runs these checks.
A finding names the task, so the COO can open it with get_all_audit_logs.

THE CHECKS (each is a plain rule on recorded facts):
  executed_without_approval   a pull request was recorded for a task whose decision is NOT Approved. Should be impossible.
  approved_without_actor      a task is Approved but nobody is recorded as having decided it.
  coo_approved_a_critical     the COO approved a task that today's code floor calls critical (coo_escalation.critical_reasons). The rules may have
                              changed since, so this is a finding to look at, not proof of wrongdoing.
  approved_but_not_acted_on   (note) approved with a plan, but no pull request is recorded: it may be stuck.
  waiting_too_long            a task has been Pending for more than WAITING_DAYS days.
  missing_events              (note) a task has no plan_submitted event: it predates the event log, or a log write was skipped.

WHAT IT CANNOT SEE, and says so in every report: work done OUTSIDE this system (anything that never went through an executive answer), a change
merged on GitHub (merging is not tracked), anything a skipped log write lost, and anything before the period. "No findings" means the checks found
nothing in the RECORDED data. It is not proof that nothing happened.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.governance import admin_store, db

logger = logging.getLogger(__name__)

PERIODS = {"day": 1, "week": 7, "month": 30}
_ALIASES = {"daily": "day", "today": "day", "weekly": "week", "monthly": "month", "1d": "day", "7d": "week", "30d": "month"}
WAITING_DAYS = 3
MAX_TASKS = 500
MAX_FINDINGS_SHOWN = 20
COO_ACTOR = "AI_COO (delegated by the CEO)"
CANNOT_SEE = ("Work done outside this system, a change merged on GitHub (merging is not tracked), anything a skipped log write lost, and anything "
              "before the period. No findings means the checks found nothing in the RECORDED data, not that nothing happened.")


def period_days(period: Any) -> int | None:
    """Days for 'day', 'week' or 'month' (any case, also 'daily', 'weekly', 'monthly', '7d'), or None if it is not one of them."""
    text = str(period or "").strip().lower()
    return PERIODS.get(_ALIASES.get(text, text))


def _tasks_sync(since: datetime) -> list[dict[str, Any]]:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT "id","agentId","timestamp","humanAction","humanActionBy","humanActionAt","authorizationRequest" FROM "AgentAuditLog" '
                    'WHERE "timestamp">=%s AND jsonb_typeof("authorizationRequest")=%s ORDER BY "timestamp" DESC LIMIT %s', (db.naive_utc(since), "object", MAX_TASKS))
        return db.fetch_all(cur)


def _events_sync(since: datetime) -> list[dict[str, Any]]:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT "targetId","details" FROM "AdminAuditLog" WHERE "action"=%s AND "timestamp">=%s LIMIT %s', ("task_event", db.naive_utc(since), MAX_TASKS * 8))
        return db.fetch_all(cur)


def analyse(rows: list[dict[str, Any]], events: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Count the tasks and run the checks. PURE: no database, so every check is tested directly."""
    from app.agents import coo_escalation

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    seen: dict[str, set[str]] = {}
    for event in events:
        body = admin_store.parse_json(event.get("details"))
        if isinstance(body, dict) and body.get("event"):
            seen.setdefault(str(event.get("targetId")), set()).add(str(body["event"]))
    by_agent: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    by_coo: Counter[str] = Counter()
    findings: list[dict[str, Any]] = []

    def flag(kind: str, row: dict[str, Any], why: str, severity: str = "review") -> None:
        findings.append({"check": kind, "severity": severity, "task_id": row["id"], "agent": row["agentId"], "why": why})

    for row in rows:
        request = admin_store.parse_json(row.get("authorizationRequest"))
        request = request if isinstance(request, dict) else {}
        action, by = str(row.get("humanAction")), row.get("humanActionBy")
        by_agent[str(row["agentId"])] += 1
        decisions[action] += 1
        if by == COO_ACTOR:
            by_coo[action] += 1
        execution = request.get("execution")
        if isinstance(execution, dict) and execution.get("status") not in (None, "running") and action != "Approved":
            flag("executed_without_approval", row, f"a pull request was recorded but the decision is {action}", "high")
        if action == "Approved" and not by:
            flag("approved_without_actor", row, "approved with nobody recorded as the decider", "high")
        if action == "Approved" and by == COO_ACTOR and request.get("kind") != "recommendation":
            critical = coo_escalation.critical_reasons(request)
            if critical:
                flag("coo_approved_a_critical", row, "the COO approved it, and today's code floor calls it critical: " + "; ".join(critical[:2]))
        if action == "Approved" and isinstance(request.get("execution_plan"), dict) and not request.get("execution"):
            flag("approved_but_not_acted_on", row, "approved with a plan, but no pull request is recorded", "info")
        if action == "Pending" and row.get("timestamp") and (now - row["timestamp"]) > timedelta(days=WAITING_DAYS):
            flag("waiting_too_long", row, f"pending for more than {WAITING_DAYS} days")
        if "plan_submitted" not in seen.get(str(row["id"]), set()):
            flag("missing_events", row, "no plan_submitted event is recorded for it", "info")
    order = {"high": 0, "review": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), -int(f["task_id"])))
    return {"tasks": len(rows), "by_agent": dict(by_agent), "decisions": dict(decisions), "decided_by_coo": dict(by_coo), "findings": findings,
            "finding_counts": dict(Counter(f["check"] for f in findings)), "truncated": len(rows) >= MAX_TASKS}


def render(report: dict[str, Any], period: str, days: int) -> str:
    """The report as plain text for the COO. Every number comes from the counts above."""
    lines = [f"AUDIT REPORT: last {days} day(s) ({period}). {report['tasks']} executive task(s) carried an authorization request."]
    if report["tasks"]:
        lines.append("By executive: " + ", ".join(f"{a.upper()} {n}" for a, n in sorted(report["by_agent"].items())) + ".")
        coo = report["decided_by_coo"]
        lines.append("Decisions: " + ", ".join(f"{d} {n}" for d, n in sorted(report["decisions"].items())) + ". "
                     + (f"The COO decided: {', '.join(f'{d} {n}' for d, n in sorted(coo.items()))}." if coo else "The COO decided none."))
    actionable = [f for f in report["findings"] if f["severity"] != "info"]
    notes = [f for f in report["findings"] if f["severity"] == "info"]
    if actionable:
        lines.append(f"FINDINGS TO LOOK AT ({len(actionable)}):")
        lines += [f"- [{f['severity']}] task #{f['task_id']} ({f['agent']}): {f['check']}: {f['why']}" for f in actionable[:MAX_FINDINGS_SHOWN]]
        if len(actionable) > MAX_FINDINGS_SHOWN:
            lines.append(f"- ... and {len(actionable) - MAX_FINDINGS_SHOWN} more.")
    else:
        lines.append("No findings to look at in the recorded data.")
    if notes:
        lines.append(f"Notes ({len(notes)}): " + ", ".join(f"{k} {n}" for k, n in sorted(Counter(f['check'] for f in notes).items())) + ".")
    if report.get("truncated"):
        lines.append(f"The report was cut at {MAX_TASKS} tasks; ask for a shorter period to see the rest.")
    lines.append("WHAT THIS CANNOT SEE: " + CANNOT_SEE)
    return "\n".join(lines)


async def generate(period: Any) -> tuple[str, bool]:
    """(report text, ok). ok is False, with a plain reason, for a bad period or an unreadable database. NEVER RAISES."""
    days = period_days(period)
    if days is None:
        return f"ERROR: period must be one of {', '.join(PERIODS)}.", False
    if not db.is_configured():
        return "ERROR: the audit log is not available, so no report could be made.", False
    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        rows = await db.run(_tasks_sync, since)
        events = await db.run(_events_sync, since)
    except Exception as exc:
        logger.error("audit_report: could not read (%s)", type(exc).__name__)
        return f"ERROR: the audit log could not be read ({type(exc).__name__}), so no report was made. Say so; do not guess.", False
    return render(analyse(rows, events), str(period).strip().lower(), days), True
