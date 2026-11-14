"""
Shows the learning loop working, step by step, so a person can read it (no model, no network: only the LOCAL database).

    cd frontend; npx prisma dev start caip-local          (once; the ApprovedStrategies migration must be applied)
    cd backend;  python scripts/try_learning_loop.py

WHAT IT DOES
    1. asks the CAO a question and prints how much "memory" its prompt holds (none)
    2. creates a pending CAO authorization request, then the CEO's APPROVE click goes through the REAL endpoint
    3. prints the row now in ApprovedStrategies
    4. asks the CAO a related question again and PRINTS the memory section of the prompt it is sent
    5. shows that the COO is not given it, and that an unrelated question gets no memory
    6. deletes everything it created

Only the model is replaced by a stand-in that records the prompt it receives. It refuses to run against anything but a local database.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from dotenv import dotenv_values  # noqa: E402

TAG = "demo-learning-loop"
KEY = "demo-council-key"
SUMMARY = TAG + " Cut feeding by 30% for 48 hours when ammonia passes 0.5 mg/L in the nursery"
QUESTION = "Ammonia is climbing again in the nursery tanks. What should we do?"
REQUEST = {"action_summary": SUMMARY, "risk_level": "low", "impact": "Stops the ammonia climb without losing stock.",
           "rollback_plan": "Resume the normal feeding rate.", "status": "pending_human_approval", "requires_human_approval": True,
           "executed": False}


def local_url() -> str:
    from app.governance import db

    url = os.getenv("GOVERNANCE_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get("GOVERNANCE_DATABASE_URL") or ""
    host = db.parse_url(url)["host"] if url else ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        sys.exit("Refusing to run: this demo only runs against a LOCAL database (start it with: cd frontend; npx prisma dev start caip-local).")
    return url


class RecordingModel:
    def __init__(self):
        self.last_system = ""
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.last_system = kwargs.get("system", "")
        body = json.dumps({"response": "ok. Recommendation for human review: your decision.", "reasoning_chain": ["Step 1: x"], "scenarios": None,
                           "pre_consultation_requests": {"coo_query": None, "cto_query": None}})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], stop_reason="end_turn")


def memory_section(system: str) -> str:
    if "<<<APPROVED_STRATEGIES" not in system:
        return "    (none: the prompt holds no memory)"
    block = system[system.index("PAST APPROVED STRATEGIES"):]
    return "\n".join("    | " + line for line in block.splitlines())


def main() -> None:
    url = local_url()
    os.environ.update({"GOVERNANCE_DATABASE_URL": url, "INTERNAL_COUNCIL_KEY": KEY})
    from fastapi.testclient import TestClient

    from app.agents import cao_agent, coo_agent
    from app.governance import db
    from app.main import app

    def sql(statement, params=()):
        with db.transaction() as conn:
            cur = conn.cursor()
            cur.execute(statement, params)
            return db.fetch_all(cur) if cur.description else []

    def cleanup():
        sql('DELETE FROM "ApprovedStrategies" WHERE "approvedBy" = %s OR "strategySummary" LIKE %s', (TAG, TAG + "%"))
        sql('DELETE FROM "AgentAuditLog" WHERE "userId" = %s', (TAG,))

    def propose() -> int:
        """The CAO's answer that proposed a strategy: a Pending audit row carrying an authorization request."""
        return sql(
            'INSERT INTO "AgentAuditLog" ("agentId","userId","query","response","constitutionalScore","sourcesCited","reasoningChain",'
            '"authorizationRequest","humanAction","timestamp") VALUES (%s,%s,%s,%s,%s,CAST(%s AS text[]),CAST(%s AS text[]),CAST(%s AS jsonb),'
            'CAST(%s AS "HumanAction"),%s) RETURNING "id"',
            ("cao", TAG, "Ammonia is climbing in the nursery", "r", 90, "{}", "{}", db.json_param(REQUEST), "Pending",
             db.naive_utc(datetime.now(timezone.utc))),
        )[0]["id"]

    def ask(module, function, question):
        asyncio.run(function(question, None, use_rag=False, audit=False, user_id=TAG))
        return model.last_system

    model = RecordingModel()
    cleanup()
    try:
        with mock.patch.object(cao_agent, "_get_client", lambda: model), mock.patch.object(coo_agent, "_get_client", lambda: model):
            print("STEP 1. Ask the CAO a question. It has not learned anything yet.")
            print(memory_section(ask(cao_agent, cao_agent.get_cao_result, QUESTION)))

            print("\nSTEP 2. The CAO proposed a strategy (a pending authorization request). The CEO clicks APPROVE.")
            audit_id = propose()
            reply = TestClient(app).post(f"/api/authorization/{audit_id}/approve", headers={"X-Internal-Council-Key": KEY, "X-Admin-Id": TAG})
            print(f"    HTTP {reply.status_code}: human_action={reply.json().get('human_action')!r}, strategy_memory={reply.json().get('strategy_memory')!r}")

            print("\nSTEP 3. The row now in the ApprovedStrategies table:")
            for row in sql('SELECT "agentRole","strategySummary","context","approvedBy","createdAt" FROM "ApprovedStrategies" WHERE "auditLogId"=%s', (audit_id,)):
                for column, value in row.items():
                    print(f"    {column:<16}{value}")

            print("\nSTEP 4. Ask the CAO a related question. This is the memory section of the prompt it is now sent:")
            print(memory_section(ask(cao_agent, cao_agent.get_cao_result, QUESTION)))

            print("\nSTEP 5. Who else sees it?")
            coo_sees = SUMMARY in ask(coo_agent, coo_agent.get_coo_result, QUESTION)
            print("    the COO, same question:          ", "sees it (WRONG)" if coo_sees else "does not see it (correct: each role has its own memory)")
            unrelated = SUMMARY in ask(cao_agent, cao_agent.get_cao_result, "Which conference should we exhibit at next spring?")
            print("    the CAO, an unrelated question:  ", "sees it (WRONG)" if unrelated else "does not see it (correct: not relevant)")
    finally:
        cleanup()
        print("\nCleaned up everything this demo created.")


if __name__ == "__main__":
    main()
