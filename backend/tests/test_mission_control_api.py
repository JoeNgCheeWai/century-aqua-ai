"""
Tests for the Phase 6 Mission Control backend: GET /api/audit-logs, POST /api/authorization/{id}/approve|reject and
GET /api/council/sessions. Run from backend/:   python -m unittest tests.test_mission_control_api

Three groups:
  1. pure helpers (date bounds, LIKE escaping, the audit entry shape)                  no database
  2. the HTTP API with the store replaced by a fake (keys, validation, codes, route order)   no database
  3. the REAL SQL against a LOCAL database only (localhost, the `prisma dev` server); skipped otherwise. It never
     touches Neon, tags every row it creates and deletes them afterwards. It needs the add_human_action_audit
     migration; if that is not applied locally the group is skipped with a message saying so.
"""

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import dotenv_values  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import audit_logs  # noqa: E402
from app.governance import admin_store, db  # noqa: E402

COUNCIL_KEY = "council-key-aaaa"
AGENT_KEYS = {"INTERNAL_CAO_KEY": "cao-key-1", "INTERNAL_COO_KEY": "coo-key-2", "INTERNAL_CTO_KEY": "cto-key-3",
              "INTERNAL_CMO_KEY": "cmo-key-4"}
AGENT_HEADERS = {"INTERNAL_CAO_KEY": "X-Internal-CAO-Key", "INTERNAL_COO_KEY": "X-Internal-COO-Key",
                 "INTERNAL_CTO_KEY": "X-Internal-CTO-Key", "INTERNAL_CMO_KEY": "X-Internal-CMO-Key"}
CTO_REQUEST = {"action_summary": "Patch the login", "risk_level": "high", "impact": "auth", "proposed_code_diff": "-a\n+b",
               "proposed_commands": [], "rollback_plan": "revert", "status": "pending_human_approval",
               "requires_human_approval": True, "executed": False}


def audit_row(**over):
    row = {"id": 7, "agentId": "cto", "userId": "u1", "query": "q", "response": "r", "constitutionalScore": 90,
           "sourcesCited": ["a"], "reasoningChain": ["s1"], "scenarios": None, "preConsultationRequests": None,
           "authorizationRequest": CTO_REQUEST, "humanAction": "Pending", "humanActionAt": None, "humanActionBy": None,
           "timestamp": datetime(2026, 11, 10, 8, 30, 0)}
    row.update(over)
    return row


# ---------------------------------------------------------------------------------------------- 1. pure helpers

