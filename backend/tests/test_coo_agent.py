"""
Phase 2 tests: the AI_COO agent (10-pillar operational brain).

Parts 1-5 need NO database: the registry, the prompt, the output contract shared with the
CAO, POST /api/coo with a mocked model, and the COO-to-CAO cross-check.
Part 6 (CooAuditRowTests) checks the AgentAuditLog row. Like test_governance_db.py it runs
ONLY against a localhost database and is skipped otherwise.

No test here calls a real model. The strategic query is the one from the Phase 2 brief:
"The CAO recommends expanding to offshore salmon farming. Evaluate this."
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
from app.agents import cao_agent, coo_agent, registry  # noqa: E402
from app.agents import cross_validation as cv  # noqa: E402
from app.api import security  # noqa: E402
from app.governance import audit, db  # noqa: E402
from app.governance.rate_limit import limiter  # noqa: E402
from app.prompts import cao_prompt, coo_prompt  # noqa: E402

from tests.test_cao_reasoning import (  # noqa: E402
    DB_READY, LOCAL_URL, STRATEGIC as CAO_ANSWER, _cleanup, _json, _query, _scenario, fake_client, model_text,
)

COO_KEY = "test-internal-coo-key"
CAO_KEY = "test-internal-cao-key"
QUERY = "The CAO recommends expanding to offshore salmon farming. Evaluate this."
TEST_AGENT = "testbot"

# What a well-behaved COO returns for that query: a stress test of the CAO plan, in the shared shape.
COO_ANSWER = {
    "response": (
        "Bottom line: SUPPORTED WITH CONDITIONS. Do not commit capital before a gated pilot. Confidence: 58%.\n\n"
        "ESCALATE TO HUMAN: this is a high-stakes capital decision and my overall confidence is below 60%.\n\n"
        "Stress test of the CAO plan\n"
        "- Plan restated: expand into offshore salmon farming, as the CAO recommends [1].\n"
        "- Financial viability: capital is not sized anywhere in the CAO's text, so the plan is not yet assessable.\n"
        "- Operational realism: vessel access, licensing and a cold-water learning curve are not costed.\n"
        "- CAO Optimistic scenario: FINANCIALLY RECKLESS. It assumes licences and capital arrive before any pilot evidence.\n"
        "- Where we differ: the CAO assumes the first harvest in 12 months; I assume 24+ (illustrative assumption).\n"
        "- Verdict: SUPPORTED WITH CONDITIONS: a gated pilot with a measurable pass/fail test.\n\n"
        "Forecast watch-list: offshore licensing timelines (emerging), feed price pressure (established).\n\n"
        "Recommendation for human review: decide whether to fund a gated, time-boxed pilot before any capital commitment."
    ),
    "reasoning_chain": [
        "Step 1 (Strategic Stress-Testing): the CAO plan needs capital that its own text never sizes.",
        "Step 2 (Project Costing): offshore CAPEX and OPEX cannot be built without site and vessel data.",
        "Step 3 (Compliance and Risk): licensing is jurisdiction specific and slow, so the timeline is stretched.",
        "Step 4 (Logistic Planning): feed, smolt and harvest logistics offshore add cost and weather risk.",
        "Data and sources: the CAO text in the message; [1] a public-literature excerpt; the rest is general knowledge.",
        "Assumptions: harvest at 24+ months and a 20% cost contingency are illustrative assumptions.",
        "Limitations: no access to Century Aqua's books; technical feasibility has not been reviewed by anyone.",
    ],
    "scenarios": {
        "optimistic": _scenario("Pilot passes its gates and a second site is financed.", "18-30 months", 20),
        "baseline": _scenario("Pilot works but costs overrun and scale-up slips a year.", "30-42 months", 50),
        "pessimistic": _scenario("Overruns exhaust the buffer and the project is written off.", "24-48 months", 30),
    },
    "pre_consultation_requests": {"coo_query": None, "cto_query": None},
}

PILLARS = (
    "PAYMENT AND FINANCE", "COMPUTE BUDGET MANAGEMENT", "PROJECT COSTING", "LOGISTIC PLANNING",
    "COMPLIANCE AND RISK MANAGEMENT", "QUALITY CONTROL", "SUSTAINABILITY", "PERFORMANCE ANALYTICS",
    "CROSS-AGENT COLLABORATION", "STRATEGIC STRESS-TESTING",
)


def coo_env():
    """Both keys set, no database and no RAG keys, so no test can reach a network service."""
    patcher = mock.patch.dict(os.environ, {"INTERNAL_COO_KEY": COO_KEY, "INTERNAL_CAO_KEY": CAO_KEY})
    patcher.start()
    for name in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "OPENAI_API_KEY", "PINECONE_INDEX"):
        os.environ.pop(name, None)
    return patcher


class RegistryTests(unittest.TestCase):
    def test_coo_is_registered_and_available_with_its_own_key(self):
        spec = registry.spec("coo")
        self.assertTrue(spec.available)
        self.assertEqual(spec.key_env, "INTERNAL_COO_KEY")
        self.assertEqual(spec.answer_path, "app.agents.coo_agent:get_coo_answer")
        self.assertIs(registry.load_answer_function("coo"), coo_agent.get_coo_answer)

    def test_cao_entry_is_untouched_and_the_keys_are_different(self):
        self.assertEqual(registry.spec("cao").answer_path, "app.agents.cao_agent:get_cao_answer")
        self.assertNotEqual(registry.spec("cao").key_env, registry.spec("coo").key_env)

    def test_the_cto_is_not_built(self):
        self.assertFalse(registry.is_known("cto"))
        self.assertEqual(set(registry.AGENTS), {"cao", "coo"})


class PromptTests(unittest.TestCase):
    def test_constitution_comes_first_then_the_coo_role_then_the_format(self):
        system = coo_agent.BASE_SYSTEM_PROMPT
        self.assertTrue(system.startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn(bc.EXECUTIVE_CONSTITUTION.strip(), system)
        self.assertEqual(bc.clause_numbers(), list(range(16)))  # the constitution is untouched
        self.assertLess(system.index("CLAUSE 0:"), system.index(coo_prompt.COO_SYSTEM_PROMPT[:80]))
        self.assertLess(system.index("THE TEN PILLARS"), system.index("ADVANCED REASONING AND PREDICTION"))

    def test_the_coo_has_a_larger_token_budget_and_a_length_budget_in_its_prompt(self):
        # A live run with a realistic CAO plan was cut off at 8192 tokens, losing the chain and scenarios.
        self.assertGreater(coo_agent.DEFAULT_COO_MAX_TOKENS, 8192)
        self.assertIn("LENGTH BUDGET", coo_prompt.COO_REASONING_PROMPT)
        self.assertIn("COMPLETE", coo_prompt.COO_REASONING_PROMPT)

    def test_a_truncated_coo_reply_still_gives_the_human_the_response_and_says_so(self):
        full = model_text(COO_ANSWER)
        cut = full[: full.index('"reasoning_chain"')]  # cut off before the chain: what a token limit does
        reply = asyncio.run(_answer_with_stop(cut, "max_tokens"))
        self.assertIn("Stress test of the CAO plan", reply)
        self.assertIn("length limit", reply)
        self.assertNotIn('"response"', reply)

    def test_the_coo_is_not_given_the_cao_prompt(self):
        self.assertNotIn(cao_prompt.CAO_SYSTEM_PROMPT[:200], coo_agent.BASE_SYSTEM_PROMPT)
        self.assertNotIn(coo_prompt.COO_SYSTEM_PROMPT[:200], cao_agent.BASE_SYSTEM_PROMPT)

    def test_all_ten_pillars_are_present_and_numbered_in_order(self):
        text = coo_prompt.COO_SYSTEM_PROMPT
        positions = []
        for number, name in enumerate(PILLARS, start=1):
            marker = f"\n{number}. {name}"
            self.assertIn(marker, text, name)
            positions.append(text.index(marker))
        self.assertEqual(positions, sorted(positions))

    def test_each_pillar_covers_the_topics_in_the_brief(self):
        text = coo_prompt.COO_SYSTEM_PROMPT.lower()
        for needle in (
            "cash-flow forecasting", "payment scheduling", "cost optimisation",
            "token usage optimisation", "cost-per-query", "budget alerts",
            "budget estimation", "resource allocation", "roi analysis",
            "supply chain optimisation", "delivery scheduling", "inventory management",
            "regulatory monitoring", "risk assessment", "mitigation strategies",
            "sop enforcement", "defect tracking", "continuous improvement",
            "environmental impact monitoring", "resource efficiency", "esg reporting",
            "kpi tracking", "trend analysis", "predictive modelling",
            "request to another agent",
        ):
            self.assertIn(needle, text, needle)

    def test_stress_test_pillar_flags_a_financially_reckless_optimistic_scenario(self):
        text = coo_prompt.COO_SYSTEM_PROMPT
        for needle in ("FINANCIALLY RECKLESS", "Optimistic", "SUPPORTED WITH CONDITIONS", "NOT SUPPORTED", "CANNOT ASSESS"):
            self.assertIn(needle, text, needle)
        for needle in ("financial viability", "operational realism"):  # headings are capitalised in the prompt
            self.assertIn(needle, text.lower(), needle)

    def test_the_coo_is_told_it_cannot_act_and_has_no_access_to_the_books(self):
        text = coo_prompt.COO_SYSTEM_PROMPT
        self.assertIn("You cannot pay, schedule, order", text)
        self.assertIn("NO live access to Century Aqua's books", text)
        self.assertIn("Never invent a cost", text)
        self.assertIn("the CTO", text)
        self.assertIn("does NOT exist yet", text)


class SharedOutputContractTests(unittest.TestCase):
    """The COO and CAO formats must stay identical in shape, so the Council Protocol can read both."""

    @staticmethod
    def _example(prompt):
        return json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])

    def test_both_prompts_show_the_identical_json_shape(self):
        coo_example = self._example(coo_prompt.COO_REASONING_PROMPT)
        cao_example = self._example(cao_prompt.CAO_REASONING_PROMPT)
        self.assertEqual(coo_example, cao_example)
        self.assertEqual(set(coo_example), {"response", "reasoning_chain", "scenarios", "pre_consultation_requests"})
        self.assertEqual(set(coo_example["scenarios"]), {"optimistic", "baseline", "pessimistic"})

    def test_the_coo_format_prompt_names_every_required_field_and_the_stress_test_section(self):
        text = coo_prompt.COO_REASONING_PROMPT
        for needle in (
            "reasoning_chain", "scenarios", "pre_consultation_requests", "coo_query", "cto_query",
            "description", "timeline", "resource_requirements", "confidence_percentage", "key_assumptions", "risks",
            "Data and sources:", "Assumptions:", "Limitations:", "Forecast watch-list",
            "Recommendation for human review:", "Stress test of the CAO plan", "financially reckless",
        ):
            self.assertIn(needle, text, needle)

    def test_the_coo_does_not_claim_to_have_consulted_anyone(self):
        self.assertIn("never state or imply that you consulted anyone", coo_prompt.COO_REASONING_PROMPT)

    def test_the_coo_reuses_the_shared_parser_and_pre_consultation_stays_forced_to_null(self):
        from app.agents import cao_reasoning

        self.assertIs(coo_agent.parse_cao_output, cao_reasoning.parse_cao_output)
        sneaky = dict(COO_ANSWER, pre_consultation_requests={"coo_query": "x", "cto_query": "Is it feasible?"})
        parsed = cao_reasoning.parse_cao_output(model_text(sneaky))
        self.assertEqual(parsed.pre_consultation_requests, {"coo_query": None, "cto_query": None})



class CooEndpointTests(unittest.TestCase):
    """POST /api/coo with a mocked model and no database (audit_id is therefore null)."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(coo_env().stop)
        self.headers = {"X-Internal-COO-Key": COO_KEY, "X-User-Id": "user_staff_1"}

    def ask(self, reply_text, message=QUERY):
        calls = []
        with mock.patch.object(coo_agent, "_client", fake_client(reply_text, calls)):
            response = self.client.post("/api/coo", headers=self.headers, json={"message": message})
        return response, calls

    def test_strategic_query_returns_the_same_shape_as_the_cao(self):
        response, calls = self.ask(model_text(COO_ANSWER))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("response", "reply", "reasoning_chain", "scenarios", "pre_consultation_requests",
                    "constitutional_score", "needs_review", "audit_id"):
            self.assertIn(key, body)
        self.assertEqual(body["reply"], body["response"])
        self.assertTrue(calls[0]["system"].startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn("THE TEN PILLARS", calls[0]["system"])
        self.assertEqual(calls[0]["messages"][-1], {"role": "user", "content": QUERY})
        self.assertEqual(calls[0]["model"], coo_agent.COO_MODEL)

    def test_the_response_stress_tests_the_cao_plan(self):
        text = self.ask(model_text(COO_ANSWER))[0].json()["response"]
        self.assertIn("Stress test of the CAO plan", text)
        self.assertIn("Financial viability", text)
        self.assertIn("Operational realism", text)
        self.assertIn("FINANCIALLY RECKLESS", text)  # the COO flagged the CAO's optimistic scenario
        self.assertIn("Verdict: SUPPORTED WITH CONDITIONS", text)
        self.assertIn("Recommendation for human review:", text)

    def test_reasoning_chain_has_three_to_five_pillar_steps_plus_the_three_closing_entries(self):
        chain = self.ask(model_text(COO_ANSWER))[0].json()["reasoning_chain"]
        steps = [s for s in chain if s.startswith("Step ")]
        self.assertTrue(3 <= len(steps) <= 5, chain)
        self.assertEqual([s.split(":")[0] for s in chain[len(steps):]], ["Data and sources", "Assumptions", "Limitations"])
        self.assertTrue(any("Strategic Stress-Testing" in s for s in steps))

    def test_three_distinct_scenarios_with_every_field_and_a_confidence(self):
        scenarios = self.ask(model_text(COO_ANSWER))[0].json()["scenarios"]
        self.assertEqual(set(scenarios), {"optimistic", "baseline", "pessimistic"})
        for name, scenario in scenarios.items():
            self.assertEqual(
                set(scenario),
                {"description", "timeline", "resource_requirements", "confidence_percentage", "key_assumptions", "risks"},
                name,
            )
            self.assertTrue(0 <= scenario["confidence_percentage"] <= 100)
        confidences = {name: scenario["confidence_percentage"] for name, scenario in scenarios.items()}
        self.assertEqual(confidences, {"optimistic": 20, "baseline": 50, "pessimistic": 30})
        self.assertEqual(len({s["description"] for s in scenarios.values()}), 3)

    def test_the_coos_scenarios_are_its_own_not_a_copy_of_the_caos(self):
        coo_scenarios = self.ask(model_text(COO_ANSWER))[0].json()["scenarios"]
        self.assertNotEqual(coo_scenarios, CAO_ANSWER["scenarios"])

    def test_pre_consultation_requests_structure_is_present_and_null(self):
        body = self.ask(model_text(COO_ANSWER))[0].json()
        self.assertEqual(body["pre_consultation_requests"], {"coo_query": None, "cto_query": None})


    def test_a_model_that_tries_to_fill_pre_consultation_is_overridden_with_null(self):
        sneaky = dict(COO_ANSWER, pre_consultation_requests={"coo_query": None, "cto_query": "Is offshore feasible?"})
        body = self.ask(model_text(sneaky))[0].json()
        self.assertEqual(body["pre_consultation_requests"], {"coo_query": None, "cto_query": None})

    def test_non_strategic_query_gets_null_scenarios_but_still_a_chain(self):
        body = self.ask(model_text(dict(COO_ANSWER, scenarios=None)), message="What is cost per query?")[0].json()
        self.assertIsNone(body["scenarios"])
        self.assertTrue(body["reasoning_chain"])

    def test_a_model_that_ignores_the_format_still_gets_a_valid_framed_response(self):
        response, _ = self.ask("Cut the feed budget by half.")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("Cut the feed budget by half.", body["response"])
        self.assertIn("Recommendation for human review:", body["response"])
        self.assertEqual((body["reasoning_chain"], body["scenarios"]), ([], None))
        self.assertTrue(body["needs_review"])

    def test_score_is_in_range_and_a_good_answer_does_not_need_review(self):
        body = self.ask(model_text(COO_ANSWER))[0].json()
        self.assertTrue(1 <= body["constitutional_score"] <= 100)
        self.assertFalse(body["needs_review"])

    def test_rag_excerpts_are_labelled_and_the_sources_are_reported(self):
        from app.rag import retrieval

        excerpt = retrieval.RetrievalResult(
            context="[1] Offshore_costs.pdf | type: market_report | page 3\nSome text.",
            sources=[retrieval.RetrievedSource("Offshore_costs.pdf", "market_report", 3, "COSTS", 0.9)],
        )
        calls = []
        with mock.patch.object(coo_agent, "_client", fake_client(model_text(COO_ANSWER), calls)), mock.patch.object(
            retrieval, "retrieve_context_with_sources", mock.AsyncMock(return_value=excerpt)
        ):
            body = self.client.post("/api/coo", headers=self.headers, json={"message": QUERY}).json()
        self.assertIn("KNOWLEDGE BASE EXCERPTS", calls[0]["system"])
        self.assertIn("hold no Century Aqua financial", calls[0]["system"])
        self.assertIn("Offshore_costs.pdf", body["response"])  # the retrieved-documents note
        self.assertIn("Offshore_costs.pdf", body["sources"])

    def test_a_model_failure_is_a_502_that_does_not_leak_the_message(self):
        async def boom(**kwargs):
            raise RuntimeError("upstream exploded with SECRET-DETAIL")

        with mock.patch.object(coo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=boom))):
            response = self.client.post("/api/coo", headers=self.headers, json={"message": QUERY})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("SECRET-DETAIL", response.text)
        self.assertNotIn(QUERY, response.text)


