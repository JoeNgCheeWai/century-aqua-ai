"""
The Boardroom's new turn detail (reasoning, and the answers to the other executives) against REAL Postgres: the LOCAL `prisma dev`
server only (skipped otherwise, never Neon).

    cd frontend; npx prisma dev start caip-local          (the add_council_turn_reasoning migration must be applied)
    cd backend;  python -m unittest tests.test_boardroom_storage_sql

It proves what the in-memory stand-in cannot: a full session's reasoning and cross-responses survive a real write and read back
through the real API payload, and a database WITHOUT the new migration still stores the session (a deploy can reach production
before its migration does, and a session must never be left RUNNING because of it).
"""

import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import council  # noqa: E402
from app.api import council as council_api  # noqa: E402
from app.governance import council_store  # noqa: E402
from tests.test_council_protocol import ALL, FakeModel, QUERY  # noqa: E402
from tests.test_mission_control_api import DB_READY, LOCAL_URL, TAG, _sql  # noqa: E402

NEEDS_DB = "needs the LOCAL prisma dev database (cd frontend; npx prisma dev start caip-local). Never runs against Neon."


def _migration_applied():
    try:
        _sql('SELECT "reasoning","responses" FROM "CouncilTurnLog" LIMIT 1')
        return True
    except Exception:
        return False


@unittest.skipUnless(DB_READY, NEEDS_DB)
class TurnDetailSqlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL})
        patcher.start()
        self.addCleanup(patcher.stop)
        if not _migration_applied():
            self.skipTest("the add_council_turn_reasoning migration is not applied to the local database")
        council_store._columns_ready = False  # forget any answer cached by an earlier test
        self.addCleanup(setattr, council_store, "_columns_ready", False)
        self.query = f"{TAG} {QUERY}"
        self._wipe()
        self.addCleanup(self._wipe)

    def _wipe(self):
        _sql('DELETE FROM "CouncilSessionLog" WHERE "query" LIKE %s', (TAG + "%",))  # turns go with it (ON DELETE CASCADE)

    async def _run_and_store(self):
        session_id = str(uuid.uuid4())
        await council_store.create_session(session_id, self.query, ALL, "all four sit")
        with mock.patch.object(council, "_client", FakeModel()):
            result = await council.run_council_session(self.query, ALL, session_id=session_id)
        await council_store.finish_session(session_id, result)
        return session_id, result

    async def test_a_full_session_stores_reasoning_and_cross_responses_and_the_api_returns_them(self):
        session_id, result = await self._run_and_store()
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        row, turns = await council_store.get_session(session_id)
        body = council_api._payload(row, turns)
        self.assertEqual(body["status"], "RESOLVED")
        by_round = {}
        for t in body["turns"]:
            by_round.setdefault(t["round"], []).append(t)
        self.assertEqual(sorted(by_round), [1, 2, 5, 6, 7])  # 7 sorts last in SQL; the UI shows it right after round 1
        for t in by_round[1] + by_round[council.CROSS_ROUND]:
            self.assertTrue(t["reasoning"], t["agent_id"])  # the private working was stored
        for t in by_round[council.CROSS_ROUND]:
            self.assertEqual({r["to"] for r in t["responses"]}, set(ALL) - {t["agent_id"]})
            self.assertTrue(all(r["adds_or_alternative"] for r in t["responses"]))
        for t in by_round[1]:
            self.assertIsNone(t["responses"])  # only the cross-response round carries answers
        for t in by_round[2] + by_round[5] + by_round[6]:
            self.assertIsNone(t["reasoning"])  # chair turns have none


    async def test_a_database_without_the_new_migration_still_stores_the_session(self):
        # Simulate a deploy that reached production before its migration: the column check says the columns are missing.
        with mock.patch.object(council_store, "_has_turn_detail_columns", lambda cur: False):
            session_id, _ = await self._run_and_store()
        row, turns = await council_store.get_session(session_id)
        self.assertEqual(row["status"], "RESOLVED")  # never left RUNNING
        self.assertTrue(turns)
        self.assertTrue(all(t["reasoning"] is None and t["responses"] is None for t in turns))  # the detail is simply not stored

    async def test_the_column_check_sees_the_real_columns_and_caches_only_a_yes(self):
        def check():
            with council_store.db.transaction() as conn:
                return council_store._has_turn_detail_columns(conn.cursor())

        self.assertTrue(await council_store.db.run(check))
        self.assertTrue(council_store._columns_ready)

    async def test_an_older_stored_session_without_detail_still_reads_back(self):
        session_id = str(uuid.uuid4())
        await council_store.create_session(session_id, self.query, ["cao", "coo"], "r")
        _sql('INSERT INTO "CouncilTurnLog" ("id","sessionId","round","agentId","position","tokensUsed") VALUES (%s,%s,1,%s,%s,10)',
             (str(uuid.uuid4()), session_id, "cao", "An old turn"))
        await council_store.fail_session(session_id, "done")
        row, turns = await council_store.get_session(session_id)
        body = council_api._payload(row, turns)
        self.assertEqual(body["turns"][0]["position"], "An old turn")
        self.assertIsNone(body["turns"][0]["reasoning"])
        self.assertIsNone(body["turns"][0]["responses"])


if __name__ == "__main__":
    unittest.main()
