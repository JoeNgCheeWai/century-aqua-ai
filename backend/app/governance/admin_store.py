"""
Mission Control queries (Phase 6): the audit-log browser, the authorization decision and the council session list.

Raw SQL through pg8000 like audit.py and council_store.py. Column names are camelCase quoted identifiers (Prisma).

READS USE SELECT *, so the audit log keeps working on a database that has not yet had the add_human_action_audit
migration (it adds AgentAuditLog.humanActionAt and humanActionBy). Only the DECISION writes those two columns, so
approve and reject answer 503 until the migration is applied, and nothing else is affected.

A DECISION IS A RECORD, NOT AN EXECUTION. decide_authorization only sets humanAction, humanActionAt and humanActionBy on
a row that carries an authorization request and is still Pending. Nothing in THIS module runs anything. (Phase 6.6: the
approve endpoint may afterwards hand an approved request that carries an execution_plan to the tool router, app/agents/
tools.py. This module only stores the outcome: claim_execution takes a once-only claim, record_execution stores the result
inside the same authorizationRequest JSON, so it shows in Mission Control.) AgentAuditLog.timestamp (when the agent
answered) is never touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.governance import council_store, db

# A real request object, not SQL NULL and not the JSON value null (the writers store SQL NULL when there is none).
_HAS_REQUEST = '"authorizationRequest" IS NOT NULL AND jsonb_typeof("authorizationRequest") = \'object\''
DECISIONS = {"approve": "Approved", "reject": "Rejected"}
COUNCIL_STATUSES = ("RUNNING", "RESOLVED", "ESCALATED_TO_CEO", "NEEDS_CEO_INPUT", "FAILED")


def parse_json(value: Any) -> Any:
    """A jsonb column comes back parsed from some drivers and as text from others."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def iso_utc(value: Any) -> str | None:
    """Prisma DateTime columns are naive UTC. Say so with a trailing Z so a browser does not read them as local time."""
    if not isinstance(value, datetime):
        return None
    naive = value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    return naive.isoformat() + "Z"


