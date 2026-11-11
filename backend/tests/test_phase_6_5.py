"""
Phase 6.5 tests (UX polish, backend side): model tiers, the conversational chat_reply and the plain-language
executive_summary. Run from backend/:   python -m unittest tests.test_phase_6_5

No test here calls a real model, and none needs a database.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import cao_agent, cao_reasoning, chat_reply, cmo_agent, cmo_authorization, coo_agent  # noqa: E402
from app.agents import cto_agent, cto_authorization, model_tiers, plain_summary  # noqa: E402
from app.prompts import chat_style  # noqa: E402

from tests.test_cao_reasoning import STRATEGIC, fake_client, model_text  # noqa: E402
from tests.test_cmo_agent import CAMPAIGN_ANSWER, CAO_KEY, CMO_KEY, COO_KEY, CTO_KEY, PUBLISH_ANSWER, cmo_env  # noqa: E402
from tests.test_cto_agent import CHANGE_ANSWER, POOLING_ANSWER  # noqa: E402

CHAT = "Short answer: pilot it first. The main risk is power cost. Do you want a pilot budget drafted?"
TIER_ENV = {"HAIKU_MODEL": "", "SONNET_MODEL": "", "OPUS_MODEL": ""}
SUMMARY = {
    "purpose": "Make pages load faster by reusing connections to the database.",
    "problem": "Right now every page opens a brand new connection, which slows things down.",
    "risk": "Too many connections at once could overload the database, so the limit must be checked first.",
}


class ModelTierTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, TIER_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_three_tiers_map_to_the_current_models(self):
        self.assertEqual(model_tiers.tier_model("haiku"), "claude-haiku-5-5")
        self.assertEqual(model_tiers.tier_model("sonnet"), "claude-sonnet-5-5")
        self.assertEqual(model_tiers.tier_model("opus"), "claude-opus-5-5")
        self.assertEqual(model_tiers.TIERS, ("haiku", "sonnet", "opus"))

    def test_a_tier_model_can_be_overridden_by_environment_without_a_code_change(self):
        with mock.patch.dict(os.environ, {"OPUS_MODEL": "claude-opus-next"}):
            self.assertEqual(model_tiers.tier_model("opus"), "claude-opus-next")

    def test_an_unknown_tier_is_refused_never_passed_through_as_a_model_id(self):
        for bad in ("claude-opus-5-5", "gpt-5", "", "OPUS", None):
            with self.assertRaises(ValueError, msg=bad):
                model_tiers.tier_model(bad)

    def test_no_tier_keeps_the_agents_own_model(self):
        self.assertEqual(model_tiers.resolve_model(None, "my-own-model"), "my-own-model")
        self.assertEqual(model_tiers.resolve_model("haiku", "my-own-model"), "claude-haiku-5-5")


class AgentTierEndpointTests(unittest.TestCase):
    """POST /api/{cao,coo,cto,cmo} with a `model` tier: the right model id reaches the Anthropic call."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        patcher = mock.patch.dict(os.environ, TIER_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cmo_env().stop)  # all four agent keys, no database, no RAG keys
        self.agents = {
            "cao": (cao_agent, "X-Internal-CAO-Key", CAO_KEY, STRATEGIC),
            "coo": (coo_agent, "X-Internal-COO-Key", COO_KEY, STRATEGIC),
            "cto": (cto_agent, "X-Internal-CTO-Key", CTO_KEY, POOLING_ANSWER),
            "cmo": (cmo_agent, "X-Internal-CMO-Key", CMO_KEY, CAMPAIGN_ANSWER),
        }

    def ask(self, agent, **extra):
        module, header, key, answer = self.agents[agent]
        calls = []
        with mock.patch.object(module, "_client", fake_client(model_text(answer), calls)):
            response = self.client.post(f"/api/{agent}", headers={header: key}, json={"message": "hello", **extra})
        return response, calls

    def test_each_tier_reaches_the_model_call_for_every_agent(self):
        expected = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
        for agent in self.agents:
            for tier, model_id in expected.items():
                response, calls = self.ask(agent, model=tier)
                self.assertEqual(response.status_code, 200, (agent, tier, response.text))
                self.assertEqual(calls[0]["model"], model_id, (agent, tier))
                self.assertEqual(response.json()["model"], model_id, (agent, tier))

    def test_without_a_tier_every_agent_uses_its_own_configured_model_as_before(self):
        own = {"cao": cao_agent.CAO_MODEL, "coo": coo_agent.COO_MODEL, "cto": cto_agent.CTO_MODEL, "cmo": cmo_agent.CMO_MODEL}
        for agent, model_id in own.items():
            response, calls = self.ask(agent)
            self.assertEqual(response.status_code, 200, agent)
            self.assertEqual(calls[0]["model"], model_id, agent)
            self.assertEqual(response.json()["model"], model_id, agent)

    def test_an_arbitrary_model_id_is_a_422_and_never_reaches_the_model(self):
        for agent in self.agents:
            for bad in ("claude-opus-5-5", "gpt-5", "OPUS", "", 5):
                response, calls = self.ask(agent, model=bad)
                self.assertEqual(response.status_code, 422, (agent, bad))
                self.assertEqual(calls, [], (agent, bad))

    def test_the_key_is_still_checked_before_the_tier(self):
        response = self.client.post("/api/cto", json={"message": "hi", "model": "opus"})
        self.assertEqual(response.status_code, 401)


