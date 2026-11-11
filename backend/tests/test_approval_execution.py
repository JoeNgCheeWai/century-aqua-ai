"""
Phase 6.6 tests: approving an authorization that carries an execution_plan opens a pull request, and ONLY that.

Through the real HTTP endpoint (POST /api/authorization/{id}/approve), with an in-memory store standing in for the database
and a fake GitHub standing in for the network. No test here reaches the network or a database.

    python -m unittest tests.test_approval_execution   (from backend/)
"""

import copy
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agents import cto_authorization, cmo_authorization  # noqa: E402
from app.governance import admin_store  # noqa: E402

from tests.test_tools import TOKEN, FakeGitHub, good_plan  # noqa: E402

COUNCIL_KEY = "council-key-aaaa"


class MemoryAuditStore:
    """
    Stands in for the database. ``claim_execution`` is the same rule as the SQL: it succeeds ONCE, only while the row is
    Approved and has an object execution_plan and no ``execution`` yet. The lock makes two simultaneous claims race the way
    the one atomic UPDATE does.
    """

    def __init__(self, request, agent="cto", human="Pending"):
        self.request, self.agent, self.human = request, agent, human
        self.calls, self.fail_store = [], False

    async def decide_authorization(self, audit_id, action, actor):
        self.calls.append(("decide", action))
        if self.human != "Pending":
            return "already_decided", {"id": audit_id, "agentId": self.agent, "humanAction": self.human}
        self.human = "Approved" if action == "approve" else "Rejected"
        return "ok", {"id": audit_id, "agentId": self.agent, "humanAction": self.human,
                      "humanActionAt": datetime(2026, 11, 12, 9, 0), "humanActionBy": actor}

    async def read_authorization_request(self, audit_id):
        self.calls.append(("read",))
        return copy.deepcopy(self.request)

    async def claim_execution(self, audit_id):
        self.calls.append(("claim",))
        plan = (self.request or {}).get("execution_plan")
        if self.human == "Approved" and isinstance(plan, dict) and "execution" not in self.request:
            self.request["execution"] = {"status": "running"}
            return True
        return False

    async def record_execution(self, audit_id, execution):
        self.calls.append(("record", execution["status"]))
        if self.fail_store:
            raise RuntimeError("db down")
        self.request["execution"] = execution
        return True


def stored_request(agent="cto", **overrides):
    """A request exactly as the normalizer would have stored it, with a valid plan."""
    if agent == "cto":
        raw = {"action_summary": "Add a hero banner", "risk_level": "low", "impact": "A visible banner on the home page.",
               "proposed_code_diff": "+ <Hero/>", "proposed_commands": [], "rollback_plan": "Close the PR.", "execution_plan": good_plan()}
        request, _ = cto_authorization.normalize_authorization_request({**raw, **overrides})
    else:
        raw = {"action_summary": "Refresh the hero", "risk_level": "low", "impact": "A visible banner.", "proposed_content": "Hero copy",
               "deployment_commands": [], "rollback_plan": "Close the PR.", "execution_plan": good_plan()}
        request, _ = cmo_authorization.normalize_authorization_request({**raw, **overrides})
    assert request is not None
    return request


