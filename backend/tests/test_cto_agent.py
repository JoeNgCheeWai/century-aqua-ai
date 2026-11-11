"""
Phase 3 tests: the AI_CTO agent (13-pillar technical expert and security sentinel).

Parts 1-6 need NO database: the registry, the prompt, the output contract shared with the CAO and
COO, the authorization workflow, POST /api/cto with a mocked model, key gating, and cross-check.
Part 7 (CtoAuditRowTests) checks the AgentAuditLog row. Like test_governance_db.py it runs ONLY
against a localhost database and is skipped otherwise.

No test here calls a real model. The two queries are the ones from the Phase 3 brief:
"Optimize our database connection pooling." (a technical question) and a code-change request
(which must produce an authorization_request, NOT executed code).
"""

import asyncio
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

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import base_constitution as bc  # noqa: E402
from app.agents import cao_agent, cao_reasoning, coo_agent, cto_agent, cto_authorization, registry  # noqa: E402
from app.agents import cross_validation as cv  # noqa: E402
from app.api import security  # noqa: E402
from app.governance import audit  # noqa: E402
from app.governance.rate_limit import limiter  # noqa: E402
from app.prompts import cao_prompt, coo_prompt, cto_prompt  # noqa: E402

from tests.test_cao_reasoning import (  # noqa: E402
    DB_READY, LOCAL_URL, _cleanup, _json, _query, _scenario, fake_client, model_text,
)

CTO_KEY = "test-internal-cto-key"
CAO_KEY = "test-internal-cao-key"
COO_KEY = "test-internal-coo-key"
TEST_AGENT = "testbot"
POOLING_QUERY = "Optimize our database connection pooling."
CHANGE_QUERY = "Change the connection pool size in the backend to 20 and apply it to production."

SHARED_FIELDS = {"response", "reasoning_chain", "scenarios", "pre_consultation_requests"}
NULL_PRE = {"coo_query": None, "cto_query": None}

PILLARS = (
    "SYSTEM ARCHITECTURE AND INFRASTRUCTURE", "CODE GENERATION AND OPTIMIZATION", "BUG DETECTION AND RESOLUTION",
    "PERFORMANCE MONITORING AND TUNING", "SECURITY AND VULNERABILITY MANAGEMENT", "DEPLOYMENT AND DEVOPS AUTOMATION",
    "DATABASE OPTIMIZATION", "API DESIGN AND INTEGRATION", "TECHNICAL DOCUMENTATION", "MAINTENANCE AUTOMATION",
    "COLLABORATION WITH HUMAN AND CLINE", "CYBERSECURITY AND SYSTEM INTEGRITY GUARDIAN",
    "USER TECHNICAL SUPPORT AND NAVIGATION GUIDE",
)

DIFF = (
    "--- a/backend/app/governance/db.py\n+++ b/backend/app/governance/db.py\n@@ -31,1 +31,1 @@\n"
    "-CONNECT_TIMEOUT_SECONDS = 10.0\n+CONNECT_TIMEOUT_SECONDS = 5.0"
)


def cto_env():
    """All three keys set, no database and no RAG keys, so no test can reach a network service."""
    patcher = mock.patch.dict(os.environ, {"INTERNAL_CTO_KEY": CTO_KEY, "INTERNAL_COO_KEY": COO_KEY, "INTERNAL_CAO_KEY": CAO_KEY})
    patcher.start()
    for name in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "OPENAI_API_KEY", "PINECONE_INDEX"):
        os.environ.pop(name, None)
    return patcher


# A technical question with no change: scenarios yes, authorization_request null.
POOLING_ANSWER = {
    "response": (
        "Bottom line: size the pool from the database's connection limit, then measure. Confidence: 65%.\n\n"
        "Today every database call opens a new connection (as described in the message). A pool of 5-10 per instance is a "
        "widely used industry range; confirm the Neon connection limit first.\n\n"
        "Forecast watch-list: connection limits on the database plan (emerging).\n\n"
        "Recommendation for human review: decide whether to adopt pooling after measuring connection-open latency."
    ),
    "reasoning_chain": [
        "Step 1 (Database Optimization): opening a connection per call adds latency and load.",
        "Step 2 (Performance Monitoring): measure connection-open time before changing anything.",
        "Step 3 (System Architecture): the pool size must fit the database connection limit across instances.",
        "Data and sources: the message only; the rest is general knowledge.",
        "Assumptions: 5-10 connections per instance is an illustrative assumption.",
        "Limitations: no access to the codebase, the database limit or live metrics; nothing has been tested.",
    ],
    "scenarios": {
        "optimistic": _scenario("Pooling cuts connection-open latency sharply.", "1-2 weeks", 30),
        "baseline": _scenario("Pooling helps modestly after tuning the pool size.", "2-3 weeks", 55),
        "pessimistic": _scenario("The pool exhausts the database connection limit and causes errors.", "1-2 weeks", 20),
    },
    "pre_consultation_requests": NULL_PRE,
    "authorization_request": None,
}

# A change request: the model must propose, never execute. The model here (wrongly) tries to mark it
# approved, executed and low-risk, and proposes a force push; the code must override all of that.
CHANGE_ANSWER = {
    "response": (
        "Bottom line: I cannot apply this myself. I have prepared an authorization request for you to review. "
        "Nothing has been executed. Confidence: 70%.\n\n"
        "Recommendation for human review: decide whether to approve the pool change after reading the diff and rollback."
    ),
    "reasoning_chain": [
        "Step 1 (Collaboration with Human and Cline): a change to production code needs a human decision.",
        "Step 2 (Deployment and DevOps): a push to main auto-deploys, so the change must not be pushed unreviewed.",
        "Step 3 (Database Optimization): confirm the database connection limit before raising the pool size.",
        "Data and sources: the message only.",
        "Assumptions: the current pool size is unknown; the diff below is illustrative.",
        "Limitations: I was not shown the file, so the diff must be checked against the repository.",
    ],
    "scenarios": None,
    "pre_consultation_requests": NULL_PRE,
    "authorization_request": {
        "action_summary": "Raise the backend connection pool size to 20.",
        "risk_level": "low",
        "impact": "Affects every database call from the backend; too large a pool could exhaust the database limit.",
        "proposed_code_diff": DIFF,
        "proposed_commands": ["python -m unittest discover -s tests -t .", "git push --force origin main"],
        "rollback_plan": "Revert the commit and redeploy; the previous value is restored.",
        "status": "approved",
        "executed": True,
        "requires_human_approval": False,
    },
}



