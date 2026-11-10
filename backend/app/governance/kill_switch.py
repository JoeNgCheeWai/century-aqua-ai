"""
The kill switch (Clause 15): atomic reset of an executive agent.

What a reset does, in ONE database transaction (all or nothing):
  1. clears the agent's stored conversation buffer (AgentState.conversationHistory),
  2. bumps AgentState.resetEpoch, which cancels any history a caller is holding
     (callers send the epoch they saw; a stale epoch means "ignore my history"),
  3. sets lastReset and zeroes the violation counter,
  4. writes an AdminAuditLog line: who reset it and when.
"Resets to the base constitution" needs no stored change: the constitution is code,
prepended to every call (base_constitution.build_system_prompt), so a reset agent
starts its next answer from exactly the constitution and its own role prompt.

What a reset does NOT do: it never deletes AgentAuditLog (accountability must
survive a reset), and it cannot reach into a caller's browser. Callers must honour
the epoch (the CAO endpoint does).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.agents import registry
from app.governance import db

logger = logging.getLogger(__name__)


class UnknownAgentError(Exception):
    pass


def _reset_sync(agent_id: str, admin_id: str, ip: str | None, now: datetime) -> dict[str, Any]:
    with db.transaction() as conn:
        cur = conn.cursor()
        # Lock the row (creating it if this agent has never been used) so two resets cannot interleave.
        cur.execute(
            'INSERT INTO "AgentState" ("agentId","conversationHistory","resetEpoch","constitutionalViolations","updatedAt") '
            "VALUES (%s,CAST('[]' AS jsonb),0,0,%s) ON CONFLICT (\"agentId\") DO NOTHING",
            (agent_id, db.naive_utc(now)),
        )
        cur.execute(
            'SELECT jsonb_array_length("conversationHistory"), "constitutionalViolations", "resetEpoch" FROM "AgentState" WHERE "agentId" = %s FOR UPDATE',
            (agent_id,),
        )
        cleared, violations_before, epoch_before = cur.fetchone()
        cur.execute(
            'UPDATE "AgentState" SET "conversationHistory" = CAST(\'[]\' AS jsonb), "resetEpoch" = "resetEpoch" + 1, '
            '"lastReset" = %s, "constitutionalViolations" = 0, "updatedAt" = %s WHERE "agentId" = %s RETURNING "resetEpoch"',
            (db.naive_utc(now), db.naive_utc(now), agent_id),
        )
        new_epoch = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO "AdminAuditLog" ("actorId","action","targetType","targetId","details","ipAddress","timestamp") '
            "VALUES (%s,'agent_reset','Agent',%s,CAST(%s AS jsonb),%s,%s)",
            (
                admin_id,
                agent_id,
                db.json_param(
                    {
                        "clearedEntries": cleared,
                        "violationsBefore": violations_before,
                        "epochBefore": epoch_before,
                        "epochAfter": new_epoch,
                    }
                ),
                ip,
                db.naive_utc(now),
            ),
        )
        return {
            "agentId": agent_id,
            "clearedEntries": int(cleared),
            "resetEpoch": int(new_epoch),
            "lastReset": now.isoformat(),
        }


async def reset_agent(agent_id: str, admin_id: str, ip: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """
    Reset one agent. Raises UnknownAgentError for an unregistered agent and
    db.GovernanceDBError if the database is unavailable; in both cases nothing was changed.
    """
    if not registry.is_known(agent_id):
        raise UnknownAgentError(agent_id)
    if not db.is_configured():
        raise db.GovernanceDBError("The governance database is not configured, so the reset cannot be recorded.")
    when = now or datetime.now(timezone.utc)
    try:
        result = await db.run(_reset_sync, agent_id, admin_id, ip, when)
    except db.GovernanceDBError:
        raise
    except Exception as exc:
        logger.error("kill switch: reset of %s failed and was rolled back (%s)", agent_id, type(exc).__name__)
        raise db.GovernanceDBError(f"Reset failed and was rolled back ({type(exc).__name__}). Nothing was changed.") from exc
    logger.warning("kill switch: agent %s reset by %s (%s entries cleared)", agent_id, admin_id, result["clearedEntries"])
    return result