class ApprovalBase(unittest.TestCase):
    def build(self, request, agent="cto", human="Pending"):
        from app.main import app

        self.client = TestClient(app)
        self.store = MemoryAuditStore(request, agent, human)
        self.gh = FakeGitHub()
        for patcher in (
            mock.patch.dict(os.environ, {"INTERNAL_COUNCIL_KEY": COUNCIL_KEY, "GITHUB_PAT": TOKEN, "GITHUB_OWNER": "", "GITHUB_REPO": "",
                                         "GOVERNANCE_DATABASE_URL": "postgres://u:p@localhost:1/x"}),
            mock.patch.object(admin_store, "decide_authorization", self.store.decide_authorization),
            mock.patch.object(admin_store, "read_authorization_request", self.store.read_authorization_request),
            mock.patch.object(admin_store, "claim_execution", self.store.claim_execution),
            mock.patch.object(admin_store, "record_execution", self.store.record_execution),
            mock.patch.object(requests, "request", self.gh),
            mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.admin = {"X-Internal-Council-Key": COUNCIL_KEY, "X-Admin-Id": "joe"}

    def act(self, action, audit_id=7):
        return self.client.post(f"/api/authorization/{audit_id}/{action}", headers=self.admin)


class ApproveRunsThePlanTests(ApprovalBase):
    def test_approving_a_cto_plan_opens_one_pull_request_and_reports_it(self):
        self.build(stored_request("cto"))
        response = self.act("approve")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["human_action"], "Approved")
        self.assertEqual(body["execution"]["status"], "succeeded")
        self.assertEqual(body["execution"]["pr_url"], f"https://github.com/JoeNgCheeWai/century-aqua-ai/pull/1")
        self.assertIn("NOT merged", body["note"])
        self.assertIs(body["executed"], False)  # the CHANGE is not live: a human still has to merge the pull request
        self.assertEqual(len(self.gh.prs), 1)
        self.assertIn("approved audit entry #7", self.gh.prs[0]["body"])
        self.assertIn("AI_CTO", self.gh.prs[0]["body"])

    def test_the_pull_request_url_is_written_back_into_the_audit_row_so_mission_control_shows_it(self):
        self.build(stored_request("cto"))
        self.act("approve")
        execution = self.store.request["execution"]
        self.assertEqual(execution["status"], "succeeded")
        self.assertEqual(execution["pr_url"], "https://github.com/JoeNgCheeWai/century-aqua-ai/pull/1")
        self.assertEqual([s["tool"] for s in execution["steps"]],
                         ["github_create_branch", "github_write_file", "github_write_file", "github_open_pr"])
        self.assertNotIn(TOKEN, repr(self.store.request))

    def test_the_cmo_works_the_same_way(self):
        self.build(stored_request("cmo"), agent="cmo")
        body = self.act("approve").json()
        self.assertEqual(body["execution"]["status"], "succeeded")
        self.assertIn("AI_CMO", self.gh.prs[0]["body"])

    def test_the_decision_is_recorded_before_anything_runs(self):
        self.build(stored_request("cto"))
        self.act("approve")
        kinds = [c[0] for c in self.store.calls]
        self.assertEqual(kinds[0], "decide")
        self.assertLess(kinds.index("decide"), kinds.index("claim"))
        self.assertLess(kinds.index("claim"), kinds.index("record"))

    def test_a_request_without_a_plan_behaves_exactly_as_before_nothing_executed_no_network(self):
        request = stored_request("cto")
        request["execution_plan"] = None
        self.build(request)
        body = self.act("approve").json()
        self.assertNotIn("execution", body)
        self.assertEqual(body["note"], "Decision recorded. Nothing was executed: a human must carry out the proposed change.")
        self.assertEqual(self.gh.calls, [])
        self.assertNotIn("claim", [c[0] for c in self.store.calls])

    def test_a_rejection_never_executes_anything_even_with_a_plan(self):
        self.build(stored_request("cto"))
        body = self.act("reject").json()
        self.assertEqual(body["human_action"], "Rejected")
        self.assertNotIn("execution", body)
        self.assertEqual(self.gh.calls, [])
        self.assertEqual([c[0] for c in self.store.calls], ["decide"])

    def test_approving_twice_is_refused_and_opens_no_second_pull_request(self):
        self.build(stored_request("cto"))
        self.assertEqual(self.act("approve").status_code, 200)
        again = self.act("approve")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(len(self.gh.prs), 1)

    def test_a_second_claim_on_the_same_approval_runs_nothing(self):
        # Even if the decision step were somehow passed twice (a retry, a race), the once-only claim stops a second run.
        self.build(stored_request("cto"))
        self.act("approve")
        calls_before = len(self.gh.calls)
        from app.api import authorization
        import asyncio

        outcome = asyncio.run(authorization._execute_if_planned(7, "joe", "cto"))
        self.assertEqual(outcome["status"], "already_claimed")
        self.assertEqual(len(self.gh.calls), calls_before)
        self.assertEqual(len(self.gh.prs), 1)