class HelperTests(unittest.TestCase):
    def test_a_plain_start_date_is_midnight_utc_and_a_plain_end_date_includes_the_whole_day(self):
        self.assertEqual(audit_logs.parse_bound("2026-11-10", "start_date", end=False), datetime(2026, 11, 10, 0, 0))
        # exclusive upper bound = the start of the NEXT day, so 17:00 on 2026-11-10 is inside end_date=2026-11-10
        self.assertEqual(audit_logs.parse_bound("2026-11-10", "end_date", end=True), datetime(2026, 11, 11, 0, 0))
        self.assertEqual(audit_logs.parse_bound("2026-12-31", "end_date", end=True), datetime(2027, 1, 1, 0, 0))

    def test_a_datetime_honours_its_timezone_and_defaults_to_utc(self):
        self.assertEqual(audit_logs.parse_bound("2026-11-10T08:00:00+08:00", "start_date", end=False), datetime(2026, 11, 10, 0, 0))
        self.assertEqual(audit_logs.parse_bound("2026-11-10T08:00:00Z", "start_date", end=False), datetime(2026, 11, 10, 8, 0))
        self.assertEqual(audit_logs.parse_bound("2026-11-10T08:00:00", "start_date", end=False), datetime(2026, 11, 10, 8, 0))

    def test_blank_is_no_bound_and_garbage_is_a_422(self):
        self.assertIsNone(audit_logs.parse_bound(None, "start_date", end=False))
        self.assertIsNone(audit_logs.parse_bound("  ", "start_date", end=False))
        for bad in ("yesterday", "2026-13-40", "10/11/2026", "2026-11"):
            with self.assertRaises(HTTPException) as caught:
                audit_logs.parse_bound(bad, "start_date", end=False)
            self.assertEqual(caught.exception.status_code, 422, bad)

    def test_like_wildcards_in_the_search_text_are_made_literal(self):
        self.assertEqual(admin_store._like("50%_off"), "%50\\%\\_off%")
        self.assertEqual(admin_store._like("a\\b"), "%a\\\\b%")

    def test_times_are_reported_as_utc_with_a_z(self):
        self.assertEqual(admin_store.iso_utc(datetime(2026, 11, 10, 8, 30)), "2026-11-10T08:30:00Z")
        self.assertIsNone(admin_store.iso_utc(None))

    def test_the_audit_entry_has_the_full_record_and_parses_json_columns_that_arrive_as_text(self):
        import json

        entry = audit_logs.audit_entry(audit_row(authorizationRequest=json.dumps(CTO_REQUEST),
                                                 scenarios='{"baseline": {}}', constitutionalScore=60))
        self.assertEqual(entry["authorization_request"]["proposed_code_diff"], "-a\n+b")
        self.assertEqual(entry["scenarios"], {"baseline": {}})
        self.assertTrue(entry["needs_review"])  # 60 is below the review threshold
        self.assertEqual(entry["timestamp"], "2026-11-10T08:30:00Z")
        for field in ("id", "agent_id", "user_id", "query", "response", "constitutional_score", "sources_cited",
                      "reasoning_chain", "pre_consultation_requests", "human_action", "human_action_at", "human_action_by"):
            self.assertIn(field, entry)

    def test_a_row_without_a_request_has_a_null_one_and_missing_decision_columns_are_tolerated(self):
        row = audit_row(authorizationRequest=None)
        del row["humanActionAt"], row["humanActionBy"]  # a database without the new migration
        entry = audit_logs.audit_entry(row)
        self.assertIsNone(entry["authorization_request"])
        self.assertIsNone(entry["human_action_at"])
        self.assertIsNone(entry["human_action_by"])


# ---------------------------------------------------------------------------------------------- 2. the HTTP API

class FakeStore:
    """Records what the API asked of the store and answers with canned data."""

    def __init__(self):
        self.calls = []
        self.audit_rows, self.audit_total = [audit_row()], 1
        self.decision = ("ok", {"id": 7, "agentId": "cto", "humanAction": "Approved",
                                "humanActionAt": datetime(2026, 11, 10, 9, 0, 0), "humanActionBy": "joe"})
        self.sessions = [{"id": "s1", "query": "Q" * 500, "status": "RESOLVED", "participatingAgents": ["cao", "coo"],
                          "startedAt": datetime(2026, 11, 10, 8, 0), "completedAt": datetime(2026, 11, 10, 8, 1),
                          "modelCalls": 6, "totalTokens": 100, "humanAction": "Pending", "escalationReason": None,
                          "optionCount": 3}]
        self.raise_error = False

    async def list_audit_logs(self, **kwargs):
        self.calls.append(("audit", kwargs))
        if self.raise_error:
            raise RuntimeError("boom")
        return self.audit_rows, self.audit_total

    async def decide_authorization(self, audit_id, action, actor):
        self.calls.append(("decide", audit_id, action, actor))
        if self.raise_error:
            raise RuntimeError("column humanActionAt does not exist")
        return self.decision

    async def list_council_sessions(self, **kwargs):
        self.calls.append(("sessions", kwargs))
        if self.raise_error:
            raise RuntimeError("boom")
        return self.sessions, 1