class ParserChatReplyTests(unittest.TestCase):
    def test_chat_reply_is_read_and_cleaned_and_its_absence_is_not_a_format_problem(self):
        parsed = cao_reasoning.parse_cao_output(model_text(dict(STRATEGIC, chat_reply="  " + CHAT + "  ")))
        self.assertEqual(parsed.chat_reply, CHAT)
        self.assertEqual(parsed.findings, [])
        bare = cao_reasoning.parse_cao_output(model_text(STRATEGIC))
        self.assertIsNone(bare.chat_reply)
        self.assertEqual(bare.findings, [])

    def test_a_chat_reply_of_the_wrong_type_or_empty_is_none(self):
        for value in (None, "", "   ", 5, True):
            self.assertIsNone(cao_reasoning.parse_cao_output(model_text(dict(STRATEGIC, chat_reply=value))).chat_reply, value)

    def test_a_very_long_chat_reply_is_capped(self):
        parsed = cao_reasoning.parse_cao_output(model_text(dict(STRATEGIC, chat_reply="word " * 1000)))
        self.assertLessEqual(len(parsed.chat_reply), cao_reasoning.MAX_CHAT_REPLY_CHARS)

    def test_a_plain_text_answer_has_no_chat_reply(self):
        self.assertIsNone(cao_reasoning.parse_cao_output("Build three new ponds.").chat_reply)


class VettingTests(unittest.TestCase):
    def test_a_chat_reply_that_claims_an_action_is_dropped(self):
        for text in ("I have approved the budget for you.", "I've published the post.", "I deployed the fix this morning."):
            self.assertIsNone(chat_reply.vetted_chat_reply(text), text)

    def test_an_ordinary_chat_reply_survives(self):
        self.assertEqual(chat_reply.vetted_chat_reply(" " + CHAT + " "), CHAT)
        waiting = "A request is waiting for your decision. Nothing has been done yet."
        self.assertEqual(chat_reply.vetted_chat_reply(waiting), waiting)
        self.assertIsNone(chat_reply.vetted_chat_reply(None))
        self.assertIsNone(chat_reply.vetted_chat_reply("   "))


