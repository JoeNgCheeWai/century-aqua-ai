"""
Database-backed governance tests: audit rows, the kill switch, atomicity, cross-validation log.

SAFETY: these tests WRITE to the database, so they run only against a LOCAL one
(localhost / 127.0.0.1, the `prisma dev` server) and are skipped otherwise. They never touch
Neon. They use a dedicated agent id ('testbot') and delete everything they created.

The URL comes from GOVERNANCE_DATABASE_URL in backend/.env (or the environment).
"""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import dotenv_values  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agents import cao_agent, registry  # noqa: E402
from app.agents import cross_validation as cv  # noqa: E402
from app.governance import audit, db, kill_switch  # noqa: E402
from app.governance.rate_limit import limiter  # noqa: E402

TEST_AGENT = "testbot"
KEY = "test-internal-key"
GOOD = (
    "Reasoning: based on the data [1]. Confidence: 85%. Sources: [1].\n\n"
    "Recommendation for human review: choose whether to proceed."
)


def _local_url() -> str | None:
    url = os.getenv("GOVERNANCE_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get("GOVERNANCE_DATABASE_URL") or ""
    try:
        host = db.parse_url(url)["host"] if url else ""
    except db.GovernanceDBError:
        return None
    return url if host in ("localhost", "127.0.0.1", "::1") else None


LOCAL_URL = _local_url()


def _reachable() -> bool:
    if not LOCAL_URL:
        return False
    with mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL}):
        try:
            with db.transaction() as conn:
                conn.cursor().execute('SELECT 1 FROM "AgentState" LIMIT 1')
            return True
        except Exception:
            return False


DB_UP = _reachable()


def _cleanup():
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM "AgentAuditLog" WHERE "agentId" = %s', (TEST_AGENT,))
        cur.execute('DELETE FROM "AgentState" WHERE "agentId" = %s', (TEST_AGENT,))
        cur.execute('DELETE FROM "CrossValidationLog" WHERE "agentAId" = %s OR "agentBId" = %s', (TEST_AGENT, TEST_AGENT))
        cur.execute('DELETE FROM "AdminAuditLog" WHERE "targetId" = %s', (TEST_AGENT,))


def _query(sql: str, params=()):
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return db.fetch_all(cur)