class FailureAndTamperingTests(ApprovalBase):
    def test_a_github_failure_never_undoes_the_decision_and_is_reported_with_what_finished(self):
        self.build(stored_request("cto"))
        self.gh.fail = {"PUT contents/frontend/src/app/about/page.tsx": 403}
        response = self.act("approve")
        self.assertEqual(response.status_code, 200)  # the decision stands
        body = response.json()
        self.assertEqual(body["human_action"], "Approved")
        self.assertEqual(body["execution"]["status"], "failed")
        self.assertIn("could not be completed", body["note"])
        self.assertEqual([s["ok"] for s in body["execution"]["steps"]], [True, True, False])
        self.assertEqual(self.gh.prs, [])
        self.assertEqual(self.store.request["execution"]["status"], "failed")  # recorded, so the UI can show it

    def test_a_failed_execution_is_not_silently_retried_by_asking_again(self):
        self.build(stored_request("cto"))
        self.gh.fail = {"POST /git/refs": 500}
        self.act("approve")
        calls = len(self.gh.calls)
        self.assertEqual(self.act("approve").status_code, 409)  # already decided
        self.assertEqual(len(self.gh.calls), calls)

    def test_a_missing_token_is_a_reported_failure_not_a_crash_and_the_decision_stands(self):
        self.build(stored_request("cto"))
        with mock.patch.dict(os.environ, {"GITHUB_PAT": ""}):
            body = self.act("approve").json()
        self.assertEqual(body["human_action"], "Approved")
        self.assertEqual(body["execution"]["status"], "unconfigured")
        self.assertIn("GITHUB_PAT", body["execution"]["error"])
        self.assertEqual(self.gh.calls, [])

    def test_a_stored_plan_tampered_in_the_database_is_refused_at_run_time(self):
        request = stored_request("cto")
        request["execution_plan"]["files_to_update"].append({"path": ".github/workflows/pr-check.yml", "content": "x", "commit_message": "m"})
        self.build(request)
        body = self.act("approve").json()
        self.assertEqual(body["execution"]["status"], "refused")
        self.assertEqual(self.gh.calls, [])  # nothing reached GitHub, not even the branch

    def test_a_tampered_branch_name_cannot_reach_the_default_branch(self):
        request = stored_request("cto")
        request["execution_plan"]["branch_name"] = "main"
        self.build(request)
        self.act("approve")
        self.assertEqual(self.gh.branches["main"], self.gh.sha)
        self.assertTrue(all(b.startswith("agent/") for (b, _f) in self.gh.files))

    def test_if_the_result_cannot_be_stored_the_response_says_so_and_the_pr_is_not_lost(self):
        self.build(stored_request("cto"))
        self.store.fail_store = True
        body = self.act("approve").json()
        self.assertEqual(body["human_action"], "Approved")
        self.assertEqual(body["execution"]["status"], "succeeded")
        self.assertIn("could not be stored", body["execution"]["error"])
        self.assertEqual(len(self.gh.prs), 1)

    def test_an_unexpected_error_while_reading_the_request_still_leaves_the_decision_standing(self):
        self.build(stored_request("cto"))

        async def boom(audit_id):
            raise RuntimeError("db exploded")

        with mock.patch.object(admin_store, "read_authorization_request", boom):
            response = self.act("approve")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["human_action"], "Approved")
        self.assertEqual(self.gh.calls, [])

    def test_the_approval_still_needs_the_council_key_and_an_admin_id(self):
        self.build(stored_request("cto"))
        self.assertEqual(self.client.post("/api/authorization/7/approve").status_code, 401)
        self.assertEqual(self.client.post("/api/authorization/7/approve", headers={"X-Internal-Council-Key": COUNCIL_KEY}).status_code, 400)
        self.assertEqual(self.gh.calls, [])