class PromptTests(unittest.TestCase):
    def test_the_chat_reply_block_is_in_all_four_agents_and_the_summary_block_only_in_cto_and_cmo(self):
        for module in (cao_agent, coo_agent, cto_agent, cmo_agent):
            self.assertIn(chat_style.CHAT_REPLY_PROMPT.strip(), module.BASE_SYSTEM_PROMPT, module.__name__)
            self.assertTrue(module.BASE_SYSTEM_PROMPT.startswith("EXECUTIVE CONSTITUTION"), module.__name__)
        for module in (cto_agent, cmo_agent):
            self.assertIn(chat_style.AUTHORIZATION_SUMMARY_PROMPT.strip(), module.BASE_SYSTEM_PROMPT, module.__name__)
        for module in (cao_agent, coo_agent):
            self.assertNotIn("executive_summary", module.BASE_SYSTEM_PROMPT, module.__name__)

    def test_the_chat_reply_block_asks_for_two_to_four_plain_sentences_and_forbids_claiming_action(self):
        text = chat_style.CHAT_REPLY_PROMPT
        for needle in ("chat_reply", "2 to 4 sentences", "No bullet points", "add nothing new", "Never say or imply that you have done"):
            self.assertIn(needle, text)
        self.assertIn('do not add "chat_reply"', text)  # a cross-validation verdict stays a bare verdict

    def test_the_summary_block_asks_the_three_plain_parts_forbids_code_words_and_forbids_rating_the_risk(self):
        text = chat_style.AUTHORIZATION_SUMMARY_PROMPT
        for needle in ('"purpose"', '"problem"', '"risk"', "25 words or fewer", "NO code words", "BAD:", "GOOD:", "Do NOT rate the risk"):
            self.assertIn(needle, text)

    def test_the_json_example_inside_each_reasoning_prompt_is_unchanged(self):
        from app.prompts import cao_prompt, cmo_prompt, coo_prompt, cto_prompt

        for prompt in (cao_prompt.CAO_REASONING_PROMPT, coo_prompt.COO_REASONING_PROMPT, cto_prompt.CTO_REASONING_PROMPT, cmo_prompt.CMO_REASONING_PROMPT):
            example = json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])
            self.assertNotIn("chat_reply", example)

    def test_the_output_token_budgets_are_unchanged(self):
        self.assertGreaterEqual(cto_agent.DEFAULT_CTO_MAX_TOKENS, 16384)
        self.assertGreaterEqual(cmo_agent.DEFAULT_CMO_MAX_TOKENS, 16384)



class ChatReplyEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(cmo_env().stop)
        self.headers = {"X-Internal-CAO-Key": CAO_KEY}

    def ask(self, payload):
        with mock.patch.object(cao_agent, "_client", fake_client(model_text(payload))):
            return self.client.post("/api/cao", headers=self.headers, json={"message": "q"}).json()

    def test_the_chat_reply_comes_next_to_the_full_response_and_the_score_still_reads_the_response(self):
        body = self.ask(dict(STRATEGIC, chat_reply=CHAT))
        self.assertEqual(body["chat_reply"], CHAT)
        self.assertEqual(body["reply"], body["response"])
        self.assertIn("Recommendation for human review:", body["response"])
        self.assertNotIn(CHAT, body["response"])  # an extra field, not mixed into the audited text
        self.assertFalse(body["needs_review"])

    def test_a_chat_reply_claiming_an_action_is_removed_from_the_api_answer(self):
        body = self.ask(dict(STRATEGIC, chat_reply="I have approved the pilot budget for you."))
        self.assertIsNone(body["chat_reply"])
        self.assertTrue(body["response"])  # the UI falls back to the full response

    def test_an_answer_without_a_chat_reply_has_a_null_one(self):
        self.assertIsNone(self.ask(STRATEGIC)["chat_reply"])



