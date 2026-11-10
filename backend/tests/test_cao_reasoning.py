"""
Phase 1 tests: CAO Enhanced Reasoning & Prediction.

Parts 1-3 need NO database: the output parser, the prompt, and POST /api/cao with a mocked
model asked a strategic question ("Should we invest in RAS technology for salmon?").
Part 4 (CaoAuditRowTests) checks that the AgentAuditLog row is fully populated. Like
test_governance_db.py it runs ONLY against a localhost database and is skipped otherwise.

No test here calls a real model.
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

from app.agents import base_constitution as bc  # noqa: E402
from app.agents import cao_agent, cao_reasoning  # noqa: E402
from app.governance import audit, db  # noqa: E402
from app.prompts import cao_prompt  # noqa: E402

from tests.test_governance import KEY, clean_env  # noqa: E402

QUERY = "Should we invest in RAS technology for salmon?"


def _scenario(description, timeline, confidence):
    return {
        "description": description,
        "timeline": timeline,
        "resource_requirements": "Budget: illustrative assumption, to be sized by a site study. Staff: 4-6 technicians. Infrastructure: a pilot RAS module.",
        "confidence_percentage": confidence,
        "key_assumptions": ["Stable power supply", "Biofilter reaches steady state"],
        "risks": ["Equipment failure", "Off-flavour in the first cycles"],
    }


STRATEGIC = {
    "response": (
        "Bottom line: pilot RAS for salmon before committing to scale. Confidence: 62%. "
        "The knowledge base [1] does not cover salmon RAS economics, so this rests on general knowledge. "
        "Forecast watch-list: energy prices (emerging), effluent rules (established).\n\n"
        "Recommendation for human review: decide whether to fund a small salmon RAS pilot."
    ),
    "reasoning_chain": [
        "Step 1: RAS removes dependence on site water but moves risk into engineering and power.",
        "Step 2: Salmon need cold, high-quality water, so energy cost is a major driver.",
        "Step 3: Century Aqua's evidence is for sea tilapia, not salmon, so a pilot is needed.",
        "Data and sources: [1] NUS_trial.pdf (trial_data) covers SND with sea tilapia only; the rest is general knowledge.",
        "Assumptions: electricity price and survival rate are illustrative assumptions.",
        "Limitations: financial and technical feasibility have not been reviewed by anyone.",
    ],
    "scenarios": {
        "optimistic": _scenario("Pilot succeeds and a commercial module follows.", "12-18 months", 25),
        "baseline": _scenario("Pilot works but economics need tuning.", "18-30 months", 55),
        "pessimistic": _scenario("Energy and mortality costs make the case unviable.", "24-36 months", 20),
    },
    "pre_consultation_requests": {"coo_query": None, "cto_query": None},
}


def model_text(payload) -> str:
    return payload if isinstance(payload, str) else json.dumps(payload)


def fake_client(reply_text, calls=None):
    """A stand-in for the Anthropic client that returns ``reply_text`` and records each call."""

    async def create(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply_text)], stop_reason="end_turn")

    return SimpleNamespace(messages=SimpleNamespace(create=create))


class ParserTests(unittest.TestCase):
    def test_full_structured_answer_is_parsed(self):
        parsed = cao_reasoning.parse_cao_output(model_text(STRATEGIC))
        self.assertTrue(parsed.structured)
        self.assertEqual(parsed.findings, [])
        self.assertEqual(parsed.response, STRATEGIC["response"])
        self.assertEqual(len(parsed.reasoning_chain), 6)
        self.assertEqual(set(parsed.scenarios), {"optimistic", "baseline", "pessimistic"})
        self.assertEqual(parsed.scenarios["baseline"]["confidence_percentage"], 55)
        self.assertEqual(parsed.pre_consultation_requests, {"coo_query": None, "cto_query": None})

    def test_code_fence_and_stray_prose_are_tolerated(self):
        wrapped = "Here you go:\n```json\n" + model_text(STRATEGIC) + "\n```"
        self.assertTrue(cao_reasoning.parse_cao_output(wrapped).structured)

    def test_non_strategic_answer_has_null_scenarios(self):
        data = dict(STRATEGIC, scenarios=None)
        parsed = cao_reasoning.parse_cao_output(model_text(data))
        self.assertTrue(parsed.structured)
        self.assertIsNone(parsed.scenarios)
        self.assertEqual(parsed.findings, [])  # null scenarios is valid, not a format problem

    def test_incomplete_scenario_set_is_dropped_whole_never_partial(self):
        data = json.loads(model_text(STRATEGIC))
        del data["scenarios"]["pessimistic"]
        parsed = cao_reasoning.parse_cao_output(model_text(data))
        self.assertIsNone(parsed.scenarios)
        self.assertTrue(any("scenarios" in f for f in parsed.findings))
        self.assertEqual(parsed.response, STRATEGIC["response"])  # the recommendation survives

    def test_scenario_missing_a_field_or_with_bad_confidence_is_rejected(self):
        for field, bad in (
            ("risks", []),
            ("timeline", ""),
            ("confidence_percentage", 0.7),
            ("confidence_percentage", 150),
            ("confidence_percentage", "high"),
            ("confidence_percentage", True),
            ("key_assumptions", None),
        ):
            data = json.loads(model_text(STRATEGIC))
            data["scenarios"]["baseline"][field] = bad
            self.assertIsNone(cao_reasoning.parse_cao_output(model_text(data)).scenarios, (field, bad))

    def test_confidence_given_as_text_or_float_is_normalised(self):
        for given in ("70%", " 70 ", 70.0):
            data = json.loads(model_text(STRATEGIC))
            data["scenarios"]["baseline"]["confidence_percentage"] = given
            parsed = cao_reasoning.parse_cao_output(model_text(data))
            self.assertEqual(parsed.scenarios["baseline"]["confidence_percentage"], 70, given)

    def test_pre_consultation_is_forced_to_null_in_phase_1(self):
        data = dict(STRATEGIC, pre_consultation_requests={"coo_query": "Is it affordable?", "cto_query": "Is it feasible?"})
        parsed = cao_reasoning.parse_cao_output(model_text(data))
        self.assertEqual(parsed.pre_consultation_requests, {"coo_query": None, "cto_query": None})

    def test_pre_consultation_passes_through_only_when_the_council_flag_is_on(self):
        data = dict(STRATEGIC, pre_consultation_requests={"coo_query": "Is it affordable?", "cto_query": None})
        with mock.patch.object(cao_reasoning, "PRE_CONSULTATION_ENABLED", True):
            parsed = cao_reasoning.parse_cao_output(model_text(data))
        self.assertEqual(parsed.pre_consultation_requests, {"coo_query": "Is it affordable?", "cto_query": None})

    def test_plain_text_falls_back_to_the_whole_text(self):
        parsed = cao_reasoning.parse_cao_output("Build three new ponds.")
        self.assertFalse(parsed.structured)
        self.assertEqual(parsed.response, "Build three new ponds.")
        self.assertEqual((parsed.reasoning_chain, parsed.scenarios), ([], None))
        self.assertTrue(parsed.findings)

    def test_a_cross_validation_verdict_is_returned_untouched(self):
        verdict = '{"valid": true, "reasoning": "ok", "confidence": 0.9, "constitutional_alignment": "aligned"}'
        parsed = cao_reasoning.parse_cao_output(verdict)
        self.assertEqual(parsed.response, verdict)  # cross_validation.parse_review can still read it
        self.assertFalse(parsed.structured)

    def test_truncated_json_still_gives_the_human_the_recommendation(self):
        full = model_text(STRATEGIC)
        parsed = cao_reasoning.parse_cao_output(full[: full.index('"reasoning_chain"')])
        self.assertIn("pilot RAS for salmon", parsed.response)
        self.assertNotIn('"response"', parsed.response)
        self.assertEqual((parsed.reasoning_chain, parsed.scenarios), ([], None))

    def test_never_raises_on_garbage(self):
        for raw in ("", None, "{", "}{", "[]", '{"response": 5}', '{"response": ""}', "null", "x" * 100000):
            cao_reasoning.parse_cao_output(raw)

    def test_oversized_lists_and_strings_are_capped(self):
        data = json.loads(model_text(STRATEGIC))
        data["reasoning_chain"] = [f"Step {i}: " + "x" * 5000 for i in range(40)]
        parsed = cao_reasoning.parse_cao_output(model_text(data))
        self.assertEqual(len(parsed.reasoning_chain), cao_reasoning.MAX_CHAIN_ENTRIES)
        self.assertLessEqual(max(len(s) for s in parsed.reasoning_chain), cao_reasoning.MAX_ITEM_CHARS)



class PromptTests(unittest.TestCase):
    def test_reasoning_format_is_in_the_system_prompt_after_the_constitution(self):
        system = cao_agent.BASE_SYSTEM_PROMPT
        self.assertTrue(system.startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn(cao_prompt.CAO_SYSTEM_PROMPT, system)
        self.assertLess(system.index("CLAUSE 0:"), system.index("ADVANCED REASONING AND PREDICTION"))
        self.assertEqual(bc.clause_numbers(), list(range(16)))  # the constitution itself is untouched

    def test_prompt_names_every_required_field(self):
        text = cao_prompt.CAO_REASONING_PROMPT
        for needle in (
            "reasoning_chain", "scenarios", "pre_consultation_requests", "coo_query", "cto_query",
            "optimistic", "baseline", "pessimistic", "description", "timeline", "resource_requirements",
            "confidence_percentage", "key_assumptions", "risks",
            "Data and sources:", "Assumptions:", "Limitations:", "Forecast watch-list",
            "Recommendation for human review:",
        ):
            self.assertIn(needle, text)

    def test_prompt_says_never_to_invent_data_or_pretend_a_consultation_happened(self):
        text = cao_prompt.CAO_REASONING_PROMPT
        self.assertIn("Never invent", text)
        self.assertIn("never state or imply that you did", text)

    def test_the_json_example_in_the_prompt_is_itself_valid_json(self):
        text = cao_prompt.CAO_REASONING_PROMPT
        example = text[text.index('{\n  "response"'): text.index("\n}\n") + 2]
        data = json.loads(example)
        self.assertEqual(set(data), {"response", "reasoning_chain", "scenarios", "pre_consultation_requests"})
        self.assertEqual(set(data["scenarios"]), {"optimistic", "baseline", "pessimistic"})



class CaoEndpointTests(unittest.TestCase):
    """POST /api/cao with a mocked model, no database (audit_id is therefore null)."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(clean_env().stop)
        self.headers = {"X-Internal-CAO-Key": KEY, "X-User-Id": "user_staff_1"}

    def ask(self, reply_text, message=QUERY):
        calls = []
        with mock.patch.object(cao_agent, "_client", fake_client(reply_text, calls)):
            # RAG is on by default in the endpoint; with no Pinecone/OpenAI keys it degrades to no context.
            response = self.client.post("/api/cao", headers=self.headers, json={"message": message})
        return response, calls

    def test_strategic_query_returns_the_full_phase_1_shape(self):
        response, calls = self.ask(model_text(STRATEGIC))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("response", "reasoning_chain", "scenarios", "pre_consultation_requests",
                    "constitutional_score", "needs_review", "audit_id"):
            self.assertIn(key, body)
        self.assertEqual(body["reply"], body["response"])  # old callers keep working
        self.assertIn("Recommendation for human review:", body["response"])
        self.assertTrue(calls[0]["system"].startswith("EXECUTIVE CONSTITUTION"))
        self.assertEqual(calls[0]["messages"][-1], {"role": "user", "content": QUERY})

    def test_reasoning_chain_has_three_to_five_steps_plus_the_three_closing_entries(self):
        chain = self.ask(model_text(STRATEGIC))[0].json()["reasoning_chain"]
        steps = [s for s in chain if s.startswith("Step ")]
        self.assertTrue(3 <= len(steps) <= 5, chain)
        self.assertEqual([s.split(":")[0] for s in chain[len(steps):]], ["Data and sources", "Assumptions", "Limitations"])

    def test_three_distinct_scenarios_each_with_every_field_and_a_confidence(self):
        scenarios = self.ask(model_text(STRATEGIC))[0].json()["scenarios"]
        self.assertEqual(set(scenarios), {"optimistic", "baseline", "pessimistic"})
        for name, scenario in scenarios.items():
            self.assertEqual(
                set(scenario),
                {"description", "timeline", "resource_requirements", "confidence_percentage", "key_assumptions", "risks"},
                name,
            )
            self.assertIsInstance(scenario["confidence_percentage"], int)
            self.assertTrue(0 <= scenario["confidence_percentage"] <= 100)
            self.assertTrue(scenario["key_assumptions"] and scenario["risks"])
        self.assertEqual(len({s["description"] for s in scenarios.values()}), 3)
        self.assertEqual(len({s["timeline"] for s in scenarios.values()}), 3)

    def test_pre_consultation_requests_structure_is_present_and_null(self):
        body = self.ask(model_text(STRATEGIC))[0].json()
        self.assertEqual(body["pre_consultation_requests"], {"coo_query": None, "cto_query": None})

    def test_constitutional_score_is_in_range_and_counts_the_reasoning_chain(self):
        body = self.ask(model_text(STRATEGIC))[0].json()
        self.assertTrue(1 <= body["constitutional_score"] <= 100)
        self.assertFalse(body["needs_review"])

    def test_non_strategic_query_gets_null_scenarios_but_still_a_chain(self):
        body = self.ask(model_text(dict(STRATEGIC, scenarios=None)), message="What does RAS stand for?")[0].json()
        self.assertIsNone(body["scenarios"])
        self.assertTrue(body["reasoning_chain"])

    def test_a_model_that_ignores_the_format_still_gets_a_valid_response(self):
        response, _ = self.ask("Build three new ponds.")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("Build three new ponds.", body["response"])
        self.assertIn("Recommendation for human review:", body["response"])  # code-added framing still applies
        self.assertEqual((body["reasoning_chain"], body["scenarios"]), ([], None))
        self.assertEqual(body["pre_consultation_requests"], {"coo_query": None, "cto_query": None})
        self.assertTrue(body["needs_review"])

    def test_the_endpoint_is_still_gated_by_the_key(self):
        self.assertEqual(self.client.post("/api/cao", json={"message": "hi"}).status_code, 401)


class CaoAnswerFunctionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_validation_verdict_passes_through_get_cao_answer(self):
        self.addCleanup(clean_env().stop)
        verdict = '{"valid": true, "reasoning": "ok", "confidence": 0.9, "constitutional_alignment": "aligned"}'
        with mock.patch.object(cao_agent, "_client", fake_client(verdict)):
            reply = await cao_agent.get_cao_answer("review this", use_rag=False, audit=False)
        self.assertIn(verdict, reply)  # cross_validation.parse_review can still read the JSON object



# ---------------------------------------------------------------------------------------------
# Part 4: the AgentAuditLog row. Local database only (same safety rules as test_governance_db.py).
# ---------------------------------------------------------------------------------------------
TEST_AGENT = "testbot"


def _local_url():
    url = os.getenv("GOVERNANCE_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get("GOVERNANCE_DATABASE_URL") or ""
    try:
        host = db.parse_url(url)["host"] if url else ""
    except db.GovernanceDBError:
        return None
    return url if host in ("localhost", "127.0.0.1", "::1") else None


LOCAL_URL = _local_url()


def _db_ready():
    """True if the local database is up AND the Phase 1 migration has been applied."""
    if not LOCAL_URL:
        return False
    with mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL}):
        try:
            with db.transaction() as conn:
                conn.cursor().execute('SELECT "reasoningChain","scenarios","preConsultationRequests" FROM "AgentAuditLog" LIMIT 1')
            return True
        except Exception:
            return False


