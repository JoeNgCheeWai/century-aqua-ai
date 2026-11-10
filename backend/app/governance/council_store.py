"""
CouncilSessionLog and CouncilTurnLog access (Phase 5), raw SQL through pg8000 like audit.py.

The database is the source of truth for a session, not process memory: POST creates a RUNNING row, the background
task finishes it, and GET reads it. Column names are camelCase quoted identifiers (Prisma).

ORPHAN RULE: Render can restart or redeploy while a session is in flight, which would leave a row RUNNING forever.
A RUNNING row older than ORPHAN_AFTER_SECONDS (5 minutes) is therefore reported, and recorded, as FAILED with the
reason 'orphaned by restart'. The session deadline (240 s) is shorter, so a live session is never called orphaned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.governance import db

ORPHAN_AFTER_SECONDS = 300
ORPHAN_REASON = "orphaned by restart"
TERMINAL = ("RESOLVED", "ESCALATED_TO_CEO", "NEEDS_CEO_INPUT", "FAILED")
MAX_QUERY_CHARS = 4000


def _now() -> datetime:
    return db.naive_utc(datetime.now(timezone.utc))


def _create_sync(session_id: str, query: str, agents: list[str], rationale: str) -> None:
    with db.transaction() as conn:
        conn.cursor().execute(
            'INSERT INTO "CouncilSessionLog" ("id","query","participatingAgents","selectionRationale","status",'
            '"modelCalls","startedAt","humanAction") '
            'VALUES (%s,%s,CAST(%s AS text[]),%s,CAST(%s AS "CouncilStatus"),0,%s,CAST(%s AS "HumanAction"))',
            (session_id, query[:MAX_QUERY_CHARS], db.text_array_param(agents), rationale[:1000], "RUNNING", _now(), "Pending"),
        )


async def create_session(session_id: str, query: str, agents: list[str], rationale: str) -> None:
    await db.run(_create_sync, session_id, query, agents, rationale)


def _fail_sync(session_id: str, reason: str, calls: int = 0) -> None:
    with db.transaction() as conn:
        conn.cursor().execute(
            'UPDATE "CouncilSessionLog" SET "status"=CAST(%s AS "CouncilStatus"),"escalationReason"=%s,'
            '"modelCalls"=GREATEST("modelCalls",%s),"completedAt"=%s WHERE "id"=%s AND "status"=CAST(%s AS "CouncilStatus")',
            ("FAILED", reason[:500], calls, _now(), session_id, "RUNNING"),
        )


async def fail_session(session_id: str, reason: str, calls: int = 0) -> None:
    await db.run(_fail_sync, session_id, reason, calls)


def question_record(result: dict[str, Any]) -> dict[str, list[str]] | None:
    """
    What goes in the ceoQuestions JSON column, the SAME shape for every status (no schema change was needed):

        {"critical":      the COO's critical_data_required: what the CEO must answer (NEEDS_CEO_INPUT),
         "open":          flags the COO ruled NON-critical, carried into the options as stated assumptions (RESOLVED),
         "agent_flagged": everything the agents flagged, de-duplicated (the audit record)}

    NULL when all three are empty (nothing was flagged). Older rows hold a bare list, which split_questions() reads
    as the critical questions.
    """
    record = {
        "critical": list(result.get("ceo_questions") or []),
        "open": list(result.get("open_questions") or []),
        "agent_flagged": list(result.get("agent_flagged_questions") or []),
    }
    return record if any(record.values()) else None


def split_questions(value: Any) -> dict[str, list[str]]:
    """The three question lists from a stored ceoQuestions value (an object, a bare list from older rows, or None)."""
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if isinstance(value, list):  # rows written before the record existed: the list was the critical questions
        return {"critical": [str(q) for q in value], "open": [], "agent_flagged": []}
    if isinstance(value, dict):
        return {key: [str(q) for q in value.get(key) or []] for key in ("critical", "open", "agent_flagged")}
    return {"critical": [], "open": [], "agent_flagged": []}


def _finish_sync(session_id: str, result: dict[str, Any]) -> None:
    j = db.json_param
    questions = question_record(result)
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE "CouncilSessionLog" SET "status"=CAST(%s AS "CouncilStatus"),"conflictMatrix"=CAST(%s AS jsonb),'
            '"hardConstraintVetoes"=CAST(%s AS jsonb),"cooModerationNotes"=%s,"caoSynthesizedOptions"=CAST(%s AS jsonb),'
            '"redTeamFindings"=CAST(%s AS jsonb),"dissentRecord"=CAST(%s AS jsonb),"ceoQuestions"=CAST(%s AS jsonb),'
            '"escalationReason"=%s,"modelCalls"=%s,"totalTokens"=%s,"completedAt"=%s '
            'WHERE "id"=%s AND "status"=CAST(%s AS "CouncilStatus")',
            (
                result["status"],
                j(result["conflict_matrix"]),
                j(result["hard_constraint_vetoes"]),
                result["coo_moderation_notes"],
                None if result["options"] is None else j(result["options"]),
                None if result["red_team_findings"] is None else j(result["red_team_findings"]),
                j(result["dissent_record"]),
                None if questions is None else j(questions),
                result["escalation_reason"],
                result["model_calls"],
                result["total_tokens"],
                _now(),
                session_id,
                "RUNNING",  # never overwrite a row the orphan rule already closed
            ),
        )
        for turn in result["turns"]:
            cur.execute(
                'INSERT INTO "CouncilTurnLog" ("id","sessionId","round","agentId","position","hardConstraints",'
                '"confidence","sourcesCited","auditLogId","tokensUsed","timestamp") '
                "VALUES (gen_random_uuid()::text,%s,%s,%s,%s,CAST(%s AS jsonb),%s,CAST(%s AS text[]),%s,%s,%s)",
                (
                    session_id,
                    turn["round"],
                    turn["agent_id"],
                    turn["position"][:2000],
                    None if not turn["hard_constraints"] else j(turn["hard_constraints"]),
                    turn["confidence"],
                    db.text_array_param(turn["sources_cited"]),
                    turn["audit_log_id"],
                    turn["tokens_used"],
                    _now(),
                ),
            )


async def finish_session(session_id: str, result: dict[str, Any]) -> None:
    await db.run(_finish_sync, session_id, result)


def _select_sync(session_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM "CouncilSessionLog" WHERE "id"=%s', (session_id,))
        rows = db.fetch_all(cur)
        if not rows:
            return None, []
        cur.execute('SELECT * FROM "CouncilTurnLog" WHERE "sessionId"=%s ORDER BY "round","timestamp"', (session_id,))
        return rows[0], db.fetch_all(cur)


def is_orphaned(row: dict[str, Any], now: datetime | None = None) -> bool:
    """A RUNNING row whose start is more than ORPHAN_AFTER_SECONDS ago (the process that ran it is gone)."""
    started = row.get("startedAt")
    if row.get("status") != "RUNNING" or not isinstance(started, datetime):
        return False
    return (now or _now()) - started > timedelta(seconds=ORPHAN_AFTER_SECONDS)


async def get_session(session_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(row, turns), or (None, []). Applies the orphan rule: a stale RUNNING row is closed as FAILED first."""
    row, turns = await db.run(_select_sync, session_id)
    if row is not None and is_orphaned(row):
        await fail_session(session_id, ORPHAN_REASON)
        row, turns = await db.run(_select_sync, session_id)
    return row, turns