class RegistryTests(unittest.TestCase):
    def test_cto_is_registered_and_available_with_its_own_key(self):
        spec = registry.spec("cto")
        self.assertTrue(spec.available)
        self.assertEqual(spec.key_env, "INTERNAL_CTO_KEY")
        self.assertEqual(spec.answer_path, "app.agents.cto_agent:get_cto_answer")
        self.assertIs(registry.load_answer_function("cto"), cto_agent.get_cto_answer)

    def test_the_other_entries_are_untouched_and_all_three_keys_differ(self):
        self.assertEqual(registry.spec("cao").answer_path, "app.agents.cao_agent:get_cao_answer")
        self.assertEqual(registry.spec("coo").answer_path, "app.agents.coo_agent:get_coo_answer")
        keys = {registry.spec(a).key_env for a in ("cao", "coo", "cto")}
        self.assertEqual(keys, {"INTERNAL_CAO_KEY", "INTERNAL_COO_KEY", "INTERNAL_CTO_KEY"})


class PromptTests(unittest.TestCase):
    def test_constitution_comes_first_then_the_cto_role_then_the_format(self):
        system = cto_agent.BASE_SYSTEM_PROMPT
        self.assertTrue(system.startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn(bc.EXECUTIVE_CONSTITUTION.strip(), system)
        self.assertEqual(bc.clause_numbers(), list(range(16)))  # the constitution is untouched
        self.assertLess(system.index("CLAUSE 0:"), system.index(cto_prompt.CTO_SYSTEM_PROMPT[:80]))
        self.assertLess(system.index("THE THIRTEEN PILLARS"), system.index("AUTHORIZATION WORKFLOW"))
        self.assertLess(system.index("AUTHORIZATION WORKFLOW"), system.index("ADVANCED REASONING AND PREDICTION"))

    def test_the_cto_is_not_given_the_other_executives_role_prompts(self):
        self.assertNotIn(cao_prompt.CAO_SYSTEM_PROMPT[:200], cto_agent.BASE_SYSTEM_PROMPT)
        self.assertNotIn(coo_prompt.COO_SYSTEM_PROMPT[:200], cto_agent.BASE_SYSTEM_PROMPT)
        self.assertNotIn(cto_prompt.CTO_SYSTEM_PROMPT[:200], coo_agent.BASE_SYSTEM_PROMPT)
        self.assertNotIn(cto_prompt.CTO_SYSTEM_PROMPT[:200], cao_agent.BASE_SYSTEM_PROMPT)

    def test_all_thirteen_pillars_are_present_numbered_and_in_order(self):
        text = cto_prompt.CTO_SYSTEM_PROMPT
        positions = []
        for number, name in enumerate(PILLARS, start=1):
            marker = f"\n{number}. {name}"
            self.assertIn(marker, text, name)
            positions.append(text.index(marker))
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("\n14. ", text.split("AUTHORIZATION WORKFLOW")[0])  # exactly thirteen

    def test_each_pillar_covers_the_topics_in_the_brief(self):
        text = cto_prompt.CTO_SYSTEM_PROMPT.lower()
        for needle in (
            "failure domains", "single points of failure", "code only as text", "regression test", "stack trace",
            "measure before tuning", "least privilege", "key rotation", "rollback", "prisma migrate deploy",
            "connection pooling", "index or schema change", "backward-compatible", "technical documentation",
            "dependency updates", "backups", "cline", "acceptance test",
            "threat detection", "anomaly scoring", "0-100", "flag", "auto-block", "escalate",
            "contextual help", "troubleshooting", "feedback loop",
        ):
            self.assertIn(needle, text, needle)

    def test_the_sentinel_proposes_blocks_it_never_blocks_and_scores_anomalies(self):
        text = cto_prompt.CTO_SYSTEM_PROMPT
        for needle in ("You are NOT an intrusion-detection system", "cannot block, quarantine or change anything",
                       "AUTO-BLOCK, as a PROPOSAL", "You never state that something has been blocked",
                       "0-39 normal or low", "40-69 suspicious", "70-89 likely hostile", "90-100 critical",
                       "ESCALATE TO HUMAN:"):
            self.assertIn(needle, text, needle)

    def test_the_cto_is_told_it_has_no_tools_and_no_access(self):
        text = cto_prompt.CTO_SYSTEM_PROMPT
        for needle in ("You have NO tools", "You cannot run, deploy, commit, migrate", "NO live access to the codebase",
                       "Never invent a file path", "Never ask for, repeat or store a secret"):
            self.assertIn(needle, text, needle)

    def test_the_authorization_workflow_is_mandatory_and_names_every_field(self):
        text = cto_prompt.CTO_SYSTEM_PROMPT
        self.assertIn("you MUST output a structured authorization_request", text)
        self.assertIn("INSTEAD of acting", text)
        for needle in ("action_summary", "risk_level", "impact", "proposed_code_diff", "proposed_commands",
                       "rollback_plan", "pending_human_approval", '"low", "medium", "high", "critical"'):
            self.assertIn(needle, text, needle)

    def test_the_cto_has_a_token_budget_and_a_length_budget_in_its_prompt(self):
        self.assertGreaterEqual(cto_agent.DEFAULT_CTO_MAX_TOKENS, 16384)
        self.assertIn("LENGTH BUDGET", cto_prompt.CTO_REASONING_PROMPT)
        self.assertIn("COMPLETE", cto_prompt.CTO_REASONING_PROMPT)



class SharedOutputContractTests(unittest.TestCase):
    """The CTO's first four fields must stay identical in shape to the CAO's and COO's (Council Protocol)."""

    @staticmethod
    def _example(prompt):
        return json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])

    def test_the_cto_example_is_the_cao_example_plus_exactly_authorization_request(self):
        cto_example = self._example(cto_prompt.CTO_REASONING_PROMPT)
        cao_example = self._example(cao_prompt.CAO_REASONING_PROMPT)
        coo_example = self._example(coo_prompt.COO_REASONING_PROMPT)
        self.assertEqual(coo_example, cao_example)
        self.assertEqual(set(cto_example), SHARED_FIELDS | {"authorization_request"})
        self.assertIsNone(cto_example["authorization_request"])
        for field in SHARED_FIELDS:
            self.assertEqual(cto_example[field], cao_example[field], field)

    def test_the_cto_response_model_is_the_cao_response_model_plus_one_field(self):
        from app.api.cao import CAOResponse
        from app.api.cto import CTOResponse

        self.assertTrue(issubclass(CTOResponse, CAOResponse))
        self.assertEqual(set(CTOResponse.model_fields) - set(CAOResponse.model_fields), {"authorization_request"})

    def test_the_cto_reuses_the_shared_parser_and_pre_consultation_stays_forced_to_null(self):
        self.assertIs(cto_agent.parse_cao_output, cao_reasoning.parse_cao_output)
        sneaky = dict(POOLING_ANSWER, pre_consultation_requests={"coo_query": "Is it affordable?", "cto_query": "x"})
        self.assertEqual(cao_reasoning.parse_cao_output(model_text(sneaky)).pre_consultation_requests, NULL_PRE)

    def test_the_shared_parser_ignores_the_extra_authorization_field(self):
        parsed = cao_reasoning.parse_cao_output(model_text(CHANGE_ANSWER))
        self.assertTrue(parsed.structured)
        self.assertEqual(parsed.findings, [])