DB_READY = _db_ready()


def _query(sql, params=()):
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return db.fetch_all(cur)


def _cleanup():
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM "AgentAuditLog" WHERE "agentId" = %s', (TEST_AGENT,))
        cur.execute('DELETE FROM "AgentState" WHERE "agentId" = %s', (TEST_AGENT,))


def _json(value):
    return value if isinstance(value, (dict, list)) or value is None else json.loads(value)



@unittest.skipUnless(DB_READY, "needs the local prisma dev database with the enhance_cao_reasoning_and_prediction migration applied")
class CaoAuditRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL, "INTERNAL_CAO_KEY": KEY}),
            mock.patch.object(cao_agent, "AGENT_ID", TEST_AGENT),  # never write rows under the real 'cao' id
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

    async def test_strategic_answer_is_logged_with_every_field_populated(self):
        with mock.patch.object(cao_agent, "_client", fake_client(model_text(STRATEGIC))):
            result = await cao_agent.get_cao_result(QUERY, user_id="user_staff_1")
        self.assertIsInstance(result.audit_id, int)

        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertEqual((row["agentId"], row["userId"], row["query"]), (TEST_AGENT, "user_staff_1", QUERY))
        self.assertIn("Recommendation for human review:", row["response"])
        self.assertEqual(row["constitutionalScore"], result.constitutional_score)
        self.assertEqual(row["humanAction"], "Pending")
        self.assertTrue(row["sourcesCited"])

        self.assertEqual(list(row["reasoningChain"]), STRATEGIC["reasoning_chain"])
        self.assertEqual(_json(row["scenarios"]), STRATEGIC["scenarios"])
        self.assertEqual(_json(row["preConsultationRequests"]), {"coo_query": None, "cto_query": None})

    async def test_stored_scenarios_are_three_distinct_scenarios_with_confidence(self):
        with mock.patch.object(cao_agent, "_client", fake_client(model_text(STRATEGIC))):
            result = await cao_agent.get_cao_result(QUERY)
        stored = _json(_query('SELECT "scenarios" FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]["scenarios"])
        self.assertEqual(set(stored), {"optimistic", "baseline", "pessimistic"})
        confidences = {name: scenario["confidence_percentage"] for name, scenario in stored.items()}
        self.assertEqual(confidences, {"optimistic": 25, "baseline": 55, "pessimistic": 20})

    async def test_plain_text_answer_stores_sql_null_scenarios_and_an_empty_chain(self):
        with mock.patch.object(cao_agent, "_client", fake_client("Build three new ponds.")):
            result = await cao_agent.get_cao_result("q")
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertIsNone(row["scenarios"])  # SQL NULL, not the JSON value null
        self.assertEqual(list(row["reasoningChain"]), [])
        self.assertEqual(_json(row["preConsultationRequests"]), {"coo_query": None, "cto_query": None})

    async def test_text_with_quotes_backslashes_and_braces_round_trips_in_the_chain(self):
        tricky = ['Step 1: the "quoted" part', "Step 2: back\\slash and {braces}, commas", "Step 3: ünïcode ✓"]
        data = dict(STRATEGIC, reasoning_chain=tricky)
        with mock.patch.object(cao_agent, "_client", fake_client(model_text(data))):
            result = await cao_agent.get_cao_result("q")
        row = _query('SELECT "reasoningChain" FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertEqual(list(row["reasoningChain"]), tricky)

    async def test_other_agents_can_still_log_without_the_new_fields(self):
        audit_id, _ = await audit.record_response(TEST_AGENT, "u", "q", "Reasoning: x. Confidence: 80%.", [])
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (audit_id,))[0]
        self.assertEqual(list(row["reasoningChain"]), [])
        self.assertIsNone(row["scenarios"])

    async def test_endpoint_end_to_end_logs_the_row_it_returns(self):
        from app.main import app

        client = TestClient(app)
        headers = {"X-Internal-CAO-Key": KEY, "X-User-Id": "user_staff_1"}
        with mock.patch.object(cao_agent, "_client", fake_client(model_text(STRATEGIC))), mock.patch(
            "app.api.cao.audit.current_epoch", mock.AsyncMock(return_value=0)
        ):
            response = client.post("/api/cao", headers=headers, json={"message": QUERY})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsInstance(body["audit_id"], int)
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (body["audit_id"],))[0]
        self.assertEqual(list(row["reasoningChain"]), body["reasoning_chain"])
        self.assertEqual(_json(row["scenarios"]), body["scenarios"])
        self.assertEqual(_json(row["preConsultationRequests"]), body["pre_consultation_requests"])
        self.assertEqual(row["constitutionalScore"], body["constitutional_score"])


if __name__ == "__main__":
    unittest.main()