@unittest.skipUnless(DB_UP, "needs the local prisma dev database (GOVERNANCE_DATABASE_URL on localhost) with migrations applied")
class DbTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL, "INTERNAL_CAO_KEY": KEY}),
            mock.patch.dict(registry.AGENTS, {TEST_AGENT: registry.AgentSpec(TEST_AGENT, "Test", "INTERNAL_COO_KEY", None)}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        _cleanup()
        self.addCleanup(_cleanup)
        # Safety net: no database test may reach OpenAI or Pinecone. Importing app.main runs
        # load_dotenv(), which loads the REAL keys from backend/.env and would otherwise let the
        # CAO endpoint run real retrieval. Retrieval is therefore stubbed for every test here.
        from app.rag import retrieval

        rag_stub = mock.patch.object(
            retrieval,
            "retrieve_context_with_sources",
            mock.AsyncMock(return_value=retrieval.RetrievalResult(context="", sources=[])),
        )
        self.rag_stub = rag_stub.start()
        self.addCleanup(rag_stub.stop)
        limiter.reset()


class AuditLogTests(DbTestCase):
    async def test_record_response_writes_an_audit_row_and_agent_state(self):
        audit_id, score = await audit.record_response(
            TEST_AGENT, "user_abc", "What next?", GOOD, ["NUS_trial.pdf", "general knowledge"]
        )
        self.assertIsInstance(audit_id, int)
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (audit_id,))[0]
        self.assertEqual((row["agentId"], row["userId"], row["query"]), (TEST_AGENT, "user_abc", "What next?"))
        self.assertEqual(row["response"], GOOD)
        self.assertEqual(row["constitutionalScore"], score.score)
        self.assertEqual(row["sourcesCited"], ["NUS_trial.pdf", "general knowledge"])
        self.assertEqual(row["humanAction"], "Pending")
        self.assertIsNotNone(row["timestamp"])
        state = audit.snapshot_sync(TEST_AGENT)
        self.assertEqual(len(state["conversationHistory"]), 1)
        self.assertEqual(state["conversationHistory"][0]["userId"], "user_abc")
        self.assertEqual(state["resetEpoch"], 0)

    async def test_timestamp_is_stored_as_utc(self):
        from datetime import datetime, timezone

        before = datetime.now(timezone.utc).replace(tzinfo=None)
        audit_id, _ = await audit.record_response(TEST_AGENT, "u", "q", GOOD, [])
        stamp = _query('SELECT "timestamp" FROM "AgentAuditLog" WHERE id = %s', (audit_id,))[0]["timestamp"]
        self.assertLess(abs((stamp - before).total_seconds()), 60)

    async def test_non_compliant_answers_count_as_violations(self):
        await audit.record_response(TEST_AGENT, "u", "q", "Just do it.", [])
        await audit.record_response(TEST_AGENT, "u", "q", GOOD, [])
        self.assertEqual(audit.snapshot_sync(TEST_AGENT)["constitutionalViolations"], 1)

    async def test_rolling_buffer_is_capped_and_keeps_the_newest(self):
        for i in range(audit.MAX_BUFFER + 3):
            await audit.record_response(TEST_AGENT, "u", f"question {i}", GOOD, [])
        history = audit.snapshot_sync(TEST_AGENT)["conversationHistory"]
        self.assertEqual(len(history), audit.MAX_BUFFER)
        self.assertEqual(history[-1]["query"], f"question {audit.MAX_BUFFER + 2}")
        self.assertEqual(history[0]["query"], "question 3")
        self.assertEqual(len(_query('SELECT id FROM "AgentAuditLog" WHERE "agentId" = %s', (TEST_AGENT,))), audit.MAX_BUFFER + 3)

    async def test_human_action_can_be_updated_and_validated(self):
        audit_id, _ = await audit.record_response(TEST_AGENT, "u", "q", GOOD, [])
        self.assertTrue(await audit.set_human_action(audit_id, "Approved"))
        self.assertEqual(_query('SELECT "humanAction" FROM "AgentAuditLog" WHERE id = %s', (audit_id,))[0]["humanAction"], "Approved")
        self.assertFalse(await audit.set_human_action(audit_id + 100000, "Rejected"))
        with self.assertRaises(ValueError):
            await audit.set_human_action(audit_id, "Maybe")

    async def test_cao_answer_is_logged_end_to_end(self):
        async def create(**kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=GOOD)], stop_reason="end_turn")

        with mock.patch.object(cao_agent, "AGENT_ID", TEST_AGENT), mock.patch.object(
            cao_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))
        ):
            result = await cao_agent.get_cao_result("Should we expand?", use_rag=False, user_id="user_staff_1")
        self.assertIsNotNone(result.audit_id)
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertEqual((row["agentId"], row["userId"], row["query"]), (TEST_AGENT, "user_staff_1", "Should we expand?"))
        self.assertEqual(row["constitutionalScore"], result.constitutional_score)
        self.assertIn("Recommendation for human review:", row["response"])

    async def test_cao_endpoint_returns_governance_fields_and_honours_a_stale_epoch(self):
        seen = []

        async def create(**kwargs):
            seen.append(kwargs["messages"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=GOOD)], stop_reason="end_turn")

        from app.main import app

        client = TestClient(app)
        headers = {"X-Internal-CAO-Key": KEY, "X-User-Id": "user_staff_1"}
        history = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}]
        with mock.patch.object(cao_agent, "AGENT_ID", TEST_AGENT), mock.patch.object(
            cao_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))
        ), mock.patch("app.api.cao.audit.current_epoch", mock.AsyncMock(return_value=3)):
            fresh = client.post("/api/cao", headers=headers, json={"message": "now", "history": history, "reset_epoch": 3})
            stale = client.post("/api/cao", headers=headers, json={"message": "now", "history": history, "reset_epoch": 2})
        self.assertEqual(fresh.status_code, 200)
        body = fresh.json()
        self.assertEqual((body["history_discarded"], body["reset_epoch"]), (False, 3))
        self.assertIn("constitutional_score", body)
        self.assertIsInstance(body["audit_id"], int)
        self.assertEqual(len(seen[0]), 3)  # history + the new question reached the model
        self.assertTrue(stale.json()["history_discarded"])
        self.assertEqual(seen[1], [{"role": "user", "content": "now"}])  # the cancelled history never reached the model