class ApiBase(unittest.TestCase):
    def setUp(self):
        from app.main import app

        self.client = TestClient(app)
        self.store = FakeStore()
        for patcher in (
            mock.patch.dict(os.environ, {"INTERNAL_COUNCIL_KEY": COUNCIL_KEY, **AGENT_KEYS,
                                         "GOVERNANCE_DATABASE_URL": "postgres://u:p@localhost:1/x"}),
            mock.patch.object(admin_store, "list_audit_logs", self.store.list_audit_logs),
            mock.patch.object(admin_store, "decide_authorization", self.store.decide_authorization),
            mock.patch.object(admin_store, "list_council_sessions", self.store.list_council_sessions),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.headers = {"X-Internal-Council-Key": COUNCIL_KEY}
        self.admin = {**self.headers, "X-Admin-Id": "joe"}

    def send(self, method, path, headers=None):
        return self.client.request(method, path, headers=headers)


ENDPOINTS = [("GET", "/api/audit-logs"), ("GET", "/api/council/sessions"),
             ("POST", "/api/authorization/7/approve"), ("POST", "/api/authorization/7/reject")]


class KeySeparationTests(ApiBase):
    def test_every_endpoint_needs_the_council_key_and_does_no_work_without_it(self):
        for method, path in ENDPOINTS:
            self.assertEqual(self.send(method, path).status_code, 401, path)
            self.assertEqual(self.send(method, path, {"X-Internal-Council-Key": "wrong", "X-Admin-Id": "joe"}).status_code, 401, path)
        self.assertEqual(self.store.calls, [])  # the key is checked before the database is touched

    def test_no_agent_key_opens_any_of_them_in_any_header(self):
        for method, path in ENDPOINTS:
            for env, header in AGENT_HEADERS.items():
                for sent in (header, "X-Internal-Council-Key"):
                    headers = {sent: AGENT_KEYS[env], "X-Admin-Id": "joe"}
                    self.assertEqual(self.send(method, path, headers).status_code, 401, (path, env, sent))
        self.assertEqual(self.store.calls, [])

    def test_the_council_key_opens_no_agent_endpoint(self):
        for path in ("/api/cao", "/api/coo", "/api/cto", "/api/cmo"):
            for header in (*AGENT_HEADERS.values(), "X-Internal-Council-Key"):
                response = self.client.post(path, json={"message": "hi"}, headers={header: COUNCIL_KEY})
                self.assertEqual(response.status_code, 401, (path, header))

    def test_an_unset_council_key_closes_every_endpoint_with_503(self):
        with mock.patch.dict(os.environ, {"INTERNAL_COUNCIL_KEY": ""}):
            for method, path in ENDPOINTS:
                self.assertEqual(self.send(method, path, self.admin).status_code, 503, path)
                self.assertEqual(self.send(method, path, {"X-Internal-Council-Key": "", "X-Admin-Id": "joe"}).status_code, 503, path)


class RouteOrderTests(ApiBase):
    def test_sessions_is_the_list_and_not_a_session_with_the_id_sessions(self):
        response = self.client.get("/api/council/sessions", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("items", response.json())  # the list, not the 404 'Council session not found'
        self.assertEqual(self.store.calls[0][0], "sessions")

    def test_a_real_session_id_still_reaches_the_phase_5_endpoint(self):
        async def no_such_session(session_id):
            return None, []

        with mock.patch("app.api.council.council_store.get_session", no_such_session):
            response = self.client.get("/api/council/00000000-0000-4000-8000-000000000000", headers=self.headers)
        self.assertEqual(response.status_code, 404)  # the Phase 5 'Council session not found'
        self.assertEqual([c for c in self.store.calls if c[0] == "sessions"], [])  # and NOT the list



class AuditLogApiTests(ApiBase):
    def get(self, query=""):
        return self.client.get("/api/audit-logs" + query, headers=self.headers)

    def test_returns_items_total_and_paging_with_the_full_record(self):
        body = self.get().json()
        self.assertEqual((body["total"], body["limit"], body["offset"]), (1, 50, 0))
        item = body["items"][0]
        self.assertEqual(item["agent_id"], "cto")
        self.assertEqual(item["authorization_request"]["proposed_code_diff"], "-a\n+b")
        self.assertEqual(item["human_action"], "Pending")

    def test_filters_reach_the_store_with_dates_as_naive_utc_bounds(self):
        response = self.get("?agent_id=cmo&start_date=2026-11-01&end_date=2026-11-10&limit=5&offset=10"
                            "&human_action=Pending&has_authorization=true&search=%20launch%20")
        self.assertEqual(response.status_code, 200)
        kwargs = self.store.calls[0][1]
        self.assertEqual(kwargs["agent_id"], "cmo")
        self.assertEqual(kwargs["start"], datetime(2026, 11, 1))
        self.assertEqual(kwargs["end"], datetime(2026, 11, 11))  # exclusive: all of the 10th is included
        self.assertEqual((kwargs["limit"], kwargs["offset"]), (5, 10))
        self.assertEqual((kwargs["human_action"], kwargs["has_authorization"], kwargs["search"]), ("Pending", True, "launch"))

    def test_no_filters_means_no_filters(self):
        self.get()
        kwargs = self.store.calls[0][1]
        self.assertEqual([kwargs[k] for k in ("agent_id", "start", "end", "human_action", "has_authorization", "search")],
                         [None] * 6)

    def test_bad_parameters_are_422_and_never_reach_the_database(self):
        for query in ("?limit=0", "?limit=201", "?offset=-1", "?limit=abc", "?agent_id=NOT%20VALID", "?agent_id=a;drop",
                      "?human_action=Maybe", "?start_date=yesterday", "?end_date=2026-13-45",
                      "?start_date=2026-11-10&end_date=2026-11-01", "?has_authorization=perhaps"):
            self.assertEqual(self.get(query).status_code, 422, query)
        self.assertEqual(self.store.calls, [])

    def test_a_same_day_range_is_valid_and_includes_that_day(self):
        self.assertEqual(self.get("?start_date=2026-11-10&end_date=2026-11-10").status_code, 200)

    def test_503_when_the_database_is_not_configured_or_fails(self):
        with mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": "", "DATABASE_URL": ""}):
            self.assertEqual(self.get().status_code, 503)
        self.store.raise_error = True
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("boom", response.text)  # no internals leak


class CouncilSessionsApiTests(ApiBase):
    def get(self, query=""):
        return self.client.get("/api/council/sessions" + query, headers=self.headers)

    def test_returns_summary_rows_with_a_cut_query_and_no_result_payload(self):
        body = self.get().json()
        self.assertEqual((body["total"], body["limit"], body["offset"]), (1, 20, 0))
        item = body["items"][0]
        self.assertEqual((item["session_id"], item["status"], item["participating_agents"], item["option_count"]),
                         ("s1", "RESOLVED", ["cao", "coo"], 3))
        self.assertEqual(item["started_at"], "2026-11-10T08:00:00Z")
        self.assertLessEqual(len(item["query"]), 401)  # 400 characters plus the ellipsis
        self.assertTrue(item["query"].endswith("…"))
        for heavy in ("options", "turns", "red_team_findings", "conflict_matrix"):
            self.assertNotIn(heavy, item)

    def test_status_limit_and_offset_reach_the_store(self):
        self.get("?status=FAILED&limit=5&offset=15")
        self.assertEqual(self.store.calls[0], ("sessions", {"status": "FAILED", "limit": 5, "offset": 15}))

    def test_bad_parameters_are_422(self):
        for query in ("?status=DONE", "?status=resolved", "?limit=0", "?limit=101", "?offset=-1"):
            self.assertEqual(self.get(query).status_code, 422, query)
        self.assertEqual(self.store.calls, [])

    def test_503_when_the_database_is_missing_or_fails(self):
        with mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": "", "DATABASE_URL": ""}):
            self.assertEqual(self.get().status_code, 503)
        self.store.raise_error = True
        self.assertEqual(self.get().status_code, 503)



class AuthorizationApiTests(ApiBase):
    def post(self, action, audit_id=7, headers=None):
        return self.client.post(f"/api/authorization/{audit_id}/{action}", headers=self.admin if headers is None else headers)

    def test_approve_records_the_decision_and_says_nothing_was_executed(self):
        response = self.post("approve")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["audit_id"], body["human_action"], body["human_action_by"]), (7, "Approved", "joe"))
        self.assertEqual(body["human_action_at"], "2026-11-10T09:00:00Z")
        self.assertIs(body["executed"], False)
        self.assertEqual(self.store.calls, [("decide", 7, "approve", "joe")])

    def test_reject_passes_the_reject_action(self):
        self.store.decision = ("ok", {"id": 7, "agentId": "cmo", "humanAction": "Rejected",
                                      "humanActionAt": datetime(2026, 11, 10, 9, 5), "humanActionBy": "joe"})
        body = self.post("reject").json()
        self.assertEqual(body["human_action"], "Rejected")
        self.assertEqual(self.store.calls, [("decide", 7, "reject", "joe")])

    def test_who_is_deciding_is_required_and_validated(self):
        self.assertEqual(self.post("approve", headers=self.headers).status_code, 400)  # no X-Admin-Id
        self.assertEqual(self.post("approve", headers={**self.headers, "X-Admin-Id": "bad id; drop"}).status_code, 400)
        self.assertEqual(self.store.calls, [])

    def test_the_store_outcomes_map_to_404_and_409(self):
        for outcome, status in (("not_found", 404), ("no_request", 409), ("already_decided", 409)):
            self.store.decision = (outcome, {"id": 7, "agentId": "cto", "humanAction": "Approved"})
            self.assertEqual(self.post("approve").status_code, status, outcome)

    def test_an_already_decided_request_says_what_it_was_decided_as(self):
        self.store.decision = ("already_decided", {"id": 7, "agentId": "cto", "humanAction": "Rejected"})
        self.assertIn("Rejected", self.post("approve").json()["detail"])

    def test_a_non_numeric_or_unsupported_path_is_rejected(self):
        self.assertEqual(self.post("approve", audit_id="abc").status_code, 422)
        self.assertEqual(self.post("modify").status_code, 404)  # only approve and reject exist
        self.assertEqual(self.client.get("/api/authorization/7/approve", headers=self.admin).status_code, 405)

    def test_a_database_failure_is_503_with_a_migration_hint_and_no_internals(self):
        self.store.raise_error = True
        response = self.post("approve")
        self.assertEqual(response.status_code, 503)
        self.assertIn("migration", response.json()["detail"])
        self.assertNotIn("humanActionAt does not exist", response.text)
        with mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": "", "DATABASE_URL": ""}):
            self.assertEqual(self.post("approve").status_code, 503)

    def test_the_approval_endpoint_can_only_open_a_pull_request_through_the_tool_router(self):
        # Phase 6.6 changed this on purpose. Approving may now open a PULL REQUEST, and only through app/agents/tools.py. The
        # guarantee that remains is that nothing here can run a shell command or reach GitHub by any other route.
        from app.api import authorization

        import ast

        tree = ast.parse(Path(authorization.__file__).read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        for forbidden in ("subprocess", "os", "requests", "httpx", "urllib", "urllib.request", "http.client", "socket", "shutil"):
            self.assertNotIn(forbidden, imported, forbidden)
        self.assertIn("app.agents", imported)  # `from app.agents import tools`: the ONE route to GitHub
        code = " ".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        for forbidden in ("api.github.com", "GITHUB_PAT"):
            self.assertNotIn(forbidden, code.replace(ast.get_docstring(tree) or "", ""), forbidden)
        calls = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for forbidden in ("system", "popen", "Popen", "exec", "eval"):
            self.assertNotIn(forbidden, calls, forbidden)

    def test_the_tool_router_has_no_merge_delete_force_or_shell_capability(self):
        # The router is the only module allowed to reach GitHub. Whatever it can do must stay: branch, file, pull request.
        from app.agents import tools

        import ast

        tree = ast.parse(Path(tools.__file__).read_text(encoding="utf-8"))
        # Read the CODE, not the docstrings and comments (which rightly SAY "no merge, no force push").
        doc_nodes = {id(n.body[0].value) for n in ast.walk(tree)
                     if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
                     and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)}
        code_strings = " ".join(n.value for n in ast.walk(tree)
                                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes)
        for forbidden in ("/merge", "DELETE", "force", "workflow_dispatch", "/releases", "/tags", "refs/heads/main", "refs/heads/master"):
            self.assertNotIn(forbidden, code_strings, forbidden)
        calls = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for forbidden in ("system", "popen", "run", "Popen", "exec", "eval", "check_output", "check_call"):
            self.assertNotIn(forbidden, calls, forbidden)
        used = {n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_call"
                and len(n.args) > 1 and isinstance(n.args[1], ast.Constant)}
        self.assertEqual(used, {"GET", "POST", "PUT"})  # no DELETE, no PATCH: nothing can be removed or force-moved
        self.assertEqual(sorted(tools.TOOLS), ["github_create_branch", "github_open_pr", "github_write_file", "mock_generate_image"])