class ExecutiveSummaryTests(unittest.TestCase):
    def cto(self, **overrides):
        raw = {**CHANGE_ANSWER["authorization_request"], "proposed_commands": ["python -m unittest"], **overrides}
        return cto_authorization.normalize_authorization_request(raw)

    def cmo(self, **overrides):
        raw = {**PUBLISH_ANSWER["authorization_request"], **overrides}
        return cmo_authorization.normalize_authorization_request(raw)

    def test_a_good_summary_is_kept_as_three_clean_parts_for_both_agents(self):
        padded = {k: "  " + v + "  " for k, v in SUMMARY.items()}
        for norm in (self.cto, self.cmo):
            request, findings = norm(executive_summary=padded, risk_level="medium")
            self.assertEqual(request["executive_summary"], SUMMARY)
            self.assertFalse(any("executive_summary" in f for f in findings), findings)

    def test_each_part_is_capped_at_a_sentence_end_where_possible(self):
        long = {k: ("This is a sentence. " * 40) for k in SUMMARY}
        request, _ = self.cmo(executive_summary=long)
        for part in request["executive_summary"].values():
            self.assertLessEqual(len(part), plain_summary.MAX_PART_CHARS)
            self.assertTrue(part.endswith("."), part)

    def test_a_missing_summary_is_none_and_is_not_an_error(self):
        for norm in (self.cto, self.cmo):
            request, findings = norm()
            self.assertIsNone(request["executive_summary"])
            self.assertFalse(any("executive_summary" in f for f in findings))

    def test_a_summary_with_a_missing_or_blank_part_or_the_wrong_shape_is_dropped_whole(self):
        bad = [
            {"purpose": SUMMARY["purpose"], "problem": SUMMARY["problem"]},
            dict(SUMMARY, risk="   "),
            dict(SUMMARY, problem=None),
            "One long paragraph that is not three parts.",
            ["a", "b", "c"],
            {},
        ]
        for value in bad:
            for norm in (self.cto, self.cmo):
                request, findings = norm(executive_summary=value)
                self.assertIsNotNone(request)  # the request itself survives
                self.assertIsNone(request["executive_summary"], value)
                self.assertTrue(any("executive_summary" in f for f in findings), value)

    def test_a_summary_that_claims_an_action_is_dropped_with_a_finding_and_the_request_survives(self):
        for norm in (self.cto, self.cmo):
            request, findings = norm(executive_summary=dict(SUMMARY, purpose="I have deployed the change and it is live."))
            self.assertIsNotNone(request)
            self.assertIsNone(request["executive_summary"])
            self.assertTrue(any("claimed an action" in f for f in findings))

    def test_the_cto_summary_with_code_names_is_dropped_the_exact_bad_example_from_the_brief(self):
        bad = dict(SUMMARY, purpose="Implement CouncilSessionLog and CouncilTurnLog tables with JSON fields for conflict matrix and red team findings.")
        request, findings = self.cto(executive_summary=bad)
        self.assertIsNone(request["executive_summary"])
        self.assertTrue(any("not plain language" in f for f in findings))

    def test_each_kind_of_code_artefact_is_caught_for_the_cto(self):
        samples = [
            "Add the `reviewNote` field.", "Add a reviewNote field.", "Update CouncilSessionLog.", "Edit the council_session table.",
            "Change schema.prisma.", "Open frontend/prisma/schema.prisma now.", "Run the migration { now }.", "Call connect() on start.",
        ]
        for sample in samples:
            request, _ = self.cto(executive_summary=dict(SUMMARY, purpose=sample))
            self.assertIsNone(request["executive_summary"], sample)

    def test_the_exact_good_example_from_the_brief_passes_and_ordinary_words_are_not_flagged(self):
        good = {
            "purpose": "Keep a record of past council sessions.",
            "problem": "Right now we cannot look back at earlier council decisions.",
            "risk": "It adds one new field and leaves existing records alone, and it can be undone.",
        }
        request, findings = self.cto(executive_summary=good, risk_level="medium")
        self.assertEqual(request["executive_summary"], good)
        self.assertFalse(any("executive_summary" in f for f in findings), findings)

    def test_the_cmo_may_name_a_brand_without_being_dropped(self):
        ok = dict(SUMMARY, purpose="Post the announcement on LinkedIn tomorrow morning.")
        request, findings = self.cmo(executive_summary=ok)
        self.assertEqual(request["executive_summary"]["purpose"], "Post the announcement on LinkedIn tomorrow morning.")
        self.assertFalse(any("executive_summary" in f for f in findings), findings)

    def test_a_low_risk_claim_next_to_a_higher_computed_level_is_replaced_by_the_computed_level(self):
        for claim in ("Low risk, it is a standard change.", "The risk is low.", "Minimal risk overall.", "There is no risk here."):
            request, findings = self.cto(executive_summary=dict(SUMMARY, risk=claim), risk_level="low", proposed_commands=["git push origin main"])
            self.assertEqual(request["risk_level"], "high", claim)
            self.assertEqual(request["executive_summary"]["risk"], plain_summary.code_risk_sentence("high"), claim)
            self.assertTrue(any("understated the risk" in f for f in findings), claim)

    def test_a_low_risk_claim_is_left_alone_when_the_computed_level_is_low(self):
        text = "Low risk: it changes nothing that exists today."
        request, _ = self.cto(executive_summary=dict(SUMMARY, risk=text), risk_level="low", proposed_commands=["git status"], proposed_code_diff=None)
        self.assertEqual(request["risk_level"], "low")
        self.assertEqual(request["executive_summary"]["risk"], text)

    def test_the_summary_changes_none_of_the_safety_rules(self):
        for norm in (self.cto, self.cmo):
            request, _ = norm(executive_summary=SUMMARY, status="approved", executed=True, requires_human_approval=False)
            self.assertEqual(request["status"], "pending_human_approval")
            self.assertIs(request["executed"], False)
            self.assertIs(request["requires_human_approval"], True)
        request, _ = self.cto(executive_summary=SUMMARY, risk_level="low", proposed_commands=["git push --force origin main"])
        self.assertEqual(request["risk_level"], "critical")  # a friendly summary cannot lower the floor

    def test_a_summary_is_never_enough_to_make_a_request(self):
        raw = {"action_summary": "x", "executive_summary": SUMMARY}
        self.assertIsNone(cto_authorization.normalize_authorization_request(raw)[0])
        self.assertIsNone(cmo_authorization.normalize_authorization_request(raw)[0])

    def test_garbage_never_raises(self):
        for value in (5, True, 3.5, [], {"purpose": 1, "problem": [], "risk": {}}, {"purpose": "x" * 100000, "problem": "y", "risk": "z"}):
            plain_summary.normalize_plain_summary(value, risk_level="high", allow_identifiers=False)

    def test_the_api_models_carry_the_summary_and_older_rows_without_it_still_validate(self):
        from app.api.cmo import CMOAuthorizationRequest
        from app.api.cto import AuthorizationRequest

        old_cto = {k: v for k, v in self.cto()[0].items() if k != "executive_summary"}
        old_cmo = {k: v for k, v in self.cmo()[0].items() if k != "executive_summary"}
        self.assertIsNone(AuthorizationRequest(**old_cto).executive_summary)
        self.assertIsNone(CMOAuthorizationRequest(**old_cmo).executive_summary)
        with_summary = AuthorizationRequest(**self.cto(executive_summary=SUMMARY, risk_level="medium")[0])
        self.assertEqual(with_summary.executive_summary.model_dump(), SUMMARY)


class AuthorizationEndpointSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def test_the_cto_returns_the_summary_inside_the_authorization_request(self):
        self.addCleanup(cmo_env().stop)
        waiting = "A change is waiting for your decision. Nothing has been done yet."
        answer = dict(CHANGE_ANSWER, chat_reply=waiting)
        answer["authorization_request"] = dict(CHANGE_ANSWER["authorization_request"], executive_summary=SUMMARY)
        with mock.patch.object(cto_agent, "_client", fake_client(model_text(answer))):
            body = self.client.post("/api/cto", headers={"X-Internal-CTO-Key": CTO_KEY}, json={"message": "change it"}).json()
        self.assertEqual(body["authorization_request"]["executive_summary"], SUMMARY)
        self.assertEqual(body["authorization_request"]["status"], "pending_human_approval")
        self.assertEqual(body["chat_reply"], waiting)


if __name__ == "__main__":
    unittest.main()