class NormalizerAcceptsPlansTests(unittest.TestCase):
    def test_a_cto_request_can_be_just_a_plan_and_keeps_it(self):
        request, findings = cto_authorization.normalize_authorization_request(
            {"action_summary": "Add banner", "risk_level": "low", "impact": "visible", "execution_plan": good_plan()})
        self.assertIsNotNone(request, findings)
        self.assertEqual(request["execution_plan"]["branch_name"], "agent/add-hero-banner")
        self.assertEqual(request["status"], "pending_human_approval")
        self.assertIs(request["executed"], False)

    def test_a_request_with_only_a_refused_plan_is_dropped_with_the_reason(self):
        bad = good_plan(files_to_update=[{"path": ".env", "content": "x", "commit_message": "m"}])
        request, findings = cto_authorization.normalize_authorization_request({"action_summary": "x", "execution_plan": bad})
        self.assertIsNone(request)
        self.assertTrue(any("protected" in f for f in findings), findings)

    def test_a_refused_plan_does_not_sink_a_request_that_still_has_a_diff(self):
        request, findings = cto_authorization.normalize_authorization_request(
            {"action_summary": "x", "risk_level": "low", "impact": "i", "proposed_code_diff": "--- a/x\n+++ b/x",
             "execution_plan": good_plan(branch_name="")})
        self.assertIsNotNone(request)
        self.assertIsNone(request["execution_plan"])
        self.assertTrue(any("execution_plan refused" in f for f in findings))

    def test_a_plan_cannot_lower_the_risk_and_a_backend_file_raises_it_to_high(self):
        base = {"action_summary": "x", "risk_level": "low", "impact": "i"}
        ui, _ = cto_authorization.normalize_authorization_request({**base, "execution_plan": good_plan()})
        self.assertEqual(ui["risk_level"], "medium")  # any plan writes files: at least medium
        backend = good_plan(files_to_update=[{"path": "backend/app/main.py", "content": "x = 1\n", "commit_message": "m"}])
        raised, findings = cto_authorization.normalize_authorization_request({**base, "execution_plan": backend})
        self.assertEqual(raised["risk_level"], "high")
        self.assertTrue(any("raised from low to high" in f for f in findings))

    def test_a_plan_whose_code_contains_a_destructive_command_is_floored_by_the_same_patterns_as_a_diff(self):
        plan = good_plan(files_to_update=[{"path": "frontend/scripts/x.sh", "content": "git push --force origin main\n", "commit_message": "m"}])
        request, _ = cto_authorization.normalize_authorization_request({"action_summary": "x", "risk_level": "low", "impact": "i", "execution_plan": plan})
        self.assertEqual(request["risk_level"], "critical")

    def test_a_secret_in_a_plan_drops_the_plan(self):
        content = "const k = 'ghp_" + "Z" * 30 + "';"
        plan = good_plan(files_to_update=[{"path": "frontend/src/a.ts", "content": content, "commit_message": "m"}])
        request, findings = cto_authorization.normalize_authorization_request({"action_summary": "x", "proposed_code_diff": "d", "execution_plan": plan})
        self.assertIsNone(request["execution_plan"])
        self.assertTrue(any("secret" in f for f in findings))

    def test_cmo_mockup_urls_keep_only_the_placeholder_host_and_a_plan_alone_is_enough(self):
        urls = ["https://placehold.co/800x600/1a1a2e/00ffff?text=CMO+Hero", "https://evil.example/x.png", "http://placehold.co/x", 5, "https://placehold.co/a b"]
        request, findings = cmo_authorization.normalize_authorization_request(
            {"action_summary": "Refresh hero", "risk_level": "low", "impact": "i", "mockup_urls": urls, "execution_plan": good_plan()})
        self.assertEqual(request["mockup_urls"], ["https://placehold.co/800x600/1a1a2e/00ffff?text=CMO+Hero"])
        self.assertTrue(any("mockup URL" in f for f in findings))
        self.assertIsNotNone(request["execution_plan"])

    def test_cmo_mockups_are_capped_and_deduplicated(self):
        urls = [f"https://placehold.co/{i}" for i in range(9)] + ["https://placehold.co/0"]
        request, _ = cmo_authorization.normalize_authorization_request(
            {"action_summary": "x", "risk_level": "low", "impact": "i", "proposed_content": "c", "mockup_urls": urls})
        self.assertEqual(len(request["mockup_urls"]), cmo_authorization.MAX_MOCKUPS)
        self.assertEqual(len(set(request["mockup_urls"])), len(request["mockup_urls"]))


if __name__ == "__main__":
    unittest.main()