def _like(text: str) -> str:
    """A substring pattern with the LIKE wildcards in the user's text made literal."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ---------------------------------------------------------------- audit log

def _audit_where(
    agent_id: str | None, start: datetime | None, end: datetime | None, human_action: str | None,
    has_authorization: bool | None, search: str | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if agent_id:
        clauses.append('"agentId" = %s')
        params.append(agent_id)
    if start is not None:
        clauses.append('"timestamp" >= %s')
        params.append(start)
    if end is not None:
        clauses.append('"timestamp" < %s')  # end is exclusive: the API passes the start of the next day
        params.append(end)
    if human_action:
        clauses.append('"humanAction" = CAST(%s AS "HumanAction")')
        params.append(human_action)
    if has_authorization is True:
        clauses.append(f"({_HAS_REQUEST})")
    elif has_authorization is False:
        clauses.append(f"NOT ({_HAS_REQUEST})")
    if search:
        clauses.append('("query" ILIKE %s OR "response" ILIKE %s)')
        params += [_like(search), _like(search)]
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _list_audit_sync(
    agent_id: str | None, start: datetime | None, end: datetime | None, human_action: str | None,
    has_authorization: bool | None, search: str | None, limit: int, offset: int,
) -> tuple[list[dict[str, Any]], int]:
    where, params = _audit_where(agent_id, start, end, human_action, has_authorization, search)
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "AgentAuditLog"{where}', params)
        total = int(cur.fetchone()[0])
        cur.execute(
            f'SELECT * FROM "AgentAuditLog"{where} ORDER BY "timestamp" DESC, "id" DESC LIMIT %s OFFSET %s',
            [*params, limit, offset],
        )
        return db.fetch_all(cur), total


async def list_audit_logs(
    *, agent_id: str | None, start: datetime | None, end: datetime | None, human_action: str | None,
    has_authorization: bool | None, search: str | None, limit: int, offset: int,
) -> tuple[list[dict[str, Any]], int]:
    return await db.run(_list_audit_sync, agent_id, start, end, human_action, has_authorization, search, limit, offset)


# ---------------------------------------------------------------- authorization decisions

def _decide_sync(audit_id: int, human_action: str, actor: str) -> tuple[str, dict[str, Any] | None]:
    """
    One atomic UPDATE guarded by the rule itself (a request exists AND the row is still Pending), so two people
    clicking at once cannot both win. Returns ("ok", row) | ("not_found", None) | ("no_request", None) |
    ("already_decided", row).
    """
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE "AgentAuditLog" SET "humanAction"=CAST(%s AS "HumanAction"),"humanActionAt"=%s,"humanActionBy"=%s '
            f'WHERE "id"=%s AND "humanAction"=CAST(%s AS "HumanAction") AND {_HAS_REQUEST} '
            'RETURNING "id","agentId","humanAction","humanActionAt","humanActionBy"',
            (human_action, db.naive_utc(datetime.now(timezone.utc)), actor, audit_id, "Pending"),
        )
        rows = db.fetch_all(cur)
        if rows:
            return "ok", rows[0]
        cur.execute(
            f'SELECT "id","agentId","humanAction",({_HAS_REQUEST}) AS "hasRequest" FROM "AgentAuditLog" WHERE "id"=%s',
            (audit_id,),
        )
        found = db.fetch_all(cur)
        if not found:
            return "not_found", None
        if not found[0]["hasRequest"]:
            return "no_request", None
        return "already_decided", found[0]


async def decide_authorization(audit_id: int, action: str, actor: str) -> tuple[str, dict[str, Any] | None]:
    if action not in DECISIONS:
        raise ValueError(f"action must be one of {tuple(DECISIONS)}")
    return await db.run(_decide_sync, audit_id, DECISIONS[action], actor)


# ---------------------------------------------------------------- execution of an approved plan (Phase 6.6)

def _read_request_sync(audit_id: int) -> dict[str, Any] | None:
    """The stored authorization request of one audit row, parsed, or None."""
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT "authorizationRequest" FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))
        rows = db.fetch_all(cur)
    value = parse_json(rows[0]["authorizationRequest"]) if rows else None
    return value if isinstance(value, dict) else None


async def read_authorization_request(audit_id: int) -> dict[str, Any] | None:
    return await db.run(_read_request_sync, audit_id)


def _claim_execution_sync(audit_id: int) -> bool:
    """
    Claim the right to execute, ONCE. One atomic UPDATE that only succeeds while the row is Approved and its request has an
    execution_plan that has not been claimed yet, and that records the claim (``execution``: {"status": "running"}) in the
    same statement. Two simultaneous callers, or a retry after a crash, cannot both claim it, so one approval can open at
    most one pull request.
    """
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE "AgentAuditLog" SET "authorizationRequest" = jsonb_set("authorizationRequest", \'{execution}\', CAST(%s AS jsonb)) '
            'WHERE "id"=%s AND "humanAction"=CAST(%s AS "HumanAction") AND ' + _HAS_REQUEST + ' '
            "AND jsonb_typeof(\"authorizationRequest\"->'execution_plan') = 'object' AND NOT jsonb_exists(\"authorizationRequest\", 'execution') "
            'RETURNING "id"',
            (db.json_param({"status": "running"}), audit_id, "Approved"),
        )
        return bool(db.fetch_all(cur))


async def claim_execution(audit_id: int) -> bool:
    return await db.run(_claim_execution_sync, audit_id)


def _record_execution_sync(audit_id: int, execution: dict[str, Any]) -> bool:
    """Replace the claim with the final result (status, steps, pr_url, error) inside the same authorization request."""
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE "AgentAuditLog" SET "authorizationRequest" = jsonb_set("authorizationRequest", \'{execution}\', CAST(%s AS jsonb)) '
            'WHERE "id"=%s AND ' + _HAS_REQUEST + ' RETURNING "id"',
            (db.json_param(execution), audit_id),
        )
        return bool(db.fetch_all(cur))


async def record_execution(audit_id: int, execution: dict[str, Any]) -> bool:
    return await db.run(_record_execution_sync, audit_id, execution)


# ---------------------------------------------------------------- council sessions

def _list_sessions_sync(status: str | None, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    where, params = "", []
    if status:
        where, params = ' WHERE "status" = CAST(%s AS "CouncilStatus")', [status]
    now = db.naive_utc(datetime.now(timezone.utc))
    cutoff = now - timedelta(seconds=council_store.ORPHAN_AFTER_SECONDS)
    with db.transaction() as conn:
        cur = conn.cursor()
        # The same orphan rule the single-session GET applies lazily: a RUNNING row whose process is gone is FAILED.
        cur.execute(
            'UPDATE "CouncilSessionLog" SET "status"=CAST(%s AS "CouncilStatus"),"escalationReason"=%s,"completedAt"=%s '
            'WHERE "status"=CAST(%s AS "CouncilStatus") AND "startedAt" < %s',
            ("FAILED", council_store.ORPHAN_REASON, now, "RUNNING", cutoff),
        )
        cur.execute(f'SELECT COUNT(*) FROM "CouncilSessionLog"{where}', params)
        total = int(cur.fetchone()[0])
        cur.execute(
            'SELECT "id","query","status","participatingAgents","startedAt","completedAt","modelCalls","totalTokens",'
            '"humanAction","escalationReason",'
            "CASE WHEN jsonb_typeof(\"caoSynthesizedOptions\") = 'array' "
            "THEN jsonb_array_length(\"caoSynthesizedOptions\") ELSE 0 END AS \"optionCount\" "
            f'FROM "CouncilSessionLog"{where} ORDER BY "startedAt" DESC, "id" DESC LIMIT %s OFFSET %s',
            [*params, limit, offset],
        )
        return db.fetch_all(cur), total


async def list_council_sessions(*, status: str | None, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    return await db.run(_list_sessions_sync, status, limit, offset)