class CooKeyGatingTests(unittest.TestCase):
    """/api/coo is gated by INTERNAL_COO_KEY only, and never calls the model before the key is checked."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(coo_env().stop)
        self.calls = []
        patcher = mock.patch.object(coo_agent, "_client", fake_client(model_text(COO_ANSWER), self.calls))
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, headers=None):
        return self.client.post("/api/coo", json={"message": QUERY}, headers=headers or {})

    def test_no_key_and_a_wrong_key_are_401_and_never_reach_the_model(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.post({"X-Internal-COO-Key": "wrong"}).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_the_cao_key_does_not_open_the_coo(self):
        for header in ("X-Internal-COO-Key", "X-Internal-CAO-Key"):
            self.assertEqual(self.post({header: CAO_KEY}).status_code, 401, header)
        self.assertEqual(self.calls, [])

    def test_the_coo_key_does_not_open_the_cao_or_the_other_governance_endpoints(self):
        for header in ("X-Internal-CAO-Key", "X-Internal-COO-Key"):
            r = self.client.post("/api/cao", json={"message": QUERY}, headers={header: COO_KEY})
            self.assertEqual(r.status_code, 401, header)
        cross = {"agent_a_id": "cao", "agent_b_id": "coo", "query": "q", "context": "c"}
        self.assertEqual(
            self.client.post("/api/agents/cross-check", json=cross, headers={"X-Internal-CAO-Key": COO_KEY}).status_code, 401
        )

    def test_the_correct_key_opens_the_coo(self):
        self.assertEqual(self.post({"X-Internal-COO-Key": COO_KEY}).status_code, 200)

    def test_the_endpoint_is_closed_503_when_the_coo_key_is_not_configured(self):
        os.environ.pop("INTERNAL_COO_KEY")
        response = self.post({"X-Internal-COO-Key": "anything"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(COO_KEY, response.text)
        self.assertEqual(self.calls, [])

    def test_an_empty_configured_key_does_not_accept_an_empty_header(self):
        os.environ["INTERNAL_COO_KEY"] = ""
        self.assertEqual(self.post({"X-Internal-COO-Key": ""}).status_code, 503)

    def test_the_cao_gate_is_unchanged_by_the_new_parameters(self):
        security.require_internal_key(CAO_KEY)  # the original one-argument call still works
        with self.assertRaises(Exception) as caught:
            security.require_internal_key("wrong")
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("X-Internal-CAO-Key", caught.exception.detail)



class CrossCheckWithTheRealCooTests(unittest.IsolatedAsyncioTestCase):
    """The real COO (not a stand-in) can now review the CAO through the existing cross-check."""

    VERDICT = '{"valid": false, "reasoning": "Optimistic scenario is financially reckless.", "confidence": 0.8, "constitutional_alignment": "partially_aligned"}'

    def setUp(self):
        self.addCleanup(coo_env().stop)
        limiter.reset()

    async def test_cao_to_coo_cross_check_uses_the_real_coo_function(self):
        calls = []
        with mock.patch.object(coo_agent, "_client", fake_client(self.VERDICT, calls)):
            result = await cv.cross_check("cao", "coo", "Expand offshore?", model_text(CAO_ANSWER))
        self.assertFalse(result.valid)  # the COO said the plan has a material problem
        self.assertEqual(result.constitutional_alignment, "partially_aligned")
        self.assertEqual(result.confidence, 0.8)
        # The review reached the real COO: constitution first, ten pillars, RAG off (no excerpts block).
        self.assertTrue(calls[0]["system"].startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn("STRATEGIC STRESS-TESTING", calls[0]["system"])
        self.assertNotIn("KNOWLEDGE BASE EXCERPTS", calls[0]["system"])
        self.assertIn("RECOMMENDATION UNDER REVIEW", calls[0]["messages"][-1]["content"])

    async def test_a_cross_check_review_is_not_written_to_the_audit_log_as_an_answer(self):
        with mock.patch.object(coo_agent, "_client", fake_client(self.VERDICT)), mock.patch.object(
            audit, "record_response", mock.AsyncMock()
        ) as record:
            await cv.cross_check("cao", "coo", "q", "ctx")
        record.assert_not_awaited()  # audit=False: the review goes to CrossValidationLog instead

    async def test_the_reverse_direction_coo_to_cao_also_works(self):
        with mock.patch.object(cao_agent, "_client", fake_client(self.VERDICT)):
            result = await cv.cross_check("coo", "cao", "q", "ctx")
        self.assertFalse(result.valid)


class RecordsToTheAuditLogAsCooTests(unittest.IsolatedAsyncioTestCase):
    """No database: prove the call is audit.record_response('coo', ...) with every Phase 1 field."""

    def setUp(self):
        self.addCleanup(coo_env().stop)

    async def test_get_coo_result_records_agent_coo_with_the_structured_fields(self):
        record = mock.AsyncMock(return_value=(7, mock.Mock()))
        with mock.patch.object(coo_agent, "_client", fake_client(model_text(COO_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await coo_agent.get_coo_result(QUERY, use_rag=False, user_id="user_staff_1")
        self.assertEqual(result.audit_id, 7)
        args, kwargs = record.call_args
        self.assertEqual(args[:3], ("coo", "user_staff_1", QUERY))
        self.assertIn("Stress test of the CAO plan", args[3])
        self.assertEqual(kwargs["reasoning_chain"], COO_ANSWER["reasoning_chain"])
        self.assertEqual(kwargs["scenarios"], COO_ANSWER["scenarios"])
        self.assertEqual(kwargs["pre_consultation_requests"], {"coo_query": None, "cto_query": None})
        self.assertEqual(kwargs["score"].score, result.constitutional_score)

    async def test_audit_false_records_nothing(self):
        record = mock.AsyncMock()
        with mock.patch.object(coo_agent, "_client", fake_client(model_text(COO_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await coo_agent.get_coo_result("q", use_rag=False, audit=False)
        record.assert_not_awaited()
        self.assertIsNone(result.audit_id)

    async def test_a_database_failure_never_blocks_the_coos_answer(self):
        bad_db = {"GOVERNANCE_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x?sslmode=disable"}
        with mock.patch.dict(os.environ, bad_db), mock.patch.object(
            coo_agent, "_client", fake_client(model_text(COO_ANSWER))
        ):
            with self.assertLogs("app.governance.audit", level="ERROR") as logs:
                result = await coo_agent.get_coo_result("q", use_rag=False)
        self.assertIn("Recommendation for human review", result.reply)
        self.assertIsNone(result.audit_id)
        self.assertIn("GovernanceDBError", logs.output[0])

    async def test_get_coo_answer_returns_text_for_cross_validation(self):
        reply = await _answer(model_text(COO_ANSWER))
        self.assertIsInstance(reply, str)
        self.assertIn("Stress test of the CAO plan", reply)


async def _answer(text):
    with mock.patch.object(coo_agent, "_client", fake_client(text)):
        return await coo_agent.get_coo_answer("q", use_rag=False, audit=False)


async def _answer_with_stop(text, stop_reason):
    async def create(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)

    with mock.patch.object(coo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))):
        return await coo_agent.get_coo_answer("q", use_rag=False, audit=False)



# ---------------------------------------------------------------------------------------------
# Part 6: the real AgentAuditLog row. Local database only (same safety rules as test_governance_db.py).
# Rows are written under the dedicated agent id 'testbot' and deleted afterwards, so the local 'coo'
# history is never touched. The agent id 'coo' itself is proven by RecordsToTheAuditLogAsCooTests above.
# ---------------------------------------------------------------------------------------------
@unittest.skipUnless(DB_READY, "needs the local prisma dev database with the Phase 1 migration applied")
class CooAuditRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL, "INTERNAL_COO_KEY": COO_KEY}),
            mock.patch.object(coo_agent, "AGENT_ID", TEST_AGENT),
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

    async def test_the_coo_answer_is_logged_with_every_field_populated(self):
        with mock.patch.object(coo_agent, "_client", fake_client(model_text(COO_ANSWER))):
            result = await coo_agent.get_coo_result(QUERY, user_id="user_staff_1")
        self.assertIsInstance(result.audit_id, int)

        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertEqual((row["agentId"], row["userId"], row["query"]), (TEST_AGENT, "user_staff_1", QUERY))
        self.assertIn("Stress test of the CAO plan", row["response"])
        self.assertIn("Recommendation for human review:", row["response"])
        self.assertEqual(row["constitutionalScore"], result.constitutional_score)
        self.assertEqual(row["humanAction"], "Pending")
        self.assertIsNotNone(row["timestamp"])
        self.assertEqual(list(row["reasoningChain"]), COO_ANSWER["reasoning_chain"])
        self.assertEqual(_json(row["scenarios"]), COO_ANSWER["scenarios"])
        self.assertEqual(_json(row["preConsultationRequests"]), {"coo_query": None, "cto_query": None})
        self.assertTrue(row["sourcesCited"])

    async def test_the_agent_state_buffer_is_kept_for_the_coo_too(self):
        with mock.patch.object(coo_agent, "_client", fake_client("Cut the feed budget by half.")):
            await coo_agent.get_coo_result(QUERY, user_id="user_staff_1")
        state = audit.snapshot_sync(TEST_AGENT)
        self.assertEqual(len(state["conversationHistory"]), 1)
        self.assertEqual(state["conversationHistory"][0]["query"], QUERY)
        self.assertEqual(state["constitutionalViolations"], 1)  # the plain-text answer scored below 70

    async def test_the_stored_stress_test_scenarios_have_the_cao_shape(self):
        with mock.patch.object(coo_agent, "_client", fake_client(model_text(COO_ANSWER))):
            result = await coo_agent.get_coo_result(QUERY)
        stored = _json(_query('SELECT "scenarios" FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]["scenarios"])
        self.assertEqual(set(stored), {"optimistic", "baseline", "pessimistic"})
        for scenario in stored.values():
            self.assertEqual(
                set(scenario),
                {"description", "timeline", "resource_requirements", "confidence_percentage", "key_assumptions", "risks"},
            )


    async def test_endpoint_end_to_end_logs_the_row_it_returns(self):
        from app.main import app

        client = TestClient(app)
        headers = {"X-Internal-COO-Key": COO_KEY, "X-User-Id": "user_staff_1"}
        with mock.patch.object(coo_agent, "_client", fake_client(model_text(COO_ANSWER))), mock.patch(
            "app.api.coo.audit.current_epoch", mock.AsyncMock(return_value=0)
        ):
            response = client.post("/api/coo", headers=headers, json={"message": QUERY})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsInstance(body["audit_id"], int)
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (body["audit_id"],))[0]
        self.assertEqual(list(row["reasoningChain"]), body["reasoning_chain"])
        self.assertEqual(_json(row["scenarios"]), body["scenarios"])
        self.assertEqual(_json(row["preConsultationRequests"]), body["pre_consultation_requests"])
        self.assertEqual(row["constitutionalScore"], body["constitutional_score"])

    async def test_the_coo_honours_the_kill_switch_epoch(self):
        from app.main import app

        client = TestClient(app)
        headers = {"X-Internal-COO-Key": COO_KEY, "X-User-Id": "user_staff_1"}
        history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier answer"}]
        seen = []

        async def create(**kwargs):
            seen.append(kwargs["messages"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=model_text(COO_ANSWER))], stop_reason="end_turn")

        with mock.patch.object(coo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))), mock.patch(
            "app.api.coo.audit.current_epoch", mock.AsyncMock(return_value=3)
        ):
            fresh = client.post("/api/coo", headers=headers, json={"message": "now", "history": history, "reset_epoch": 3})
            stale = client.post("/api/coo", headers=headers, json={"message": "now", "history": history, "reset_epoch": 2})
        self.assertEqual((fresh.json()["history_discarded"], stale.json()["history_discarded"]), (False, True))
        self.assertEqual(len(seen[0]), 3)
        self.assertEqual(seen[1], [{"role": "user", "content": "now"}])  # the cancelled history never reached the model


if __name__ == "__main__":
    unittest.main()