class AuthorizationNormaliserTests(unittest.TestCase):
    """The safety rules live in CODE (cto_authorization.py), not in the prompt, so they hold whatever the model writes."""

    def norm(self, **overrides):
        raw = {**CHANGE_ANSWER["authorization_request"], "proposed_commands": ["python -m unittest"], **overrides}
        return cto_authorization.normalize_authorization_request(raw)

    def test_status_is_forced_to_pending_whatever_the_model_claims(self):
        request, _ = self.norm(status="approved", executed=True, requires_human_approval=False)
        self.assertEqual(request["status"], "pending_human_approval")
        self.assertIs(request["executed"], False)
        self.assertIs(request["requires_human_approval"], True)

    def test_a_missing_or_unknown_risk_level_fails_safe_to_high_never_low(self):
        for given in (None, "", "tiny", "LOW!", 3, ["low"]):
            request, findings = self.norm(risk_level=given)
            self.assertEqual(request["risk_level"], "high", given)
            self.assertTrue(any("risk_level" in f for f in findings), given)

    def test_a_valid_risk_level_is_kept_case_insensitively(self):
        self.assertEqual(self.norm(risk_level=" Medium ")[0]["risk_level"], "medium")

    def test_a_request_with_nothing_to_review_is_dropped_not_invented(self):
        for raw in ({"action_summary": "x"}, {"action_summary": "x", "proposed_commands": [], "proposed_code_diff": ""},
                    {"proposed_commands": ["ls"]}, {}, "text", 5, []):
            request, findings = cto_authorization.normalize_authorization_request(raw)
            self.assertIsNone(request, raw)
            self.assertTrue(findings, raw)
        self.assertEqual(cto_authorization.normalize_authorization_request(None), (None, []))

    def test_a_missing_impact_gets_a_placeholder_that_says_it_is_unknown(self):
        request, findings = self.norm(impact="")
        self.assertIn("unknown", request["impact"])
        self.assertTrue(any("impact" in f for f in findings))


    def test_the_risk_floor_raises_a_dangerously_underrated_request(self):
        cases = {
            'psql -c \'DROP TABLE "AgentAuditLog";\'': "critical",
            'TRUNCATE TABLE "AgentAuditLog"': "critical",
            'DELETE FROM "AgentAuditLog";': "critical",
            "npx prisma migrate reset": "critical",
            "git push --force origin main": "critical",
            "git reset --hard HEAD~3": "critical",
            "npx prisma migrate deploy": "high",
            "git push origin main": "high",
            "pip install requests": "high",
        }
        for command, floor in cases.items():
            request, findings = self.norm(risk_level="low", proposed_commands=[command])
            self.assertEqual(request["risk_level"], floor, command)
            self.assertTrue(any("raised from low" in f for f in findings), command)

    def test_the_floor_never_lowers_a_higher_honest_label(self):
        request, _ = self.norm(risk_level="critical", proposed_code_diff=None, proposed_commands=["git status"])
        self.assertEqual(request["risk_level"], "critical")

    def test_harmless_commands_do_not_inflate_the_risk(self):
        # No diff here: any diff floors at medium by design (files will change), so it would mask this check.
        request, findings = self.norm(risk_level="low", proposed_code_diff=None, proposed_commands=["git status", "python -m unittest"])
        self.assertEqual(request["risk_level"], "low")
        self.assertFalse(any("raised" in f for f in findings))

    def test_a_diff_alone_floors_at_medium(self):
        request, _ = self.norm(risk_level="low", proposed_commands=[], proposed_code_diff=DIFF)
        self.assertEqual(request["risk_level"], "medium")
        self.assertEqual(request["proposed_code_diff"], DIFF)

    def test_a_delete_with_a_where_clause_is_high_not_critical(self):
        request, _ = self.norm(risk_level="low", proposed_commands=['DELETE FROM "AgentAuditLog" WHERE id = 5;'])
        self.assertEqual(request["risk_level"], "high")

    def test_commands_given_as_one_string_are_split_and_everything_is_capped(self):
        request, _ = self.norm(proposed_commands="git status\npython -m unittest\n\n")
        self.assertEqual(request["proposed_commands"], ["git status", "python -m unittest"])
        big, _ = self.norm(proposed_commands=[f"echo {i}" for i in range(100)], proposed_code_diff="x" * 50000)
        self.assertEqual(len(big["proposed_commands"]), cto_authorization.MAX_COMMANDS)
        self.assertLessEqual(len(big["proposed_code_diff"]), cto_authorization.MAX_DIFF_CHARS)

    def test_the_audit_summary_states_nothing_was_executed_and_lists_the_commands(self):
        request, _ = self.norm(risk_level="high")
        summary = cto_authorization.audit_summary(request)
        self.assertIn("pending human approval", summary)
        self.assertIn("nothing has been executed", summary)
        self.assertIn("Risk level: high", summary)
        self.assertIn("python -m unittest", summary)
        self.assertEqual(cto_authorization.audit_summary(None), "")

    def test_it_never_raises_on_garbage(self):
        for raw in (None, "", 0, [], [1], {"action_summary": 5, "proposed_commands": 5}, {"action_summary": "x", "proposed_commands": [None, {}, 3]}):
            cto_authorization.normalize_authorization_request(raw)



