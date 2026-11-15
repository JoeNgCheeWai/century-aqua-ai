"""
COO-led approval and the Operations Summary against REAL Postgres (the LOCAL `prisma dev` server only; skipped otherwise, never Neon).

This proves what the in-memory tests cannot: the SQL itself (the once-only review write, the summary query) and the WHOLE flow through the real
shared approval path: a safe task is reviewed by a scripted COO, approved as "AI_COO (delegated by the CEO)", a pull request is opened on a FAKE
GitHub exactly once and never merged, and the Operations Summary counts it, while an unsafe task, a "no", and a person who decides first all
leave the CEO in charge.

    cd frontend; npx prisma dev start caip-local          (once)
    cd backend;  python -m unittest tests.test_coo_operational_approval_sql

Every row carries userId = TAG and is deleted afterwards. Nothing here reaches the network.
"""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import operational_approval as oa  # noqa: E402
from app.governance import admin_store, operations_log  # noqa: E402
from tests.test_coo_operational_approval import request, scripted, verdict_json  # noqa: E402
from tests.test_mission_control_api import COUNCIL_KEY, DB_READY, LOCAL_URL, _cleanup, _insert_audit, _sql  # noqa: E402

SKIP = "needs the LOCAL prisma dev database (cd frontend; npx prisma dev start caip-local). Never runs against Neon."
NEW_UI = "frontend/src/components/mc/BrandNewBadge.tsx"


def new_file_request(**extra):
    """A safe request whose one file is BRAND NEW (the fake GitHub has no such file), so the read-before-write guard is satisfied."""
    plan = {"branch_name": "agent/badge", "pr_title": "Add a badge", "pr_body": "A small badge.",
            "files_to_update": [{"path": NEW_UI, "content": "export default function B() { return null; }\n", "commit_message": "Add badge"}]}
    # The three fields code forces on every real request (cto_authorization), so the stored row looks exactly like a production one.
    return request(execution_plan=plan, status="pending_human_approval", requires_human_approval=True, executed=False, **extra)


def stored(audit_id):
    row = _sql('SELECT "authorizationRequest","humanAction","humanActionBy" FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))[0]
    return admin_store.parse_json(row["authorizationRequest"]), row["humanAction"], row["humanActionBy"]


@unittest.skipUnless(DB_READY, SKIP)
class OperationalReviewSqlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL})
        patcher.start()
        self.addCleanup(patcher.stop)
        _cleanup()
        self.addCleanup(_cleanup)

    async def test_the_review_is_written_once_and_only_while_the_task_is_still_pending(self):
        audit_id = _insert_audit(request=new_file_request(), human="Pending")
        self.assertTrue(await oa._record(audit_id, "coo_declined", "UI fix", ["first"], None))
        self.assertFalse(await oa._record(audit_id, "coo_declined", "UI fix", ["second"], None))  # never overwritten
        self.assertEqual(stored(audit_id)[0]["operational_review"]["reasons"], ["first"])
        self.assertEqual(stored(audit_id)[0]["execution_plan"]["branch_name"], "agent/badge")  # the rest of the request is untouched
        decided = _insert_audit(request=new_file_request(), human="Rejected")
        self.assertFalse(await oa._record(decided, "coo_declined", "UI fix", ["late"], None))  # a person already decided
        self.assertNotIn("operational_review", stored(decided)[0])

    async def test_a_row_with_no_request_cannot_be_written_to(self):
        audit_id = _insert_audit(request=None, human="Pending")
        self.assertFalse(await oa._record(audit_id, "coo_declined", "UI fix", ["x"], None))

    async def test_the_summary_counts_what_the_log_really_holds_and_nothing_else(self):
        def review(route, kind="UI fix", status=None):
            body = new_file_request(operational_review={"route": route, "kind": kind, "reasons": [], "coo": None, "at": "2026-11-15T10:00:00Z"})
            if status:
                body["execution"] = {"status": status, "pr_url": "https://github.com/o/r/pull/1" if status == "succeeded" else None}
            return body

        for route, kind, status in (("coo_approved", "UI fix", "succeeded"), ("coo_approved", "UI fix", "succeeded"),
                                    ("coo_approved", "test", "failed"), ("coo_declined", "UI fix", None), ("coo_unavailable", "UI fix", None)):
            _insert_audit(request=review(route, kind, status), human="Approved" if route == "coo_approved" else "Pending", ts="2026-11-15 10:00:00")
        _insert_audit(request=new_file_request(), human="Pending", ts="2026-11-15 10:00:00")  # a task the COO never touched: not counted
        _insert_audit(request=review("coo_approved", status="succeeded"), human="Approved", ts="2020-01-01 10:00:00")  # far too old
        facts = await operations_log.collect(days=90_000)
        mine = [i for i in facts["items"] if i["kind"] is not None]
        self.assertGreaterEqual(facts["reviewed"], 5)
        self.assertGreaterEqual(len(mine), 5)
        text = operations_log.summary_text(facts)
        self.assertNotIn("resolved", text.lower())
        self.assertNotIn("zero disruption", text.lower())
        self.assertIn("not a live change", text)

    async def test_the_window_excludes_old_rows(self):
        _insert_audit(request=new_file_request(operational_review={"route": "coo_approved", "kind": "UI fix", "reasons": [], "coo": None, "at": "x"}),
                      human="Approved", ts="2020-01-01 10:00:00")
        facts = await operations_log.collect(days=7)
        self.assertEqual(facts["approved"], 0)