# ---------------------------------------------------------------------------------------------- 3. the real SQL (LOCAL only)

TAG = "mctest"  # userId / session query prefix on every row this file creates, so cleanup is exact


def _local_url():
    url = os.getenv("GOVERNANCE_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get("GOVERNANCE_DATABASE_URL") or ""
    try:
        host = db.parse_url(url)["host"] if url else ""
    except db.GovernanceDBError:
        return None
    return url if host in ("localhost", "127.0.0.1", "::1") else None


LOCAL_URL = _local_url()


def _sql(statement, params=()):
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(statement, params)
        return db.fetch_all(cur) if cur.description else []


def _db_ready():
    """The local database is reachable AND has the Phase 6 columns (migration add_human_action_audit)."""
    if not LOCAL_URL:
        return False
    with mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL}):
        try:
            _sql('SELECT "humanActionAt","humanActionBy" FROM "AgentAuditLog" LIMIT 1')
            _sql('SELECT 1 FROM "CouncilSessionLog" LIMIT 1')
            return True
        except Exception:
            return False


DB_READY = _db_ready()


def _cleanup():
    _sql('DELETE FROM "AgentAuditLog" WHERE "userId" = %s', (TAG,))
    _sql('DELETE FROM "CouncilSessionLog" WHERE "query" LIKE %s', (TAG + "%",))