class CtoEndpointTests(unittest.TestCase):
    """POST /api/cto with a mocked model and no database (audit_id is therefore null)."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(cto_env().stop)
        self.headers = {"X-Internal-CTO-Key": CTO_KEY, "X-User-Id": "user_staff_1"}

    def ask(self, reply_text, message=POOLING_QUERY):
        calls = []
        with mock.patch.object(cto_agent, "_client", fake_client(reply_text, calls)):
            response = self.client.post("/api/cto", headers=self.headers, json={"message": message})
        return response, calls

    def test_technical_query_returns_the_same_shape_as_the_cao_and_coo_plus_authorization_request(self):
        response, calls = self.ask(model_text(POOLING_ANSWER))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("response", "reply", "reasoning_chain", "scenarios", "pre_consultation_requests",
                    "authorization_request", "constitutional_score", "needs_review", "audit_id"):
            self.assertIn(key, body)
        self.assertEqual(body["reply"], body["response"])
        self.assertTrue(calls[0]["system"].startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn("THE THIRTEEN PILLARS", calls[0]["system"])
        self.assertEqual(calls[0]["messages"][-1], {"role": "user", "content": POOLING_QUERY})
        self.assertEqual(calls[0]["model"], cto_agent.CTO_MODEL)

    def test_a_technical_question_has_no_authorization_request(self):
        body = self.ask(model_text(POOLING_ANSWER))[0].json()
        self.assertIsNone(body["authorization_request"])
        self.assertNotIn("AUTHORIZATION REQUEST", body["response"])

    def test_reasoning_chain_has_three_to_five_pillar_steps_plus_the_three_closing_entries(self):
        chain = self.ask(model_text(POOLING_ANSWER))[0].json()["reasoning_chain"]
        steps = [s for s in chain if s.startswith("Step ")]
        self.assertTrue(3 <= len(steps) <= 5, chain)
        self.assertEqual([s.split(":")[0] for s in chain[len(steps):]], ["Data and sources", "Assumptions", "Limitations"])
        self.assertTrue(any("Database Optimization" in s for s in steps))

    def test_three_distinct_scenarios_with_every_field_and_a_confidence(self):
        scenarios = self.ask(model_text(POOLING_ANSWER))[0].json()["scenarios"]
        self.assertEqual(set(scenarios), {"optimistic", "baseline", "pessimistic"})
        for name, scenario in scenarios.items():
            self.assertEqual(
                set(scenario),
                {"description", "timeline", "resource_requirements", "confidence_percentage", "key_assumptions", "risks"},
                name,
            )
        confidences = {name: s["confidence_percentage"] for name, s in scenarios.items()}
        self.assertEqual(confidences, {"optimistic": 30, "baseline": 55, "pessimistic": 20})
        self.assertEqual(len({s["description"] for s in scenarios.values()}), 3)

    def test_pre_consultation_requests_structure_is_present_and_null(self):
        self.assertEqual(self.ask(model_text(POOLING_ANSWER))[0].json()["pre_consultation_requests"], NULL_PRE)

    def test_score_is_in_range_and_a_good_answer_does_not_need_review(self):
        body = self.ask(model_text(POOLING_ANSWER))[0].json()
        self.assertTrue(1 <= body["constitutional_score"] <= 100)
        self.assertFalse(body["needs_review"])

    def test_a_model_that_ignores_the_format_still_gets_a_valid_framed_response(self):
        response, _ = self.ask("Set the pool size to 10.")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("Set the pool size to 10.", body["response"])
        self.assertIn("Recommendation for human review:", body["response"])
        self.assertEqual((body["reasoning_chain"], body["scenarios"], body["authorization_request"]), ([], None, None))
        self.assertTrue(body["needs_review"])

    def test_a_model_failure_is_a_502_that_does_not_leak_the_message(self):
        async def boom(**kwargs):
            raise RuntimeError("upstream exploded with SECRET-DETAIL")

        with mock.patch.object(cto_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=boom))):
            response = self.client.post("/api/cto", headers=self.headers, json={"message": POOLING_QUERY})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("SECRET-DETAIL", response.text)
        self.assertNotIn(POOLING_QUERY, response.text)


    # ---- the Authorization Workflow, end to end over HTTP ----

    def test_a_code_change_request_returns_an_authorization_request_not_executed_code(self):
        response, _ = self.ask(model_text(CHANGE_ANSWER), message=CHANGE_QUERY)
        self.assertEqual(response.status_code, 200, response.text)
        auth = response.json()["authorization_request"]
        self.assertIsNotNone(auth)
        for key in ("action_summary", "risk_level", "impact", "proposed_code_diff", "proposed_commands", "rollback_plan"):
            self.assertIn(key, auth)
        self.assertEqual(auth["action_summary"], "Raise the backend connection pool size to 20.")
        self.assertEqual(auth["proposed_code_diff"], DIFF)  # the change is TEXT for review
        self.assertIn("python -m unittest discover -s tests -t .", auth["proposed_commands"])

    def test_the_request_can_never_claim_to_be_approved_or_executed(self):
        # The mock model claimed status "approved", executed true and no approval needed. The code overrides all three.
        auth = self.ask(model_text(CHANGE_ANSWER), message=CHANGE_QUERY)[0].json()["authorization_request"]
        self.assertEqual(auth["status"], "pending_human_approval")
        self.assertIs(auth["executed"], False)
        self.assertIs(auth["requires_human_approval"], True)

    def test_a_dangerous_command_raises_the_risk_even_though_the_model_said_low(self):
        auth = self.ask(model_text(CHANGE_ANSWER), message=CHANGE_QUERY)[0].json()["authorization_request"]
        self.assertEqual(auth["risk_level"], "critical")  # the mock proposed "git push --force origin main" as "low"

    def test_the_response_text_says_nothing_was_executed_and_records_the_request_for_the_audit_log(self):
        body = self.ask(model_text(CHANGE_ANSWER), message=CHANGE_QUERY)[0].json()
        self.assertIn("AUTHORIZATION REQUEST (pending human approval; nothing has been executed)", body["response"])
        self.assertIn("Risk level: critical", body["response"])
        self.assertIn("Recommendation for human review:", body["response"])

    def test_no_executed_code_leaks_out_as_a_result(self):
        body = self.ask(model_text(CHANGE_ANSWER), message=CHANGE_QUERY)[0].json()
        text = body["response"].lower()
        for claim in ("i have applied", "i have deployed", "i have pushed", "i have updated", "has been applied", "has been deployed"):
            self.assertNotIn(claim, text, claim)
        self.assertIsNone(body["scenarios"])  # a plain change request is not a strategic question

    def test_a_cut_off_change_request_is_never_recovered_or_invented(self):
        full = model_text(CHANGE_ANSWER)
        cut = full[: full.index('"proposed_commands"')]  # cut mid-request, as a token limit would
        reply = asyncio.run(_answer_with_stop(cut, "max_tokens"))
        self.assertIn("length limit", reply)
        self.assertIn("was NOT recovered", reply)
        self.assertNotIn("git push", reply)  # no half-built command surfaces
        result = asyncio.run(_result_with_stop(cut, "max_tokens"))
        self.assertIsNone(result.authorization_request)

    def test_a_model_authorization_request_with_nothing_to_review_is_dropped(self):
        empty = dict(CHANGE_ANSWER, authorization_request={"action_summary": "Do something", "risk_level": "low"})
        body = self.ask(model_text(empty), message=CHANGE_QUERY)[0].json()
        self.assertIsNone(body["authorization_request"])
        self.assertNotIn("AUTHORIZATION REQUEST", body["response"])

    def test_the_api_contract_refuses_a_request_that_claims_to_be_approved(self):
        from pydantic import ValidationError

        from app.api.cto import AuthorizationRequest

        base = dict(action_summary="x", risk_level="low", impact="y")
        for bad in ({"status": "approved"}, {"executed": True}, {"requires_human_approval": False}, {"risk_level": "tiny"}):
            with self.assertRaises(ValidationError, msg=bad):
                AuthorizationRequest(**{**base, **bad})
        self.assertEqual(AuthorizationRequest(**base).status, "pending_human_approval")

    def test_a_threat_assessment_with_no_action_has_a_null_authorization_request(self):
        finding = dict(POOLING_ANSWER, authorization_request=None)
        finding["response"] = finding["response"].replace("size the pool", "flag the repeated login failures (anomaly score 55)")
        body = self.ask(model_text(finding), message="Here are 40 failed logins from one IP. Assess.")[0].json()
        self.assertIsNone(body["authorization_request"])



async def _answer_with_stop(text, stop_reason):
    return (await _result_with_stop(text, stop_reason)).reply


async def _result_with_stop(text, stop_reason):
    async def create(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)

    with mock.patch.object(cto_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))):
        return await cto_agent.get_cto_result("q", use_rag=False, audit=False)


class CtoKeyGatingTests(unittest.TestCase):
    """/api/cto is gated by INTERNAL_CTO_KEY only, and never calls the model before the key is checked."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(cto_env().stop)
        self.calls = []
        patcher = mock.patch.object(cto_agent, "_client", fake_client(model_text(POOLING_ANSWER), self.calls))
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, headers=None, path="/api/cto"):
        return self.client.post(path, json={"message": POOLING_QUERY}, headers=headers or {})

    def test_no_key_and_a_wrong_key_are_401_and_never_reach_the_model(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.post({"X-Internal-CTO-Key": "wrong"}).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_the_cao_and_coo_keys_do_not_open_the_cto(self):
        for key in (CAO_KEY, COO_KEY):
            for header in ("X-Internal-CTO-Key", "X-Internal-CAO-Key", "X-Internal-COO-Key"):
                self.assertEqual(self.post({header: key}).status_code, 401, (header, key))
        self.assertEqual(self.calls, [])

    def test_the_cto_key_does_not_open_any_other_endpoint(self):
        cross = {"agent_a_id": "cao", "agent_b_id": "cto", "query": "q", "context": "c"}
        for header in ("X-Internal-CTO-Key", "X-Internal-CAO-Key", "X-Internal-COO-Key"):
            for path in ("/api/cao", "/api/coo"):
                self.assertEqual(self.post({header: CTO_KEY}, path).status_code, 401, (header, path))
            r = self.client.post("/api/agents/cross-check", json=cross, headers={header: CTO_KEY})
            self.assertEqual(r.status_code, 401, header)
        r = self.client.post("/api/admin/agents/cto/reset", headers={"X-Internal-CTO-Key": CTO_KEY, "X-Admin-Id": "a"})
        self.assertEqual(r.status_code, 401)  # the kill switch stays on the CAO key
        self.assertEqual(self.calls, [])

    def test_the_correct_key_opens_the_cto(self):
        self.assertEqual(self.post({"X-Internal-CTO-Key": CTO_KEY}).status_code, 200)

    def test_the_endpoint_is_closed_503_when_the_cto_key_is_not_configured(self):
        os.environ.pop("INTERNAL_CTO_KEY")
        response = self.post({"X-Internal-CTO-Key": "anything"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(CTO_KEY, response.text)
        self.assertEqual(self.calls, [])

    def test_an_empty_configured_key_does_not_accept_an_empty_header(self):
        os.environ["INTERNAL_CTO_KEY"] = ""
        self.assertEqual(self.post({"X-Internal-CTO-Key": ""}).status_code, 503)

    def test_the_cao_and_coo_gates_are_unchanged(self):
        security.require_internal_key(CAO_KEY)  # the original one-argument call still works
        security.require_coo_key(COO_KEY)
        with self.assertRaises(Exception) as caught:
            security.require_internal_key("wrong")
        self.assertIn("X-Internal-CAO-Key", caught.exception.detail)
        with self.assertRaises(Exception) as caught:
            security.require_coo_key("wrong")
        self.assertIn("X-Internal-COO-Key", caught.exception.detail)
        with self.assertRaises(Exception) as caught:
            security.require_cto_key("wrong")
        self.assertIn("X-Internal-CTO-Key", caught.exception.detail)



class CrossCheckWithTheRealCtoTests(unittest.IsolatedAsyncioTestCase):
    """The real CTO can review the CAO and the COO through the existing cross-check (technical feasibility)."""

    VERDICT = '{"valid": false, "reasoning": "Offshore tech is unreviewed and has no rollback.", "confidence": 0.75, "constitutional_alignment": "partially_aligned"}'

    def setUp(self):
        self.addCleanup(cto_env().stop)
        limiter.reset()

    async def test_cao_to_cto_cross_check_uses_the_real_cto_function(self):
        calls = []
        with mock.patch.object(cto_agent, "_client", fake_client(self.VERDICT, calls)):
            result = await cv.cross_check("cao", "cto", "Expand offshore?", "CAO recommendation text")
        self.assertFalse(result.valid)
        self.assertEqual((result.constitutional_alignment, result.confidence), ("partially_aligned", 0.75))
        self.assertTrue(calls[0]["system"].startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn("CYBERSECURITY AND SYSTEM INTEGRITY GUARDIAN", calls[0]["system"])
        self.assertNotIn("KNOWLEDGE BASE EXCERPTS", calls[0]["system"])  # RAG is off for a review
        self.assertIn("RECOMMENDATION UNDER REVIEW", calls[0]["messages"][-1]["content"])

    async def test_coo_to_cto_and_cto_to_coo_both_work(self):
        with mock.patch.object(cto_agent, "_client", fake_client(self.VERDICT)):
            self.assertFalse((await cv.cross_check("coo", "cto", "q", "ctx")).valid)
        limiter.reset()
        with mock.patch.object(coo_agent, "_client", fake_client(self.VERDICT)):
            self.assertFalse((await cv.cross_check("cto", "coo", "q", "ctx")).valid)

    async def test_a_cross_check_review_is_not_written_to_the_audit_log_as_an_answer(self):
        with mock.patch.object(cto_agent, "_client", fake_client(self.VERDICT)), mock.patch.object(
            audit, "record_response", mock.AsyncMock()
        ) as record:
            await cv.cross_check("cao", "cto", "q", "ctx")
        record.assert_not_awaited()

    async def test_a_verdict_never_creates_an_authorization_request(self):
        result = await _result_with_stop(self.VERDICT, "end_turn")
        self.assertIsNone(result.authorization_request)
        self.assertIn(self.VERDICT, result.reply)


class RecordsToTheAuditLogAsCtoTests(unittest.IsolatedAsyncioTestCase):
    """No database: prove the call is audit.record_response('cto', ...) with every field."""

    def setUp(self):
        self.addCleanup(cto_env().stop)

    async def test_get_cto_result_records_agent_cto_with_the_structured_fields(self):
        record = mock.AsyncMock(return_value=(7, mock.Mock()))
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(POOLING_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await cto_agent.get_cto_result(POOLING_QUERY, use_rag=False, user_id="user_staff_1")
        self.assertEqual(result.audit_id, 7)
        args, kwargs = record.call_args
        self.assertEqual(args[:3], ("cto", "user_staff_1", POOLING_QUERY))
        self.assertEqual(kwargs["reasoning_chain"], POOLING_ANSWER["reasoning_chain"])
        self.assertEqual(kwargs["scenarios"], POOLING_ANSWER["scenarios"])
        self.assertEqual(kwargs["pre_consultation_requests"], NULL_PRE)
        self.assertEqual(kwargs["score"].score, result.constitutional_score)
        self.assertIsNone(kwargs["authorization_request"])  # no change requested: nothing to store

    async def test_a_change_request_passes_the_full_validated_request_to_the_audit_log(self):
        record = mock.AsyncMock(return_value=(9, mock.Mock()))
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(CHANGE_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await cto_agent.get_cto_result(CHANGE_QUERY, use_rag=False)
        stored = record.call_args.kwargs["authorization_request"]
        self.assertEqual(stored, result.authorization_request)  # the same object the API returns
        self.assertEqual(stored["status"], "pending_human_approval")  # the HARDENED version, not the model's
        self.assertIs(stored["executed"], False)
        self.assertEqual(stored["risk_level"], "critical")  # raised by the code floor from the model's "low"
        self.assertEqual(stored["proposed_code_diff"], DIFF)  # the full diff, not just a summary
        self.assertIn("git push --force origin main", stored["proposed_commands"])
        self.assertEqual(stored["rollback_plan"], "Revert the commit and redeploy; the previous value is restored.")

    async def test_a_request_the_code_dropped_is_not_stored(self):
        empty = dict(CHANGE_ANSWER, authorization_request={"action_summary": "Do something", "risk_level": "low"})
        record = mock.AsyncMock(return_value=(10, mock.Mock()))
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(empty))), mock.patch.object(
            audit, "record_response", record
        ):
            await cto_agent.get_cto_result(CHANGE_QUERY, use_rag=False)
        self.assertIsNone(record.call_args.kwargs["authorization_request"])

    async def test_a_cut_off_change_request_stores_no_request(self):
        full = model_text(CHANGE_ANSWER)
        cut = full[: full.index('"proposed_commands"')]
        record = mock.AsyncMock(return_value=(11, mock.Mock()))

        async def create(**kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=cut)], stop_reason="max_tokens")

        with mock.patch.object(cto_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))), mock.patch.object(
            audit, "record_response", record
        ):
            await cto_agent.get_cto_result(CHANGE_QUERY, use_rag=False)
        self.assertIsNone(record.call_args.kwargs["authorization_request"])  # never a half-built request

    async def test_the_stored_text_of_a_change_request_carries_the_authorization_summary(self):
        record = mock.AsyncMock(return_value=(8, mock.Mock()))
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(CHANGE_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            await cto_agent.get_cto_result(CHANGE_QUERY, use_rag=False)
        stored = record.call_args[0][3]
        self.assertIn("AUTHORIZATION REQUEST (pending human approval; nothing has been executed)", stored)
        self.assertIn("Risk level: critical", stored)
        self.assertIn("Raise the backend connection pool size to 20.", stored)

    async def test_audit_false_records_nothing(self):
        record = mock.AsyncMock()
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(POOLING_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await cto_agent.get_cto_result("q", use_rag=False, audit=False)
        record.assert_not_awaited()
        self.assertIsNone(result.audit_id)

    async def test_a_database_failure_never_blocks_the_ctos_answer(self):
        bad_db = {"GOVERNANCE_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x?sslmode=disable"}
        with mock.patch.dict(os.environ, bad_db), mock.patch.object(
            cto_agent, "_client", fake_client(model_text(CHANGE_ANSWER))
        ):
            with self.assertLogs("app.governance.audit", level="ERROR") as logs:
                result = await cto_agent.get_cto_result("q", use_rag=False)
        self.assertIn("Recommendation for human review", result.reply)
        self.assertIsNotNone(result.authorization_request)  # the request still reaches the human
        self.assertIsNone(result.audit_id)
        self.assertIn("GovernanceDBError", logs.output[0])

    async def test_get_cto_answer_returns_text_for_cross_validation(self):
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(POOLING_ANSWER))):
            reply = await cto_agent.get_cto_answer("q", use_rag=False, audit=False)
        self.assertIsInstance(reply, str)
        self.assertIn("size the pool", reply)