class KillSwitchTests(DbTestCase):
    async def _populate(self, n=3):
        for i in range(n):
            await audit.record_response(TEST_AGENT, "user_abc", f"q{i}", "Just do it.", [])

    async def test_reset_clears_history_bumps_epoch_and_logs_who_and_when(self):
        await self._populate(3)
        before = audit.snapshot_sync(TEST_AGENT)
        self.assertEqual((len(before["conversationHistory"]), before["constitutionalViolations"]), (3, 3))

        result = await kill_switch.reset_agent(TEST_AGENT, "admin_42", "10.1.2.3")

        self.assertEqual((result["clearedEntries"], result["resetEpoch"]), (3, 1))
        after = audit.snapshot_sync(TEST_AGENT)
        self.assertEqual(after["conversationHistory"], [])  # history is cleared in the database
        self.assertEqual((after["resetEpoch"], after["constitutionalViolations"]), (1, 0))
        self.assertIsNotNone(after["lastReset"])
        log = _query('SELECT * FROM "AdminAuditLog" WHERE "targetId" = %s', (TEST_AGENT,))
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["actorId"], log[0]["action"], log[0]["ipAddress"]), ("admin_42", "agent_reset", "10.1.2.3"))
        self.assertIsNotNone(log[0]["timestamp"])
        details = log[0]["details"] if isinstance(log[0]["details"], dict) else json.loads(log[0]["details"])
        self.assertEqual((details["clearedEntries"], details["epochBefore"], details["epochAfter"]), (3, 0, 1))

    async def test_reset_keeps_the_audit_trail(self):
        await self._populate(2)
        await kill_switch.reset_agent(TEST_AGENT, "admin_42")
        self.assertEqual(len(_query('SELECT id FROM "AgentAuditLog" WHERE "agentId" = %s', (TEST_AGENT,))), 2)

    async def test_reset_of_a_never_used_agent_works_and_epochs_keep_counting(self):
        first = await kill_switch.reset_agent(TEST_AGENT, "admin_42")
        second = await kill_switch.reset_agent(TEST_AGENT, "admin_42")
        self.assertEqual((first["clearedEntries"], first["resetEpoch"], second["resetEpoch"]), (0, 1, 2))

    async def test_current_epoch_reflects_resets(self):
        self.assertEqual(await audit.current_epoch(TEST_AGENT), 0)
        await kill_switch.reset_agent(TEST_AGENT, "admin_42")
        self.assertEqual(await audit.current_epoch(TEST_AGENT), 1)

    async def test_reset_is_atomic_if_the_log_write_fails_nothing_changes(self):
        await self._populate(3)
        original = kill_switch.db.json_param

        def explode(_value):  # runs inside the transaction, AFTER the state row was already updated
            raise RuntimeError("simulated failure while writing the log line")

        with mock.patch.object(kill_switch.db, "json_param", explode):
            with self.assertRaises(db.GovernanceDBError):
                await kill_switch.reset_agent(TEST_AGENT, "admin_42")
        self.assertIs(kill_switch.db.json_param, original)

        state = audit.snapshot_sync(TEST_AGENT)
        self.assertEqual(len(state["conversationHistory"]), 3)  # NOT cleared
        self.assertEqual((state["resetEpoch"], state["constitutionalViolations"]), (0, 3))
        self.assertIsNone(state["lastReset"])
        self.assertEqual(_query('SELECT id FROM "AdminAuditLog" WHERE "targetId" = %s', (TEST_AGENT,)), [])

    async def test_unknown_agent_is_refused_and_nothing_is_written(self):
        with self.assertRaises(kill_switch.UnknownAgentError):
            await kill_switch.reset_agent("cfo", "admin_42")
        self.assertEqual(_query('SELECT id FROM "AgentState" WHERE "agentId" = %s', ("cfo",)), [])

    async def test_reset_endpoint_end_to_end(self):
        from app.main import app

        await self._populate(2)
        client = TestClient(app)
        headers = {"X-Internal-CAO-Key": KEY, "X-Admin-Id": "user_admin_7"}
        response = client.post(f"/api/admin/agents/{TEST_AGENT}/reset", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["agentId"], body["clearedEntries"], body["resetEpoch"]), (TEST_AGENT, 2, 1))
        self.assertEqual(audit.snapshot_sync(TEST_AGENT)["conversationHistory"], [])
        self.assertEqual(_query('SELECT "actorId" FROM "AdminAuditLog" WHERE "targetId" = %s', (TEST_AGENT,))[0]["actorId"], "user_admin_7")


class CrossValidationLogTests(DbTestCase):
    async def test_completed_check_is_written_to_the_cross_validation_log(self):
        async def reviewer(prompt):
            return '{"valid": false, "reasoning": "Missing cost risk.", "confidence": 0.7, "constitutional_alignment": "partially_aligned"}'

        with mock.patch.dict(registry.AGENTS, {"coo": registry.AgentSpec("coo", "COO", "INTERNAL_COO_KEY", None)}):
            result = await cv.cross_check(TEST_AGENT, "coo", "Expand?", GOOD, reviewer=reviewer)
        self.assertTrue(result.logged)
        rows = _query('SELECT * FROM "CrossValidationLog" WHERE "agentAId" = %s', (TEST_AGENT,))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["agentBId"], rows[0]["query"]), ("coo", "Expand?"))
        stored = rows[0]["response"] if isinstance(rows[0]["response"], dict) else json.loads(rows[0]["response"])
        self.assertEqual(stored["valid"], False)
        self.assertEqual(stored["constitutional_alignment"], "partially_aligned")


if __name__ == "__main__":
    unittest.main()
