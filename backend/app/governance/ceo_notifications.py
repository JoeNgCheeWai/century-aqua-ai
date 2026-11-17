"""
The CEO's NOTIFICATIONS: the one way a CRITICAL decision reaches the CEO now that the COO is the sole approver of everything else.

WHAT IT IS. One row in ``CeoNotification`` per task the COO escalated: the task's audit id, a plain-language WHY it is critical, the COO's
recommendation, its risk, and a status (unread -> read -> resolved). The CEO reads them in Mission Control (a bell and a "Needs your decision" list).
It is created by code from the COO's notify_ceo_of_critical_decision tool, never by a model writing SQL.

WHAT IT IS NOT. There is no email, text or push here: the repository has no such service. A notification is IN-APP, so it reaches the CEO when
Mission Control is open (the bell polls). If the CEO must be told while the app is closed, a delivery channel has to be added; this table is the
record such a channel would read.

RULES, IN CODE. One OPEN notification per task (a second escalation of the same task refreshes it instead of stacking a duplicate). Text is capped
and has secrets masked. The writes NEVER RAISE: a database problem returns a plain failure, so the caller always knows whether the CEO was
actually notified and an escalation is never lost silently. The list RAISES on a database error (the API answers 503): an empty list would wrongly
tell the CEO that nothing needs them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.agents.execution_plan import redact_secrets
from app.governance import admin_store, db

logger = logging.getLogger(__name__)

STATUSES = ("unread", "read", "resolved")
OPEN = ("unread", "read")
MAX_TITLE, MAX_EXPLANATION, MAX_RECOMMENDATION = 200, 2000, 1000
MAX_LIMIT, DEFAULT_LIMIT = 100, 20
COLUMNS = '"id","auditId","agentId","title","explanation","recommendation","riskLevel","status","createdAt","readAt","resolvedAt","resolvedBy"'


def clean(value: Any, limit: int) -> str:
    """Trimmed, whitespace-collapsed, secret-masked text cut to ``limit``. '' for anything that is not text."""
    return redact_secrets(" ".join(value.split()))[:limit] if isinstance(value, str) else ""


def _now() -> datetime:
    return db.naive_utc(datetime.now(timezone.utc))


def present(row: dict[str, Any]) -> dict[str, Any]:
    """One row as the API returns it (snake_case, times as UTC with a Z)."""
    return {
        "id": row["id"], "audit_id": row.get("auditId"), "agent_id": row.get("agentId"), "title": row["title"], "explanation": row["explanation"],
        "recommendation": row.get("recommendation"), "risk_level": row.get("riskLevel"), "status": row["status"],
        "created_at": admin_store.iso_utc(row.get("createdAt")), "read_at": admin_store.iso_utc(row.get("readAt")),
        "resolved_at": admin_store.iso_utc(row.get("resolvedAt")), "resolved_by": row.get("resolvedBy"),
    }


# ------------------------------------------------------------------------------------------------ writes
def _create_sync(audit_id: int | None, agent_id: str | None, title: str, explanation: str, recommendation: str | None, risk: str | None) -> tuple[int, bool]:
    """(notification id, created). An OPEN notification for the same task is refreshed, not duplicated."""
    with db.transaction() as conn:
        cur = conn.cursor()
        if audit_id is not None:
            cur.execute('SELECT "id" FROM "CeoNotification" WHERE "auditId"=%s AND "status" IN (%s,%s) ORDER BY "id" DESC LIMIT 1', (audit_id, *OPEN))
            found = db.fetch_all(cur)
            if found:
                cur.execute('UPDATE "CeoNotification" SET "title"=%s,"explanation"=%s,"recommendation"=%s,"riskLevel"=%s,"status"=%s,"readAt"=NULL WHERE "id"=%s',
                            (title, explanation, recommendation, risk, "unread", found[0]["id"]))
                return int(found[0]["id"]), False
        cur.execute('INSERT INTO "CeoNotification" ("auditId","agentId","title","explanation","recommendation","riskLevel","status","createdAt") '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING "id"', (audit_id, agent_id, title, explanation, recommendation, risk, "unread", _now()))
        return int(db.fetch_all(cur)[0]["id"]), True


async def create(audit_id: int | None, agent_id: str | None, title: Any, explanation: Any, recommendation: Any = None, risk_level: Any = None) -> tuple[int | None, str]:
    """Create (or refresh) one notification. Returns (id or None, a plain message). NEVER RAISES; None means the CEO was NOT notified."""
    title_text, why, advice = clean(title, MAX_TITLE), clean(explanation, MAX_EXPLANATION), clean(recommendation, MAX_RECOMMENDATION) or None
    if not title_text or not why:
        return None, "a notification needs a title and a plain-language explanation"
    if not db.is_configured():
        return None, "the database is not configured, so the CEO could not be notified"
    risk = risk_level if risk_level in ("low", "medium", "high", "critical") else None
    try:
        new_id, created = await db.run(_create_sync, audit_id, agent_id, title_text, why, advice, risk)
    except Exception as exc:
        logger.error("ceo_notifications: could not create (%s)", type(exc).__name__)
        return None, f"the notification could not be saved ({type(exc).__name__})"
    return new_id, "created" if created else "updated"


def _mark_sync(notification_id: int, status: str, actor: str | None) -> bool:
    with db.transaction() as conn:
        cur = conn.cursor()
        if status == "read":
            cur.execute('UPDATE "CeoNotification" SET "status"=%s,"readAt"=%s WHERE "id"=%s AND "status"=%s RETURNING "id"', ("read", _now(), notification_id, "unread"))
        else:
            cur.execute('UPDATE "CeoNotification" SET "status"=%s,"resolvedAt"=%s,"resolvedBy"=%s WHERE "id"=%s AND "status"<>%s RETURNING "id"',
                        ("resolved", _now(), actor, notification_id, "resolved"))
        return bool(db.fetch_all(cur))


async def mark(notification_id: int, status: str, actor: str | None = None) -> bool:
    """Mark one notification 'read' (only from unread) or 'resolved' (from any open state). True if a row changed. NEVER RAISES."""
    if status not in ("read", "resolved") or not isinstance(notification_id, int) or isinstance(notification_id, bool):
        return False
    try:
        return bool(await db.run(_mark_sync, notification_id, status, actor))
    except Exception as exc:
        logger.error("ceo_notifications: could not mark #%s (%s)", notification_id, type(exc).__name__)
        return False


def _resolve_for_audit_sync(audit_id: int, actor: str | None) -> int:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE "CeoNotification" SET "status"=%s,"resolvedAt"=%s,"resolvedBy"=%s WHERE "auditId"=%s AND "status"<>%s RETURNING "id"',
                    ("resolved", _now(), actor, audit_id, "resolved"))
        return len(db.fetch_all(cur))


async def resolve_for_audit(audit_id: int, actor: str | None) -> int:
    """The task was decided: settle every open notification about it. Returns how many. NEVER RAISES."""
    if not db.is_configured():
        return 0
    try:
        return int(await db.run(_resolve_for_audit_sync, audit_id, actor))
    except Exception as exc:
        logger.error("ceo_notifications: could not settle for audit #%s (%s)", audit_id, type(exc).__name__)
        return 0


# ------------------------------------------------------------------------------------------------ reads
def _list_sync(status: str | None, limit: int, offset: int) -> tuple[list[dict[str, Any]], int, int]:
    if status is None:
        where, params = "", ()
    elif status == "open":
        where, params = ' WHERE "status" IN (%s,%s)', OPEN
    else:
        where, params = ' WHERE "status"=%s', (status,)
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT {COLUMNS} FROM "CeoNotification"{where} ORDER BY "createdAt" DESC,"id" DESC LIMIT %s OFFSET %s', (*params, limit, offset))
        rows = db.fetch_all(cur)
        cur.execute(f'SELECT COUNT(*) AS n FROM "CeoNotification"{where}', params)
        total = int(db.fetch_all(cur)[0]["n"])
        cur.execute('SELECT COUNT(*) AS n FROM "CeoNotification" WHERE "status"=%s', ("unread",))
        unread = int(db.fetch_all(cur)[0]["n"])
    return rows, total, unread


async def list_notifications(status: str | None = "open", limit: int = DEFAULT_LIMIT, offset: int = 0) -> tuple[list[dict[str, Any]], int, int]:
    """(items, total matching, unread count). ``status``: unread, read, resolved, open (unread + read) or None for all. RAISES on a database error."""
    rows, total, unread = await db.run(_list_sync, status, max(1, min(MAX_LIMIT, limit)), max(0, offset))
    return [present(r) for r in rows], total, unread