# ---------------------------------------------------------------------------------------------
# Part 7: the real AgentAuditLog row. Local database only (same safety rules as test_governance_db.py).
# Rows are written under the dedicated agent id 'testbot' and deleted afterwards, so the local 'cto'
# history is never touched. The agent id 'cto' itself is proven by RecordsToTheAuditLogAsCtoTests above.
# ---------------------------------------------------------------------------------------------
@unittest.skipUnless(DB_READY, "needs the local prisma dev database with the Phase 1 migration applied")
class CtoAuditRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL, "INTERNAL_CTO_KEY": CTO_KEY}),
            mock.patch.object(cto_agent, "AGENT_ID", TEST_AGENT),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        _cleanup()
        self.addCleanup(_cleanup)
        # No test here may reach OpenAI or Pinecone (importing app.main loads the real keys from .env).
        from app.rag import retrieval

        rag = mock.patch.object(
            retrieval,
            "retrieve_context_with_sources",
            mock.AsyncMock(return_value=retrieval.RetrievalResult(context="", sources=[])),
        )
        rag.start()
        self.addCleanup(rag.stop)

    async def test_the_pooling_answer_is_logged_with_every_field_populated(self):
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(POOLING_ANSWER))):
            result = await cto_agent.get_cto_result(POOLING_QUERY, user_id="user_staff_1")
        self.assertIsInstance(result.audit_id, int)

        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertEqual((row["agentId"], row["userId"], row["query"]), (TEST_AGENT, "user_staff_1", POOLING_QUERY))
        self.assertIn("Recommendation for human review:", row["response"])
        self.assertEqual(row["constitutionalScore"], result.constitutional_score)
        self.assertEqual(row["humanAction"], "Pending")
        self.assertIsNotNone(row["timestamp"])
        self.assertEqual(list(row["reasoningChain"]), POOLING_ANSWER["reasoning_chain"])
        self.assertEqual(_json(row["scenarios"]), POOLING_ANSWER["scenarios"])
        self.assertEqual(_json(row["preConsultationRequests"]), NULL_PRE)
        self.assertTrue(row["sourcesCited"])

    async def test_a_change_request_is_recorded_as_pending_with_its_risk_and_nothing_executed(self):
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(CHANGE_ANSWER))):
            result = await cto_agent.get_cto_result(CHANGE_QUERY, user_id="user_staff_1")
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertIn("AUTHORIZATION REQUEST (pending human approval; nothing has been executed)", row["response"])
        self.assertIn("Risk level: critical", row["response"])
        self.assertIn("Raise the backend connection pool size to 20.", row["response"])
        self.assertEqual(row["humanAction"], "Pending")  # a person still has to act on it
        self.assertIsNone(row["scenarios"])  # a plain change request is not strategic: SQL NULL
        self.assertEqual(result.authorization_request["status"], "pending_human_approval")

    async def test_a_change_request_populates_the_authorization_request_column_with_the_full_request(self):
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(CHANGE_ANSWER))):
            result = await cto_agent.get_cto_result(CHANGE_QUERY, user_id="user_staff_1")
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        stored = _json(row["authorizationRequest"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored, result.authorization_request)  # exactly what the API returns
        self.assertEqual(
            set(stored),
            {"action_summary", "executive_summary", "risk_level", "impact", "proposed_code_diff", "proposed_commands",
             "rollback_plan", "status", "requires_human_approval", "executed"},
        )
        # The HARDENED request is what is stored, not what the model wrote (it claimed approved/executed/low).
        self.assertEqual(stored["status"], "pending_human_approval")
        self.assertIs(stored["executed"], False)
        self.assertIs(stored["requires_human_approval"], True)
        self.assertEqual(stored["risk_level"], "critical")
        # The FULL diff and commands survive the round trip (newlines, quotes and dashes included).
        self.assertEqual(stored["proposed_code_diff"], DIFF)
        self.assertEqual(stored["proposed_commands"], ["python -m unittest discover -s tests -t .", "git push --force origin main"])
        self.assertEqual(stored["action_summary"], "Raise the backend connection pool size to 20.")
        self.assertEqual(row["humanAction"], "Pending")

    async def test_the_stored_request_matches_the_api_response_end_to_end(self):
        from app.main import app

        headers = {"X-Internal-CTO-Key": CTO_KEY, "X-User-Id": "user_staff_1"}
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(CHANGE_ANSWER))), mock.patch(
            "app.api.cto.audit.current_epoch", mock.AsyncMock(return_value=0)
        ):
            response = TestClient(app).post("/api/cto", headers=headers, json={"message": CHANGE_QUERY})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        row = _query('SELECT "authorizationRequest" FROM "AgentAuditLog" WHERE id = %s', (body["audit_id"],))[0]
        self.assertEqual(_json(row["authorizationRequest"]), body["authorization_request"])

    async def test_the_column_is_sql_null_when_the_cto_proposes_no_change(self):
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(POOLING_ANSWER))):
            result = await cto_agent.get_cto_result(POOLING_QUERY)
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertIsNone(row["authorizationRequest"])  # SQL NULL, not the JSON value null
        self.assertIsNone(result.authorization_request)
        check = _query('SELECT "authorizationRequest" IS NULL AS is_sql_null FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))
        self.assertTrue(check[0]["is_sql_null"])

    async def test_the_column_is_null_for_a_plain_text_answer_and_for_a_dropped_request(self):
        dropped = model_text(dict(CHANGE_ANSWER, authorization_request={"action_summary": "x"}))
        for reply in ("Set the pool size to 10.", dropped):
            with mock.patch.object(cto_agent, "_client", fake_client(reply)):
                result = await cto_agent.get_cto_result(CHANGE_QUERY)
            row = _query('SELECT "authorizationRequest" FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
            self.assertIsNone(row["authorizationRequest"], reply[:40])

    async def test_a_cut_off_change_request_leaves_the_column_null(self):
        full = model_text(CHANGE_ANSWER)
        cut = full[: full.index('"proposed_commands"')]

        async def create(**kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=cut)], stop_reason="max_tokens")

        with mock.patch.object(cto_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))):
            result = await cto_agent.get_cto_result(CHANGE_QUERY)
        row = _query('SELECT "authorizationRequest" FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertIsNone(row["authorizationRequest"])

    async def test_cao_and_coo_style_rows_leave_the_column_null(self):
        # record_response without the new argument is exactly how the CAO and COO call it.
        audit_id, _ = await audit.record_response(TEST_AGENT, "u", "q", "Reasoning: x. Confidence: 80%.", [])
        row = _query('SELECT "authorizationRequest" FROM "AgentAuditLog" WHERE id = %s', (audit_id,))[0]
        self.assertIsNone(row["authorizationRequest"])

    async def test_a_row_without_a_request_does_not_name_the_new_column_so_a_not_yet_migrated_database_still_works(self):
        # Deploy-order safety: the backend may go live before `prisma migrate deploy` has run on Neon. Only a CTO
        # change request may name the new column, so CAO, COO and non-change CTO rows keep being audit-logged.
        # This records the SQL that is sent, using the real local database (which does have the column).
        import contextlib

        executed = []
        real_transaction = audit.db.transaction

        @contextlib.contextmanager
        def spying_transaction(*args, **kwargs):
            with real_transaction(*args, **kwargs) as conn:
                real_cursor = conn.cursor

                def cursor():
                    cur = real_cursor()
                    real_execute = cur.execute

                    def execute(sql, params=()):
                        executed.append(sql)
                        return real_execute(sql, params)

                    cur.execute = execute
                    return cur

                conn.cursor = cursor
                yield conn

        with mock.patch.object(audit.db, "transaction", spying_transaction):
            await audit.record_response(TEST_AGENT, "u", "q", "Reasoning: x. Confidence: 80%.", [])
            await audit.record_response(
                TEST_AGENT, "u", "q", "Reasoning: x. Confidence: 80%.", [],
                authorization_request={"action_summary": "x", "risk_level": "high", "status": "pending_human_approval"},
            )
        inserts = [s for s in executed if 'INSERT INTO "AgentAuditLog"' in s]
        self.assertEqual(len(inserts), 2)
        self.assertNotIn("authorizationRequest", inserts[0])
        self.assertIn('"authorizationRequest"', inserts[1])

    async def test_the_agent_state_buffer_and_violation_count_work_for_the_cto_too(self):
        with mock.patch.object(cto_agent, "_client", fake_client("Set the pool size to 10.")):
            await cto_agent.get_cto_result(POOLING_QUERY, user_id="user_staff_1")
        state = audit.snapshot_sync(TEST_AGENT)
        self.assertEqual(len(state["conversationHistory"]), 1)
        self.assertEqual(state["conversationHistory"][0]["query"], POOLING_QUERY)
        self.assertEqual(state["constitutionalViolations"], 1)  # the plain-text answer scored below 70

    async def test_endpoint_end_to_end_logs_the_row_it_returns(self):
        from app.main import app

        client = TestClient(app)
        headers = {"X-Internal-CTO-Key": CTO_KEY, "X-User-Id": "user_staff_1"}
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(POOLING_ANSWER))), mock.patch(
            "app.api.cto.audit.current_epoch", mock.AsyncMock(return_value=0)
        ):
            response = client.post("/api/cto", headers=headers, json={"message": POOLING_QUERY})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsInstance(body["audit_id"], int)
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (body["audit_id"],))[0]
        self.assertEqual(list(row["reasoningChain"]), body["reasoning_chain"])
        self.assertEqual(_json(row["scenarios"]), body["scenarios"])
        self.assertEqual(_json(row["preConsultationRequests"]), body["pre_consultation_requests"])
        self.assertEqual(row["constitutionalScore"], body["constitutional_score"])

    async def test_the_cto_honours_the_kill_switch_epoch(self):
        from app.main import app

        client = TestClient(app)
        headers = {"X-Internal-CTO-Key": CTO_KEY, "X-User-Id": "user_staff_1"}
        history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier answer"}]
        seen = []

        async def create(**kwargs):
            seen.append(kwargs["messages"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=model_text(POOLING_ANSWER))], stop_reason="end_turn")

        with mock.patch.object(cto_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))), mock.patch(
            "app.api.cto.audit.current_epoch", mock.AsyncMock(return_value=3)
        ):
            fresh = client.post("/api/cto", headers=headers, json={"message": "now", "history": history, "reset_epoch": 3})
            stale = client.post("/api/cto", headers=headers, json={"message": "now", "history": history, "reset_epoch": 2})
        self.assertEqual((fresh.json()["history_discarded"], stale.json()["history_discarded"]), (False, True))
        self.assertEqual(len(seen[0]), 3)
        self.assertEqual(seen[1], [{"role": "user", "content": "now"}])  # the cancelled history never reached the model


if __name__ == "__main__":
    unittest.main()

