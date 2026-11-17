"""
TASK EVENTS: the automatic audit trail of an executive task's life, so nobody has to log anything by hand.

Every step of a task is recorded by CODE at the place it happens (never by a model, never by a person remembering to):
    plan_submitted   an executive's answer carried an authorization request      (governance/audit.record_response)
    coo_decision     the COO approved, rejected or escalated it                    (agents/coo_tools)
    ceo_notified     the COO told the CEO about a critical decision               (agents/coo_tools)
    ceo_decision     the CEO approved or rejected it                              (api/authorization.apply_decision)
    execution        an approval opened a pull request (or failed to)             (api/authorization.apply_decision)

TWO ROWS PER EVENT, in the existing AdminAuditLog table (no migration): one under the EXECUTIVE'S own id (actorId ``ai_cto``, action ``task_event``: the
executive's own audit log) and one under the COO's (actorId ``ai_coo``, action ``coo_oversight``: the COO's view of every executive, for audits). Both
carry the same details: the event, the audit entry number, the agent, and plain facts (a status, a risk level, a pull request address, the COO's reasoning).

SAFE TO CALL ANYWHERE. record() never raises and never waits long: a short timeout, and a circuit breaker so a slow database costs one answer at most one
failed write. A skipped write is LOST (it is not retried), and the report says what the log could not show. Text is capped and has secrets masked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.execution_plan import redact_secrets
from app.governance import admin_store, db

logger = logging.getLogger(__name__)

EVENTS = ("plan_submitted", "coo_decision", "ceo_notified", "ceo_decision", "execution")
OWN_ACTION, OVERSIGHT_ACTION, OVERSIGHT_ACTOR = "task_event", "coo_oversight", "ai_coo"
LOG_TIMEOUT_SECONDS = 2.0
LOG_COOLDOWN_SECONDS = 60.0
MAX_TEXT = 600
MAX_LIMIT = 500
_skip_until = 0.0  # time.monotonic() before which the write is skipped; 0 means the breaker is closed


def reset_breaker() -> None:
    global _skip_until
    _skip_until = 0.0


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(" ".join(value.split()))[:MAX_TEXT]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_secrets(str(value))[:MAX_TEXT]


def payload(audit_id: int, agent_id: str, event: str, details: dict[str, Any]) -> dict[str, Any]:
    """The JSON stored on both rows. Pure, so it is tested without a database."""
    return {"event": event, "audit_id": audit_id, "agent": agent_id, **{k: _clean(v) for k, v in details.items()}}


def _record_sync(audit_id: int, agent_id: str, body: dict[str, Any]) -> None:
    with db.transaction() as conn:
        cur = conn.cursor()
        for actor, action in ((f"ai_{agent_id}", OWN_ACTION), (OVERSIGHT_ACTOR, OVERSIGHT_ACTION)):
            cur.execute('INSERT INTO "AdminAuditLog" ("actorId","action","targetType","targetId","details") VALUES (%s,%s,%s,%s,CAST(%s AS jsonb))',
                        (actor, action, "AgentAuditLog", str(audit_id)[:60], db.json_param(body)))


async def record(audit_id: int | None, agent_id: str | None, event: str, **details: Any) -> bool:
    """Write one event (two rows). True if written. NEVER RAISES, never waits more than LOG_TIMEOUT_SECONDS, and is a no-op without a database."""
    global _skip_until
    if audit_id is None or not agent_id or event not in EVENTS or not db.valid_agent_id(str(agent_id)):
        return False
    body = payload(int(audit_id), str(agent_id), event, details)
    print(f"TASK EVENT: {event} task={audit_id} agent={agent_id}", flush=True)
    if not db.is_configured() or time.monotonic() < _skip_until:
        return False
    try:
        await asyncio.wait_for(db.run(_record_sync, int(audit_id), str(agent_id), body), timeout=LOG_TIMEOUT_SECONDS)
        return True
    except Exception as exc:
        _skip_until = time.monotonic() + LOG_COOLDOWN_SECONDS
        logger.error("task_events: could not log %s for task %s (%s); skipping the event log for %.0f s", event, audit_id, type(exc).__name__, LOG_COOLDOWN_SECONDS)
        return False


def _list_sync(since: datetime, agent_id: str | None, event: str | None, limit: int) -> list[dict[str, Any]]:
    sql = 'SELECT "id","actorId","targetId","details","timestamp" FROM "AdminAuditLog" WHERE "action"=%s AND "timestamp">=%s'
    params: list[Any] = [OWN_ACTION, db.naive_utc(since)]
    if agent_id:
        sql += ' AND "actorId"=%s'
        params.append(f"ai_{agent_id}")
    if event:
        sql += " AND \"details\"->>'event'=%s"
        params.append(event)
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(sql + ' ORDER BY "timestamp" DESC,"id" DESC LIMIT %s', (*params, limit))
        return db.fetch_all(cur)


async def list_events(days: int, agent_id: str | None = None, event: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """The executives' own events in the last ``days`` days, newest first, each as {id, agent, event, audit_id, at, details}. RAISES on a database error."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    rows = await db.run(_list_sync, since, agent_id, event, max(1, min(MAX_LIMIT, limit)))
    out = []
    for row in rows:
        body = admin_store.parse_json(row.get("details"))
        body = body if isinstance(body, dict) else {}
        out.append({"id": row["id"], "agent": str(row["actorId"]).removeprefix("ai_"), "event": body.get("event"), "audit_id": body.get("audit_id"),
                    "at": admin_store.iso_utc(row.get("timestamp")), "details": body})
    return out