@unittest.skipUnless(DB_READY, SKIP)
class WholeFlowTests(unittest.IsolatedAsyncioTestCase):
    """The real approval path and the real SQL. Only GitHub (the same fake as test_tools.py) and the COO's replies are scripted."""

    def setUp(self):
        import requests

        from tests.test_tools import TOKEN, FakeGitHub

        class DefaultBranchGitHub(FakeGitHub):
            """The real contents API reads the default branch when no ref is sent (the existence check sends none); the shared fake needs one."""

            def __call__(self, method, url, headers=None, timeout=None, params=None, json=None, **kw):
                if method == "GET" and "/contents/" in url and not params:
                    params = {"ref": self.default}
                return super().__call__(method, url, headers=headers, timeout=timeout, params=params, json=json, **kw)

        self.gh = DefaultBranchGitHub()
        env = {"INTERNAL_COUNCIL_KEY": COUNCIL_KEY, "GITHUB_PAT": TOKEN, "GITHUB_OWNER": "", "GITHUB_REPO": "", "GOVERNANCE_DATABASE_URL": LOCAL_URL,
               "COO_AUTO_APPROVAL": "on"}
        for patcher in (mock.patch.dict(os.environ, env), mock.patch.object(requests, "request", self.gh),
                        mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        _cleanup()
        self.addCleanup(_cleanup)

    async def route(self, audit_id, req, reply=None):
        return await oa.maybe_route(audit_id, "cto", req, originals={}, reviewer=scripted(verdict_json() if reply is None else reply))

    async def test_a_safe_task_is_approved_by_the_coo_a_pull_request_opens_once_and_nothing_is_merged(self):
        req = new_file_request()
        audit_id = _insert_audit(request=req, human="Pending")
        outcome = await self.route(audit_id, req)
        self.assertEqual(outcome["route"], "coo_approved", outcome)
        stored_request, human, by = stored(audit_id)
        self.assertEqual((human, by), ("Approved", "AI_COO (delegated by the CEO)"))  # recorded as the COO's, not as a person's
        self.assertEqual(stored_request["operational_review"]["route"], "coo_approved")
        self.assertEqual(stored_request["operational_review"]["approved_by"], "AI_COO (delegated by the CEO)")
        self.assertEqual(stored_request["execution"]["status"], "succeeded")
        self.assertEqual(len(self.gh.prs), 1)  # exactly one pull request
        self.assertFalse(any(c["path"].endswith("/merge") for c in self.gh.calls))  # nothing was ever merged
        self.assertIs(stored_request["executed"], False)  # the request still says it is not executed (a pull request is not live)

    async def test_the_learning_loop_does_not_remember_a_delegated_approval_as_the_ceos(self):
        req = new_file_request()
        audit_id = _insert_audit(request=req, human="Pending")
        await self.route(audit_id, req)
        rows = _sql('SELECT COUNT(*) AS n FROM "ApprovedStrategies" WHERE "auditLogId"=%s', (audit_id,))
        self.assertEqual(rows[0]["n"], 0)

    async def test_the_coo_cannot_approve_the_same_task_twice_and_a_second_pull_request_never_opens(self):
        req = new_file_request()
        audit_id = _insert_audit(request=req, human="Pending")
        await self.route(audit_id, req)
        again = await self.route(audit_id, req)
        self.assertEqual(again["route"], "ceo_required")
        self.assertEqual(len(self.gh.prs), 1)

    async def test_the_coo_saying_no_leaves_it_pending_with_no_pull_request_and_logs_the_decline(self):
        req = new_file_request()
        audit_id = _insert_audit(request=req, human="Pending")
        outcome = await self.route(audit_id, req, reply=verdict_json(integrity=False))
        self.assertEqual(outcome["route"], "ceo_required")
        stored_request, human, by = stored(audit_id)
        self.assertEqual((human, by), ("Pending", None))
        self.assertEqual(stored_request["operational_review"]["route"], "coo_declined")
        self.assertEqual(self.gh.prs, [])


    async def test_a_task_the_ceo_already_rejected_is_never_approved_by_the_coo(self):
        req = new_file_request()
        audit_id = _insert_audit(request=req, human="Rejected")
        outcome = await self.route(audit_id, req)
        self.assertEqual(outcome["route"], "ceo_required")
        self.assertEqual(stored(audit_id)[1], "Rejected")
        self.assertEqual(self.gh.prs, [])

    async def test_an_unsafe_task_leaves_no_trace_and_is_not_counted(self):
        req = new_file_request(proposed_commands=["npm run build"])
        audit_id = _insert_audit(request=req, human="Pending")
        outcome = await self.route(audit_id, req)
        self.assertEqual(outcome["route"], "ceo_required")
        self.assertNotIn("operational_review", stored(audit_id)[0])
        self.assertEqual(stored(audit_id)[1], "Pending")

    async def test_the_summary_after_an_approval_counts_it_and_never_says_resolved_or_zero_disruption(self):
        req = new_file_request()
        audit_id = _insert_audit(request=req, human="Pending", ts=oa._stamp().replace("T", " ").replace("Z", ""))
        await self.route(audit_id, req)
        facts = await operations_log.collect(days=7)
        mine = [i for i in facts["items"] if i["audit_id"] == audit_id]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["route"], mine[0]["execution_status"]), ("coo_approved", "succeeded"))
        text = operations_log.summary_text(facts)
        self.assertIn("approved under your delegation", text)
        self.assertIn("opened", text)
        for banned in ("resolved", "zero disruption", "no disruption", "all clear"):
            self.assertNotIn(banned, text.lower())

    def test_the_operations_summary_endpoint_needs_the_council_key_and_returns_code_written_text(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client, headers = TestClient(app), {"X-Internal-Council-Key": COUNCIL_KEY}
        self.assertEqual(client.get("/api/operations/summary").status_code, 401)
        self.assertEqual(client.get("/api/operations/summary", headers={"X-Internal-Council-Key": "wrong"}).status_code, 401)
        body = client.get("/api/operations/summary?days=7", headers=headers).json()
        self.assertTrue(body["summary"].startswith("Operations Summary"))
        self.assertIs(body["auto_approval_enabled"], True)
        self.assertEqual(client.get("/api/operations/summary?days=0", headers=headers).status_code, 422)
        self.assertEqual(client.get("/api/operations/summary?days=91", headers=headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
