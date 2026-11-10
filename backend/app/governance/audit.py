"""
AgentAuditLog and AgentState writes (Clause 6, ACCOUNTABILITY).

Every executive-agent response is recorded: who asked, the question, the answer,
an automatic constitutional score, the sources, and what the human decided (it
starts as 'Pending'; a person changes it later). Audit rows are NEVER deleted by
the kill switch: accountability must survive a reset.

Failure policy: recording must not take an answer away from the user, so
``record_response`` logs and swallows database errors and returns None. The
caller is told whether the entry was saved, and the response includes that fact.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.governance import db
from app.governance.scoring import REVIEW_THRESHOLD, Score, score_response

logger = logging.getLogger(__name__)

HUMAN_ACTIONS = ("Approved", "Modified", "Rejected", "Pending")
MAX_BUFFER = 20  # exchanges kept in AgentState.conversationHistory
MAX_FIELD_CHARS = 20000  # a stored query/response is cut at this length


def _clip(text: str) -> str:
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + " [truncated]"


def _record_sync(
    agent_id: str,
    user_id: str,
    query: str,
    response: str,
    sources: list[str],
    score: Score,
    now: datetime,
    reasoning_chain: list[str],
    scenarios: dict[str, Any] | None,
    pre_consultation_requests: dict[str, Any] | None,
) -> int:
    """One transaction: insert the audit row, update the agent's rolling buffer and violation count."""
    violation = 1 if score.needs_review else 0
    entry = {"at": now.isoformat(), "userId": user_id, "query": _clip(query)[:2000], "reply": _clip(response)[:4000]}
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO "AgentAuditLog" ("agentId","userId","query","response","constitutionalScore","sourcesCited",'
            '"reasoningChain","scenarios","preConsultationRequests","humanAction","timestamp") '
            "VALUES (%s,%s,%s,%s,%s,%s,CAST(%s AS text[]),CAST(%s AS jsonb),CAST(%s AS jsonb),CAST(%s AS \"HumanAction\"),%s) RETURNING id",
            (
                agent_id,
                user_id,
                _clip(query),
                _clip(response),
                score.score,
                sources,
                # Written as a Postgres array literal: a Python [] has no element type for the driver to infer.
                db.text_array_param(reasoning_chain),
                # SQL NULL (not the JSON value null) when there are no scenarios.
                None if scenarios is None else db.json_param(scenarios),
                None if pre_consultation_requests is None else db.json_param(pre_consultation_requests),
                "Pending",
                db.naive_utc(now),
            ),
        )
        audit_id = cur.fetchone()[0]
        # Create the agent's state row on first use, then append to its bounded buffer.
        cur.execute(
            'INSERT INTO "AgentState" ("agentId","conversationHistory","resetEpoch","constitutionalViolations","updatedAt") '
            "VALUES (%s,CAST('[]' AS jsonb),0,0,%s) ON CONFLICT (\"agentId\") DO NOTHING",
            (agent_id, db.naive_utc(now)),
        )
        cur.execute(
            'UPDATE "AgentState" SET "conversationHistory" = '
            "(SELECT COALESCE(jsonb_agg(e ORDER BY n), CAST('[]' AS jsonb)) FROM ("
            '  SELECT e, n FROM jsonb_array_elements("conversationHistory" || CAST(%s AS jsonb)) WITH ORDINALITY AS t(e, n)'
            "  ORDER BY n DESC LIMIT %s) last_n), "
            '"constitutionalViolations" = "constitutionalViolations" + %s, "updatedAt" = %s WHERE "agentId" = %s',
            (db.json_param([entry]), MAX_BUFFER, violation, db.naive_utc(now), agent_id),
        )
        return int(audit_id)


async def record_response(
    agent_id: str,
    user_id: str,
    query: str,
    response: str,
    sources: list[str],
    now: datetime | None = None,
    score: Score | None = None,
    reasoning_chain: list[str] | None = None,
    scenarios: dict[str, Any] | None = None,
    pre_consultation_requests: dict[str, Any] | None = None,
) -> tuple[int | None, Score]:
    """
    Score and record one response. Returns (audit_id or None, score).
    ``audit_id`` is None when the database was unavailable (logged, never raised).
    Pass ``score`` when the caller scored the model's raw text before adding its own notes.

    ``reasoning_chain``, ``scenarios`` and ``pre_consultation_requests`` are the CAO's
    Phase 1 structured output. All are optional so other agents can log without them:
    an absent chain is stored as an empty array and absent JSON as SQL NULL.
    """
    score = score or score_response(response)
    when = now or datetime.now(timezone.utc)
    if not db.is_configured():
        logger.warning("audit: governance database not configured; response for %s not recorded", agent_id)
        return None, score
    try:
        audit_id = await db.run(
            _record_sync,
            agent_id,
            user_id or "unknown",
            query,
            response,
            sources,
            score,
            when,
            list(reasoning_chain or []),
            scenarios,
            pre_consultation_requests,
        )
        if score.needs_review:
            logger.warning("audit: %s response scored %s (< %s): %s", agent_id, score.score, REVIEW_THRESHOLD, "; ".join(score.findings))
        return audit_id, score
    except Exception as exc:
        logger.error("audit: could not record %s response (%s)", agent_id, type(exc).__name__)
        return None, score


def _set_human_action_sync(audit_id: int, action: str) -> bool:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE "AgentAuditLog" SET "humanAction" = CAST(%s AS "HumanAction") WHERE id = %s', (action, audit_id))
        return cur.rowcount == 1


async def set_human_action(audit_id: int, action: str) -> bool:
    """Record what the human decided. Returns False if the entry does not exist."""
    if action not in HUMAN_ACTIONS:
        raise ValueError(f"humanAction must be one of {HUMAN_ACTIONS}")
    return await db.run(_set_human_action_sync, audit_id, action)


def _epoch_sync(agent_id: str) -> int:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT "resetEpoch" FROM "AgentState" WHERE "agentId" = %s', (agent_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


async def current_epoch(agent_id: str) -> int | None:
    """The agent's reset epoch (0 if never used), or None if the database is unavailable."""
    if not db.is_configured():
        return None
    try:
        return await db.run(_epoch_sync, agent_id)
    except Exception as exc:
        logger.error("audit: could not read reset epoch for %s (%s)", agent_id, type(exc).__name__)
        return None


def snapshot_sync(agent_id: str) -> dict[str, Any] | None:
    """The agent's AgentState row as a dict (used by the kill-switch endpoint and tests)."""
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT "agentId","conversationHistory","resetEpoch","lastReset","constitutionalViolations" FROM "AgentState" WHERE "agentId" = %s',
            (agent_id,),
        )
        rows = db.fetch_all(cur)
        return rows[0] if rows else None
