"""
Phase 6.6: the once-only execution claim and the stored outcome, against REAL Postgres (the LOCAL `prisma dev` server only;
skipped otherwise, and it never touches Neon). The in-memory stand-in used elsewhere applies the same rule but is not Postgres,
so this is the test that proves the SQL itself (jsonb_set, jsonb_typeof, jsonb_exists, the atomic UPDATE).

    cd frontend; npx prisma dev start caip-local          (once)
    cd backend;  python -m unittest tests.test_execution_claim_sql

Every row it creates carries userId = TAG and is deleted afterwards. GitHub is faked: nothing here reaches the network.
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.governance import admin_store  # noqa: E402

from tests.test_mission_control_api import (  # noqa: E402
    COUNCIL_KEY, CTO_REQUEST, DB_READY, LOCAL_URL, _cleanup, _insert_audit, _sql,
)

PLAN = {"branch_name": "agent/x", "pr_title": "t", "pr_body": "", "files_to_update": [{"path": "frontend/a.ts", "content": "x", "commit_message": "m"}]}


def request(**extra):
    return {**CTO_REQUEST, "execution_plan": PLAN, **extra}


def stored(audit_id):
    row = _sql('SELECT "authorizationRequest" FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))[0]
    return admin_store.parse_json(row["authorizationRequest"])


@unittest.skipUnless(DB_READY, "needs the LOCAL prisma dev database (cd frontend; npx prisma dev start caip-local). Never runs against Neon.")
class ClaimSqlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL})
        patcher.start()
        self.addCleanup(patcher.stop)
        _cleanup()
        self.addCleanup(_cleanup)

    async def test_the_claim_succeeds_once_on_an_approved_row_with_a_plan_and_records_running(self):
        audit_id = _insert_audit(request=request(), human="Approved")
        self.assertTrue(await admin_store.claim_execution(audit_id))
        self.assertEqual(stored(audit_id)["execution"], {"status": "running"})
        self.assertEqual(stored(audit_id)["execution_plan"], PLAN)  # the rest of the request is untouched

    async def test_a_second_claim_on_the_same_row_fails(self):
        audit_id = _insert_audit(request=request(), human="Approved")
        self.assertTrue(await admin_store.claim_execution(audit_id))
        self.assertFalse(await admin_store.claim_execution(audit_id))

    async def test_simultaneous_claims_cannot_both_win(self):
        for _ in range(8):  # a race is probabilistic, so try several fresh rows
            audit_id = _insert_audit(request=request(), human="Approved")
            results = await asyncio.gather(*(admin_store.claim_execution(audit_id) for _ in range(6)))
            self.assertEqual(sum(results), 1, results)

    async def test_the_claim_needs_an_approved_row_with_a_real_plan_object(self):
        cases = {
            "pending": _insert_audit(request=request(), human="Pending"),
            "rejected": _insert_audit(request=request(), human="Rejected"),
            "no_plan_key": _insert_audit(request=CTO_REQUEST, human="Approved"),
            "json_null_plan": _insert_audit(request=request(execution_plan=None), human="Approved"),
            "string_plan": _insert_audit(request=request(execution_plan="x"), human="Approved"),
            "list_plan": _insert_audit(request=request(execution_plan=[PLAN]), human="Approved"),
        }
        for name, audit_id in cases.items():
            self.assertFalse(await admin_store.claim_execution(audit_id), name)
            self.assertNotIn("execution", stored(audit_id), name)  # a refused claim writes nothing
        no_request = _insert_audit(request=None, human="Approved")
        self.assertFalse(await admin_store.claim_execution(no_request))
        self.assertFalse(await admin_store.claim_execution(2_000_000_000))  # no such row

    async def test_an_executed_row_cannot_be_claimed_again_even_after_its_result_is_recorded(self):
        audit_id = _insert_audit(request=request(), human="Approved")
        self.assertTrue(await admin_store.claim_execution(audit_id))
        outcome = {"status": "succeeded", "steps": [{"tool": "github_open_pr", "ok": True, "message": "opened"}],
                   "pr_url": "https://github.com/o/r/pull/9", "branch": "agent/x", "error": None}
        self.assertTrue(await admin_store.record_execution(audit_id, outcome))
        self.assertEqual(stored(audit_id)["execution"], outcome)
        self.assertEqual(stored(audit_id)["status"], "pending_human_approval")  # original fields untouched
        self.assertFalse(await admin_store.claim_execution(audit_id))

    async def test_the_pr_url_reads_back_exactly_as_mission_control_will_see_it(self):
        from app.api import audit_logs

        audit_id = _insert_audit(request=request(), human="Approved")
        await admin_store.claim_execution(audit_id)
        done = {"status": "succeeded", "steps": [], "pr_url": "https://github.com/o/r/pull/9", "branch": "agent/x", "error": None}
        await admin_store.record_execution(audit_id, done)
        entry = audit_logs.audit_entry(_sql('SELECT * FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))[0])
        self.assertEqual(entry["authorization_request"]["execution"]["pr_url"], "https://github.com/o/r/pull/9")
        self.assertEqual(entry["authorization_request"]["execution_plan"], PLAN)
        self.assertEqual((await admin_store.read_authorization_request(audit_id))["execution"]["status"], "succeeded")
        self.assertIsNone(await admin_store.read_authorization_request(2_000_000_000))

    async def test_record_execution_refuses_a_row_without_a_request_and_stores_a_failure_too(self):
        no_request = _insert_audit(request=None, human="Approved")
        self.assertFalse(await admin_store.record_execution(no_request, {"status": "failed"}))
        audit_id = _insert_audit(request=request(), human="Approved")
        await admin_store.claim_execution(audit_id)
        failed = {"status": "failed", "steps": [], "pr_url": None, "branch": "agent/x", "error": "GitHub 403"}
        self.assertTrue(await admin_store.record_execution(audit_id, failed))
        self.assertEqual(stored(audit_id)["execution"]["error"], "GitHub 403")


@unittest.skipUnless(DB_READY, "needs the LOCAL prisma dev database (cd frontend; npx prisma dev start caip-local). Never runs against Neon.")
class WholeFlowSqlTests(unittest.TestCase):
    """The real HTTP endpoint and the REAL SQL. Only GitHub is faked (the same fake as test_tools.py)."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.main import app
        from tests.test_tools import TOKEN, FakeGitHub

        self.client, self.gh = TestClient(app), FakeGitHub()
        env = {"INTERNAL_COUNCIL_KEY": COUNCIL_KEY, "GITHUB_PAT": TOKEN, "GITHUB_OWNER": "", "GITHUB_REPO": "", "GOVERNANCE_DATABASE_URL": LOCAL_URL}
        import requests

        for patcher in (mock.patch.dict(os.environ, env), mock.patch.object(requests, "request", self.gh)):
            patcher.start()
            self.addCleanup(patcher.stop)
        _cleanup()
        self.addCleanup(_cleanup)
        self.headers = {"X-Internal-Council-Key": COUNCIL_KEY, "X-Admin-Id": "joe"}

    def test_approve_decides_claims_runs_and_stores_the_pr_url_and_a_second_approve_changes_nothing(self):
        from tests.test_tools import good_plan

        audit_id = _insert_audit(request={**CTO_REQUEST, "execution_plan": good_plan()}, human="Pending")
        first = self.client.post(f"/api/authorization/{audit_id}/approve", headers=self.headers)
        second = self.client.post(f"/api/authorization/{audit_id}/approve", headers=self.headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["execution"]["status"], "succeeded")
        self.assertEqual(second.status_code, 409)  # a decision is final
        self.assertEqual(len(self.gh.prs), 1)  # exactly one pull request
        self.assertEqual(stored(audit_id)["execution"]["pr_url"], first.json()["execution"]["pr_url"])
        row = _sql('SELECT "humanAction","humanActionBy" FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))[0]
        self.assertEqual((row["humanAction"], row["humanActionBy"]), ("Approved", "joe"))

    def test_a_rejection_never_claims_or_runs_anything(self):
        from tests.test_tools import good_plan

        audit_id = _insert_audit(request={**CTO_REQUEST, "execution_plan": good_plan()}, human="Pending")
        response = self.client.post(f"/api/authorization/{audit_id}/reject", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.gh.calls, [])
        self.assertNotIn("execution", stored(audit_id))

    def test_a_request_without_a_plan_is_approved_with_nothing_executed(self):
        audit_id = _insert_audit(request=CTO_REQUEST, human="Pending")
        body = self.client.post(f"/api/authorization/{audit_id}/approve", headers=self.headers).json()
        self.assertNotIn("execution", body)
        self.assertEqual(self.gh.calls, [])
        self.assertNotIn("execution", stored(audit_id))

    def test_a_github_failure_is_stored_on_the_row_and_the_decision_stands(self):
        from tests.test_tools import good_plan

        self.gh.fail = {"POST /git/refs": 403}
        audit_id = _insert_audit(request={**CTO_REQUEST, "execution_plan": good_plan()}, human="Pending")
        body = self.client.post(f"/api/authorization/{audit_id}/approve", headers=self.headers).json()
        self.assertEqual(body["human_action"], "Approved")
        self.assertEqual(body["execution"]["status"], "failed")
        self.assertEqual(stored(audit_id)["execution"]["status"], "failed")
        self.assertEqual(self.gh.prs, [])


if __name__ == "__main__":
    unittest.main()