def _insert_audit(agent="cto", query=TAG + " q", request=None, human="Pending", ts="2026-01-15 10:00:00"):
    rows = _sql(
        'INSERT INTO "AgentAuditLog" ("agentId","userId","query","response","constitutionalScore","sourcesCited",'
        '"reasoningChain","authorizationRequest","humanAction","timestamp") '
        "VALUES (%s,%s,%s,'resp',90,CAST('{}' AS text[]),CAST('{}' AS text[]),CAST(%s AS jsonb),CAST(%s AS \"HumanAction\"),%s) "
        'RETURNING "id"',
        (agent, TAG, query, None if request is None else db.json_param(request), human, ts),
    )
    return rows[0]["id"]


def _insert_session(status, started, suffix):
    options = '[{"option_id":"A"},{"option_id":"B"}]' if status == "RESOLVED" else None
    return _sql(
        'INSERT INTO "CouncilSessionLog" ("id","query","participatingAgents","selectionRationale","status","startedAt",'
        '"caoSynthesizedOptions") VALUES (gen_random_uuid()::text,%s,CAST(\'{"cao","coo"}\' AS text[]),\'r\','
        'CAST(%s AS "CouncilStatus"),%s,CAST(%s AS jsonb)) RETURNING "id"',
        (TAG + suffix, status, started, options),
    )[0]["id"]



@unittest.skipUnless(DB_READY, "needs the LOCAL prisma dev database with the add_human_action_audit migration applied "
                               "(cd frontend; npx prisma migrate deploy). It never runs against Neon.")
class RealSqlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL})
        patcher.start()
        self.addCleanup(patcher.stop)
        _cleanup()
        self.addCleanup(_cleanup)

    async def audit(self, **kwargs):
        defaults = dict(agent_id=None, start=None, end=None, human_action=None, has_authorization=None,
                        search=TAG, limit=50, offset=0)
        return await admin_store.list_audit_logs(**{**defaults, **kwargs})

    async def test_filters_ordering_paging_and_the_total(self):
        a = _insert_audit(agent="cto", ts="2026-01-10 08:00:00", request=CTO_REQUEST)
        b = _insert_audit(agent="cmo", ts="2026-01-11 09:00:00")
        c = _insert_audit(agent="cto", ts="2026-01-12 10:00:00")
        rows, total = await self.audit()
        self.assertEqual((total, [r["id"] for r in rows]), (3, [c, b, a]))  # newest first
        rows, total = await self.audit(agent_id="cto")
        self.assertEqual((total, [r["id"] for r in rows]), (2, [c, a]))
        rows, total = await self.audit(limit=1, offset=1)
        self.assertEqual((total, [r["id"] for r in rows]), (3, [b]))  # total counts ALL matches, not the page
        rows, _ = await self.audit(start=datetime(2026, 1, 11), end=datetime(2026, 1, 12))
        self.assertEqual([r["id"] for r in rows], [b])  # the start is inclusive, the end exclusive
        rows, _ = await self.audit(has_authorization=True)
        self.assertEqual([r["id"] for r in rows], [a])
        rows, _ = await self.audit(has_authorization=False)
        self.assertEqual({r["id"] for r in rows}, {b, c})

    async def test_search_is_case_insensitive_and_treats_wildcards_literally(self):
        hit = _insert_audit(query=TAG + " Launch the 50%_pilot")
        _insert_audit(query=TAG + " launch the 50 pilot")
        rows, _ = await self.audit(search=TAG + " launch the 50%_PILOT")
        self.assertEqual([r["id"] for r in rows], [hit])  # % and _ matched literally, case ignored

    async def test_decision_sets_the_columns_and_leaves_the_answer_time_alone(self):
        audit_id = _insert_audit(request=CTO_REQUEST, ts="2026-01-15 10:00:00")
        outcome, row = await admin_store.decide_authorization(audit_id, "approve", "joe")
        self.assertEqual((outcome, row["humanAction"], row["humanActionBy"]), ("ok", "Approved", "joe"))
        stored = _sql('SELECT "timestamp","humanActionAt" FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))[0]
        self.assertEqual(stored["timestamp"], datetime(2026, 1, 15, 10, 0, 0))  # Clause 6: NOT rewritten
        self.assertGreater(stored["humanActionAt"], datetime(2026, 1, 15))

    async def test_a_decision_is_final_and_needs_a_real_request(self):
        audit_id = _insert_audit(request=CTO_REQUEST)
        self.assertEqual((await admin_store.decide_authorization(audit_id, "reject", "joe"))[0], "ok")
        again = await admin_store.decide_authorization(audit_id, "approve", "eve")
        self.assertEqual((again[0], again[1]["humanAction"]), ("already_decided", "Rejected"))
        stored = _sql('SELECT "humanAction","humanActionBy" FROM "AgentAuditLog" WHERE "id"=%s', (audit_id,))[0]
        self.assertEqual((stored["humanAction"], stored["humanActionBy"]), ("Rejected", "joe"))  # the first decision stands
        no_request = _insert_audit(request=None)
        self.assertEqual((await admin_store.decide_authorization(no_request, "approve", "joe"))[0], "no_request")
        pending = _sql('SELECT "humanAction" FROM "AgentAuditLog" WHERE "id"=%s', (no_request,))[0]["humanAction"]
        self.assertEqual(pending, "Pending")
        self.assertEqual((await admin_store.decide_authorization(2_000_000_000, "approve", "joe"))[0], "not_found")

    async def test_a_json_null_request_is_not_a_request(self):
        audit_id = _sql(
            'INSERT INTO "AgentAuditLog" ("agentId","userId","query","response","constitutionalScore","authorizationRequest") '
            "VALUES ('cto',%s,'q','r',90,CAST('null' AS jsonb)) RETURNING \"id\"", (TAG,))[0]["id"]
        self.assertEqual((await admin_store.decide_authorization(audit_id, "approve", "joe"))[0], "no_request")

    async def test_two_simultaneous_decisions_cannot_both_win(self):
        import asyncio

        audit_id = _insert_audit(request=CTO_REQUEST)
        results = await asyncio.gather(admin_store.decide_authorization(audit_id, "approve", "a"),
                                       admin_store.decide_authorization(audit_id, "reject", "b"))
        self.assertEqual(sorted(r[0] for r in results), ["already_decided", "ok"])

    async def test_council_sessions_list_filters_pages_and_closes_orphans(self):
        done = _insert_session("RESOLVED", "2099-01-01 00:00:00", " done")
        stale = _insert_session("RUNNING", "2000-01-01 00:00:00", " stale")  # started long ago: its process is gone
        rows, total = await admin_store.list_council_sessions(status="RESOLVED", limit=50, offset=0)
        mine = [r for r in rows if r["query"].startswith(TAG)]
        self.assertEqual([r["id"] for r in mine], [done])
        self.assertEqual((mine[0]["optionCount"], mine[0]["participatingAgents"]), (2, ["cao", "coo"]))
        self.assertGreaterEqual(total, 1)
        failed, _ = await admin_store.list_council_sessions(status="FAILED", limit=100, offset=0)
        orphan = next(r for r in failed if r["id"] == stale)  # the orphan rule closed it
        self.assertEqual(orphan["escalationReason"], "orphaned by restart")
        paged, _ = await admin_store.list_council_sessions(status=None, limit=1, offset=0)
        self.assertEqual(len(paged), 1)


if __name__ == "__main__":
    unittest.main()

