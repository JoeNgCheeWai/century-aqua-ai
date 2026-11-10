"""
Phase 4 tests: the AI_CMO agent (Chief Marketing & Media Officer).

Parts 1-7 need NO database: the registry, the prompt, the output contract shared with the other executives,
the marketing authorization workflow, POST /api/cmo with a mocked model, key gating, and cross-check.
The last part (CmoAuditRowTests) checks the AgentAuditLog row, including the authorizationRequest column. Like
test_governance_db.py it runs ONLY against a localhost database and is skipped otherwise.

No test here calls a real model. The two requests are the ones from the Phase 4 brief:
  * "Draft a LinkedIn campaign and a 3-minute keynote intro to attract shrimp farm investors to our new RAS
    feasibility tool." (a drafting request: drafts in the response, NOTHING published)
  * a publishing request (which must produce an authorization_request, NOT an executed action).
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
from app.agents import cao_agent, cao_reasoning, cmo_agent, cmo_authorization, coo_agent, cto_agent, registry  # noqa: E402
from app.agents import cross_validation as cv  # noqa: E402
from app.api import security  # noqa: E402
from app.governance import audit  # noqa: E402
from app.governance.rate_limit import limiter  # noqa: E402
from app.prompts import cao_prompt, cmo_prompt, coo_prompt, cto_prompt  # noqa: E402

from tests.test_cao_reasoning import (  # noqa: E402
    DB_READY, LOCAL_URL, _cleanup, _json, _query, _scenario, fake_client, model_text,
)

CMO_KEY = "test-internal-cmo-key"
CAO_KEY = "test-internal-cao-key"
COO_KEY = "test-internal-coo-key"
CTO_KEY = "test-internal-cto-key"
TEST_AGENT = "testbot"

CAMPAIGN_QUERY = (
    "Draft a LinkedIn campaign and a 3-minute keynote intro to attract shrimp farm investors "
    "to our new RAS feasibility tool."
)
PUBLISH_QUERY = "Post the LinkedIn announcement for the RAS feasibility tool tomorrow at 9am."

SHARED_FIELDS = {"response", "reasoning_chain", "scenarios", "pre_consultation_requests"}
NULL_PRE = {"coo_query": None, "cto_query": None}
AUTH_FIELDS = {
    "action_summary", "risk_level", "impact", "proposed_content", "deployment_commands", "rollback_plan",
    "claims_to_verify", "status", "requires_human_approval", "executed",
}

PILLARS = (
    "MARKET INTELLIGENCE AND TREND ANALYSIS", "HIGH-IMPACT CONTENT GENERATION",
    "PRESENTATION AND SPEECH CRAFTING", "VISUAL AND MEDIA DIRECTION",
)

POST = (
    "Most RAS feasibility studies take weeks and a consultant. What if you could test your site in an afternoon?\n"
    "Our new RAS feasibility tool is built for shrimp farm owners weighing a recirculating system. [ADD TOOL FACTS]\n"
    "See how it works: [CAIP LINK]"
)


def cmo_env():
    """All four keys set, no database and no RAG keys, so no test can reach a network service."""
    patcher = mock.patch.dict(
        os.environ,
        {"INTERNAL_CMO_KEY": CMO_KEY, "INTERNAL_CAO_KEY": CAO_KEY, "INTERNAL_COO_KEY": COO_KEY, "INTERNAL_CTO_KEY": CTO_KEY},
    )
    patcher.start()
    for name in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "OPENAI_API_KEY", "PINECONE_INDEX"):
        os.environ.pop(name, None)
    return patcher


# A drafting request: the drafts are IN the response for a human to read, scenarios carry ILLUSTRATIVE engagement
# projections, and authorization_request is null because NOTHING is being published.
CAMPAIGN_ANSWER = {
    "response": (
        "Bottom line: lead with the investor's question, not the tool. Confidence: 62%.\n\n"
        "LinkedIn post, angle A (curiosity):\n" + POST + "\n\n"
        "Keynote intro (about 3 minutes, around 390 words at a common 130 words per minute): [DRAFT SCRIPT, "
        "placeholders in brackets: [INSERT TOOL FACTS], [PARTNER NAME]].\n\n"
        "Claims a human must check: none of the tool's capabilities were given to me, so every capability is a placeholder. "
        "Trend notes are from general knowledge: verify before relying on them.\n\n"
        "Watch-list: industry conference season (emerging, verify before relying on it).\n\n"
        "Recommendation for human review: decide which angle to test first and supply the tool's real facts."
    ),
    "reasoning_chain": [
        "Step 1 (Market Intelligence): investors respond to a sized problem, so open with the cost of a bad RAS decision.",
        "Step 2 (High-Impact Content): a curiosity hook outperforms a feature list for a cold LinkedIn audience (verify).",
        "Step 3 (Presentation and Speech): a keynote intro needs a 20-second hook and one sentence to remember.",
        "Data and sources: the message only; every trend is general knowledge and must be verified.",
        "Assumptions: reach and click-through figures in the scenarios are illustrative assumptions.",
        "Limitations: no analytics, no trend data and no facts about the tool; every claim needs a human to substantiate it.",
    ],
    "scenarios": {
        "optimistic": _scenario("The hook lands; engagement is illustrative: a strong share of the audience clicks through.", "4-6 weeks", 25),
        "baseline": _scenario("Steady interest; illustrative engagement is modest and a few investor conversations start.", "6-10 weeks", 50),
        "pessimistic": _scenario("The message is generic; illustrative engagement is low and no conversations start.", "6-10 weeks", 25),
    },
    "pre_consultation_requests": NULL_PRE,
    "authorization_request": None,
}

# A publishing request. The mock model misbehaves on purpose: it marks the request approved, executed and low
# risk, and its content makes a guarantee and a performance claim. The CODE must override all of that.
PUBLISH_ANSWER = {
    "response": (
        "Bottom line: I cannot publish this myself. I have prepared an authorization request for you to review. "
        "Nothing has been published. Confidence: 70%.\n\n"
        "Recommendation for human review: decide whether to approve the LinkedIn post after checking the claims."
    ),
    "reasoning_chain": [
        "Step 1 (High-Impact Content): a short announcement with one call to action suits LinkedIn.",
        "Step 2 (Authorization): publishing needs a human decision, so I propose rather than post.",
        "Step 3 (Content Discipline): the draft contains claims that need substantiation before it goes public.",
        "Data and sources: the message only.",
        "Assumptions: the posting time is as stated in the message.",
        "Limitations: I cannot post, and the claims in the draft are unverified.",
    ],
    "scenarios": None,
    "pre_consultation_requests": NULL_PRE,
    "authorization_request": {
        "action_summary": "Post the LinkedIn announcement for the RAS feasibility tool tomorrow at 9am.",
        "risk_level": "low",
        "impact": "Visible to the company page's followers; a public claim cannot be fully recalled once shared.",
        "proposed_content": "Our RAS feasibility tool cuts planning time by 60%. We guarantee better survival on every farm. [CAIP LINK]",
        "deployment_commands": ["Open the Century Aqua LinkedIn page", "Schedule the post for tomorrow 09:00 local time"],
        "rollback_plan": "Delete the post from the page; screenshots and shares may remain.",
        "claims_to_verify": [],
        "status": "approved",
        "executed": True,
        "requires_human_approval": False,
    },
}



class RegistryTests(unittest.TestCase):
    def test_cmo_is_registered_and_available_with_its_own_key(self):
        spec = registry.spec("cmo")
        self.assertTrue(spec.available)
        self.assertEqual(spec.key_env, "INTERNAL_CMO_KEY")
        self.assertEqual(spec.answer_path, "app.agents.cmo_agent:get_cmo_answer")
        self.assertIs(registry.load_answer_function("cmo"), cmo_agent.get_cmo_answer)

    def test_the_other_entries_are_untouched_and_all_four_keys_differ(self):
        self.assertEqual(registry.spec("cao").answer_path, "app.agents.cao_agent:get_cao_answer")
        self.assertEqual(registry.spec("coo").answer_path, "app.agents.coo_agent:get_coo_answer")
        self.assertEqual(registry.spec("cto").answer_path, "app.agents.cto_agent:get_cto_answer")
        keys = {registry.spec(a).key_env for a in ("cao", "coo", "cto", "cmo")}
        self.assertEqual(keys, {"INTERNAL_CAO_KEY", "INTERNAL_COO_KEY", "INTERNAL_CTO_KEY", "INTERNAL_CMO_KEY"})

    def test_the_roster_is_exactly_the_four_executives(self):
        self.assertEqual(set(registry.AGENTS), {"cao", "coo", "cto", "cmo"})


class PromptTests(unittest.TestCase):
    def test_constitution_comes_first_then_the_cmo_role_then_the_format(self):
        system = cmo_agent.BASE_SYSTEM_PROMPT
        self.assertTrue(system.startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn(bc.EXECUTIVE_CONSTITUTION.strip(), system)
        self.assertEqual(bc.clause_numbers(), list(range(16)))  # the constitution is untouched
        self.assertLess(system.index("CLAUSE 0:"), system.index(cmo_prompt.CMO_SYSTEM_PROMPT[:80]))
        self.assertLess(system.index("THE FOUR PILLARS"), system.index("AUTHORIZATION WORKFLOW"))
        self.assertLess(system.index("AUTHORIZATION WORKFLOW"), system.index("ADVANCED REASONING AND PREDICTION"))

    def test_the_cmo_is_not_given_the_other_executives_role_prompts_and_they_do_not_get_the_cmos(self):
        self.assertNotIn(cao_prompt.CAO_SYSTEM_PROMPT[:200], cmo_agent.BASE_SYSTEM_PROMPT)
        self.assertNotIn(coo_prompt.COO_SYSTEM_PROMPT[:200], cmo_agent.BASE_SYSTEM_PROMPT)
        self.assertNotIn(cto_prompt.CTO_SYSTEM_PROMPT[:200], cmo_agent.BASE_SYSTEM_PROMPT)
        for other in (cao_agent, coo_agent, cto_agent):
            self.assertNotIn(cmo_prompt.CMO_SYSTEM_PROMPT[:200], other.BASE_SYSTEM_PROMPT)

    def test_all_four_pillars_are_present_numbered_and_in_order(self):
        text = cmo_prompt.CMO_SYSTEM_PROMPT
        positions = []
        for number, name in enumerate(PILLARS, start=1):
            marker = f"\n{number}. {name}"
            self.assertIn(marker, text, name)
            positions.append(text.index(marker))
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("\n5. COMPLIANCE", text)  # exactly four pillars

    def test_each_pillar_covers_the_topics_in_the_brief(self):
        text = cmo_prompt.CMO_SYSTEM_PROMPT.lower()
        for needle in (
            # 1 market intelligence
            "competitor messaging", "platform", "verify before relying on it", "established, emerging or speculative",
            # 2 content
            "scroll-stopping", "email sequences", "ad copy", "call to action", "a/b test", "drive qualified traffic",
            # 3 presentation and speech
            "keynote", "pitch-deck", "webinar", "investors, partners and live industry audiences", "130 words per minute",
            # 4 visual and media direction
            "creative director", "midjourney", "runway", "negative prompt", "storyboard", "shot number", "aspect ratio",
        ):
            self.assertIn(needle, text, needle)


    def test_the_cmo_is_told_it_has_no_tools_no_live_data_and_cannot_publish(self):
        text = cmo_prompt.CMO_SYSTEM_PROMPT
        for needle in (
            "You have NO tools", "You cannot post, send, schedule, launch, spend, publish",
            "You have NO live access to the internet", "Never invent a statistic, a customer, a quote, a testimonial",
            "You cannot generate or see images yourself", "Never ask for, repeat or store a password",
        ):
            self.assertIn(needle, text, needle)

    def test_the_claims_discipline_is_there(self):
        text = cmo_prompt.CMO_SYSTEM_PROMPT
        for needle in (
            "CLAIMS DISCIPLINE", "a guarantee of yield, survival, return or profit", "a cure, treatment or prevention claim",
            "risk-free", "a customer name, logo, quote or testimonial that was not given to you",
            "Investor, partner and regulator material has legal exposure", "legal and compliance review",
            "never imply a promise of returns", "must be labelled as an illustration", "fake reviews",
        ):
            self.assertIn(needle, text, needle)

    def test_the_snd_probiotic_facts_are_identical_to_the_caos(self):
        cao = cao_prompt.CAO_SYSTEM_PROMPT
        facts = cao[cao.index("  - Developed with the National University"): cao.index("- Describe these as Century Aqua / NUS trial results.")]
        self.assertIn(facts, cmo_prompt.CMO_SYSTEM_PROMPT)  # the four fact bullets, character for character
        for needle in ("growout trials are current and ongoing", "preliminary", "Do not call them peer-reviewed",
                       "Disclose that Century Aqua is the developer", "do not let a headline or a visual imply"):
            self.assertIn(needle, cmo_prompt.CMO_SYSTEM_PROMPT, needle)

    def test_the_authorization_workflow_is_mandatory_names_every_field_and_allows_drafting(self):
        text = cmo_prompt.CMO_SYSTEM_PROMPT
        self.assertIn("you MUST output a structured authorization_request", text)
        self.assertIn("INSTEAD of acting", text)
        self.assertIn("You may DRAFT any asset", text)
        self.assertIn("Drafting, advising, reviewing and brainstorming need no authorization_request", text)
        for needle in ("action_summary", "risk_level", "impact", "proposed_content", "deployment_commands", "rollback_plan",
                       "claims_to_verify", "pending_human_approval", '"low", "medium", "high", "critical"',
                       "PUBLISHING content", "LAUNCHING a campaign", "CHANGING public-facing material"):
            self.assertIn(needle, text, needle)

    def test_engagement_metrics_in_scenarios_are_labelled_illustrative(self):
        text = cmo_prompt.CMO_REASONING_PROMPT
        for needle in ("PROJECTED ENGAGEMENT METRICS", "EVERY metric is an illustrative assumption",
                       "Never present a projection as a forecast, a promise or a result"):
            self.assertIn(needle, text, needle)

    def test_the_cmo_has_a_token_budget_and_a_length_budget_in_its_prompt(self):
        self.assertGreaterEqual(cmo_agent.DEFAULT_CMO_MAX_TOKENS, 16384)
        self.assertIn("LENGTH BUDGET", cmo_prompt.CMO_REASONING_PROMPT)
        self.assertIn("COMPLETE", cmo_prompt.CMO_REASONING_PROMPT)



class SharedOutputContractTests(unittest.TestCase):
    """The CMO's first four fields must stay identical in shape to the other executives' (Council Protocol)."""

    @staticmethod
    def _example(prompt):
        return json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])

    def test_the_cmo_example_is_the_cao_example_plus_exactly_authorization_request(self):
        cmo_example = self._example(cmo_prompt.CMO_REASONING_PROMPT)
        cao_example = self._example(cao_prompt.CAO_REASONING_PROMPT)
        cto_example = self._example(cto_prompt.CTO_REASONING_PROMPT)
        self.assertEqual(set(cmo_example), SHARED_FIELDS | {"authorization_request"})
        self.assertIsNone(cmo_example["authorization_request"])
        for field in SHARED_FIELDS:
            self.assertEqual(cmo_example[field], cao_example[field], field)
        self.assertEqual(cmo_example, cto_example)  # the whole object matches the CTO's: same keys, same nulls

    def test_the_cmo_response_model_is_the_cao_response_model_plus_one_field(self):
        from app.api.cao import CAOResponse
        from app.api.cmo import CMOResponse
        from app.api.cto import CTOResponse

        self.assertTrue(issubclass(CMOResponse, CAOResponse))
        self.assertEqual(set(CMOResponse.model_fields) - set(CAOResponse.model_fields), {"authorization_request"})
        self.assertEqual(set(CMOResponse.model_fields), set(CTOResponse.model_fields))  # same top-level shape as the CTO

    def test_the_cmo_authorization_fields_differ_from_the_ctos_because_it_is_content_not_code(self):
        from app.api.cmo import CMOAuthorizationRequest
        from app.api.cto import AuthorizationRequest

        self.assertEqual(set(CMOAuthorizationRequest.model_fields), AUTH_FIELDS)
        shared = {"action_summary", "risk_level", "impact", "rollback_plan", "status", "requires_human_approval", "executed"}
        self.assertTrue(shared <= set(AuthorizationRequest.model_fields))
        self.assertNotIn("proposed_code_diff", CMOAuthorizationRequest.model_fields)
        self.assertNotIn("proposed_content", AuthorizationRequest.model_fields)

    def test_the_cmo_reuses_the_shared_parser_and_pre_consultation_stays_forced_to_null(self):
        self.assertIs(cmo_agent.parse_cao_output, cao_reasoning.parse_cao_output)
        sneaky = dict(CAMPAIGN_ANSWER, pre_consultation_requests={"coo_query": "What budget?", "cto_query": "x"})
        self.assertEqual(cao_reasoning.parse_cao_output(model_text(sneaky)).pre_consultation_requests, NULL_PRE)

    def test_the_shared_parser_ignores_the_extra_authorization_field(self):
        parsed = cao_reasoning.parse_cao_output(model_text(PUBLISH_ANSWER))
        self.assertTrue(parsed.structured)
        self.assertEqual(parsed.findings, [])



class AuthorizationNormaliserTests(unittest.TestCase):
    """The safety rules live in CODE (cmo_authorization.py), not in the prompt, so they hold whatever the model writes."""

    def norm(self, **overrides):
        base = {
            "action_summary": "Post a plain note.", "risk_level": "low", "impact": "Small audience.",
            "proposed_content": "A short friendly note about feeding schedules.",
            "deployment_commands": ["Schedule it on the page"], "rollback_plan": "Delete the post.", "claims_to_verify": [],
        }
        return cmo_authorization.normalize_authorization_request({**base, **overrides})

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

    def test_a_valid_risk_level_is_kept_case_insensitively_but_public_content_is_never_below_medium(self):
        self.assertEqual(self.norm(risk_level=" High ")[0]["risk_level"], "high")
        request, _ = self.norm(risk_level="low", proposed_content="A short friendly note.", deployment_commands=["x"])
        self.assertEqual(request["risk_level"], "medium")  # anything public is at least medium: it cannot be recalled

    def test_the_risk_floor_raises_dangerous_marketing_text(self):
        cases = {
            "We guarantee higher yield on every farm.": "critical",
            "Our probiotic cures vibriosis.": "critical",
            "A risk-free way to farm shrimp.": "critical",
            "Zero mortality in our ponds.": "critical",
            "Send it to the purchased email list.": "critical",
            "A pitch for investors about the RAS tool.": "high",
            "Run paid ads on LinkedIn with a $5,000 budget.": "high",
            "Send to all subscribers on the mailing list.": "high",
            "Our tool is faster than the leading competitor.": "high",
            "Add a customer testimonial.": "high",
            "The world's first commercial sea tilapia strain.": "high",
            "ASC certified farms use it.": "high",
            "SND Probiotic keeps ammonia low.": "high",
            "Update the homepage copy in production.": "high",
        }
        for text, floor in cases.items():
            request, findings = self.norm(risk_level="low", proposed_content=text)
            self.assertEqual(request["risk_level"], floor, text)
            self.assertTrue(any("raised from low" in f for f in findings), text)

    def test_the_floor_reads_the_deployment_steps_and_claims_too_not_only_the_content(self):
        request, _ = self.norm(risk_level="low", proposed_content="Plain note.", deployment_commands=["Run paid ads for 2 weeks"])
        self.assertEqual(request["risk_level"], "high")
        request, _ = self.norm(risk_level="low", proposed_content="Plain note.", claims_to_verify=["We guarantee survival"])
        self.assertEqual(request["risk_level"], "critical")

    def test_the_floor_never_lowers_a_higher_honest_label(self):
        request, _ = self.norm(risk_level="critical", proposed_content="A short friendly note.")
        self.assertEqual(request["risk_level"], "critical")

    def test_a_request_with_nothing_to_review_is_dropped_not_invented(self):
        for raw in ({"action_summary": "x"}, {"action_summary": "x", "proposed_content": "", "deployment_commands": []},
                    {"proposed_content": "text"}, {}, "text", 5, []):
            request, findings = cmo_authorization.normalize_authorization_request(raw)
            self.assertIsNone(request, raw)
            self.assertTrue(findings, raw)
        self.assertEqual(cmo_authorization.normalize_authorization_request(None), (None, []))

    def test_a_missing_impact_gets_a_placeholder_that_says_it_is_unknown(self):
        request, findings = self.norm(impact="")
        self.assertIn("unknown", request["impact"])
        self.assertTrue(any("impact" in f for f in findings))


    def test_the_code_adds_the_claims_it_finds_in_the_draft_when_the_model_listed_none(self):
        request, findings = self.norm(
            proposed_content="Cut your water changes by 40% with our tool. It is the world's first of its kind! Join us.",
            claims_to_verify=[],
        )
        claims = request["claims_to_verify"]
        self.assertTrue(any("40%" in c for c in claims), claims)
        self.assertTrue(any("world's first" in c for c in claims), claims)
        self.assertTrue(all(c.startswith("[detected by the system") for c in claims), claims)
        self.assertTrue(any("factual claim(s) detected" in f for f in findings))

    def test_a_claim_the_model_already_listed_is_not_listed_twice(self):
        request, _ = self.norm(
            proposed_content="Cut your water changes by 40% with our tool.",
            claims_to_verify=["Cut your water changes by 40% with our tool."],
        )
        self.assertEqual(request["claims_to_verify"], ["Cut your water changes by 40% with our tool."])

    def test_plain_copy_with_no_claims_adds_nothing(self):
        request, findings = self.norm(proposed_content="Join our free webinar next week. Bring your questions about pond management.")
        self.assertEqual(request["claims_to_verify"], [])
        self.assertFalse(any("detected" in f for f in findings))

    def test_detect_claims_is_capped_and_never_raises(self):
        many = " ".join(f"Cut costs by {i}%." for i in range(100))
        self.assertLessEqual(len(cmo_authorization.detect_claims(many)), cmo_authorization.MAX_DETECTED_CLAIMS)
        for raw in (None, "", "   ", "x" * 100000):
            cmo_authorization.detect_claims(raw)

    def test_lists_are_split_and_everything_is_capped(self):
        request, _ = self.norm(deployment_commands="Open the page\nSchedule the post\n\n")
        self.assertEqual(request["deployment_commands"], ["Open the page", "Schedule the post"])
        big, _ = self.norm(
            deployment_commands=[f"step {i}" for i in range(100)], proposed_content="x" * 50000,
            claims_to_verify=[f"claim {i}" for i in range(100)],
        )
        self.assertEqual(len(big["deployment_commands"]), cmo_authorization.MAX_COMMANDS)
        self.assertLessEqual(len(big["proposed_content"]), cmo_authorization.MAX_CONTENT_CHARS)
        self.assertLessEqual(len(big["claims_to_verify"]), cmo_authorization.MAX_CLAIMS)

    def test_the_audit_summary_says_nothing_was_published_and_leaves_the_content_to_the_json_column(self):
        request, _ = self.norm(risk_level="high", proposed_content="SECRET-DRAFT-WORDING we guarantee results.")
        summary = cmo_authorization.audit_summary(request)
        self.assertIn("pending human approval", summary)
        self.assertIn("nothing has been published or executed", summary)
        self.assertIn("Risk level: critical", summary)  # raised by the floor from the model's "high"
        self.assertIn("Claims a human must substantiate before publishing", summary)
        self.assertNotIn("SECRET-DRAFT-WORDING", summary)  # the draft itself is in authorizationRequest, not the text
        self.assertEqual(cmo_authorization.audit_summary(None), "")

    def test_it_never_raises_on_garbage(self):
        for raw in (None, "", 0, [], [1], {"action_summary": 5, "proposed_content": 5},
                    {"action_summary": "x", "deployment_commands": [None, {}, 3], "claims_to_verify": 7}):
            cmo_authorization.normalize_authorization_request(raw)



class CmoEndpointTests(unittest.TestCase):
    """POST /api/cmo with a mocked model and no database (audit_id is therefore null)."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(cmo_env().stop)
        self.headers = {"X-Internal-CMO-Key": CMO_KEY, "X-User-Id": "user_staff_1"}

    def ask(self, reply_text, message=CAMPAIGN_QUERY):
        calls = []
        with mock.patch.object(cmo_agent, "_client", fake_client(reply_text, calls)):
            response = self.client.post("/api/cmo", headers=self.headers, json={"message": message})
        return response, calls

    def test_the_campaign_request_returns_the_same_shape_as_the_other_executives_plus_authorization_request(self):
        response, calls = self.ask(model_text(CAMPAIGN_ANSWER))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("response", "reply", "reasoning_chain", "scenarios", "pre_consultation_requests",
                    "authorization_request", "constitutional_score", "needs_review", "audit_id"):
            self.assertIn(key, body)
        self.assertEqual(body["reply"], body["response"])
        self.assertTrue(calls[0]["system"].startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn("THE FOUR PILLARS", calls[0]["system"])
        self.assertEqual(calls[0]["messages"][-1], {"role": "user", "content": CAMPAIGN_QUERY})
        self.assertEqual(calls[0]["model"], cmo_agent.CMO_MODEL)

    def test_a_drafting_request_puts_the_drafts_in_the_response_and_publishes_nothing(self):
        body = self.ask(model_text(CAMPAIGN_ANSWER))[0].json()
        self.assertIsNone(body["authorization_request"])  # drafting needs no authorization
        self.assertIn("Most RAS feasibility studies take weeks", body["response"])  # the LinkedIn draft is readable
        self.assertIn("Keynote intro", body["response"])
        self.assertNotIn("AUTHORIZATION REQUEST", body["response"])
        self.assertIn("Recommendation for human review:", body["response"])

    def test_reasoning_chain_has_three_to_five_pillar_steps_plus_the_three_closing_entries(self):
        chain = self.ask(model_text(CAMPAIGN_ANSWER))[0].json()["reasoning_chain"]
        steps = [s for s in chain if s.startswith("Step ")]
        self.assertTrue(3 <= len(steps) <= 5, chain)
        self.assertEqual([s.split(":")[0] for s in chain[len(steps):]], ["Data and sources", "Assumptions", "Limitations"])
        self.assertTrue(any("Market Intelligence" in s for s in steps))

    def test_three_distinct_scenarios_with_every_field_a_confidence_and_illustrative_engagement(self):
        scenarios = self.ask(model_text(CAMPAIGN_ANSWER))[0].json()["scenarios"]
        self.assertEqual(set(scenarios), {"optimistic", "baseline", "pessimistic"})
        for name, scenario in scenarios.items():
            self.assertEqual(
                set(scenario),
                {"description", "timeline", "resource_requirements", "confidence_percentage", "key_assumptions", "risks"},
                name,
            )
            self.assertIn("illustrative", scenario["description"].lower(), name)  # engagement is never presented as fact
        confidences = {name: s["confidence_percentage"] for name, s in scenarios.items()}
        self.assertEqual(confidences, {"optimistic": 25, "baseline": 50, "pessimistic": 25})
        self.assertEqual(len({s["description"] for s in scenarios.values()}), 3)

    def test_pre_consultation_requests_structure_is_present_and_null(self):
        self.assertEqual(self.ask(model_text(CAMPAIGN_ANSWER))[0].json()["pre_consultation_requests"], NULL_PRE)

    def test_score_is_in_range_and_a_good_answer_does_not_need_review(self):
        body = self.ask(model_text(CAMPAIGN_ANSWER))[0].json()
        self.assertTrue(1 <= body["constitutional_score"] <= 100)
        self.assertFalse(body["needs_review"])

    def test_a_model_that_ignores_the_format_still_gets_a_valid_framed_response(self):
        response, _ = self.ask("Here is a catchy post for you.")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("Here is a catchy post for you.", body["response"])
        self.assertIn("Recommendation for human review:", body["response"])
        self.assertEqual((body["reasoning_chain"], body["scenarios"], body["authorization_request"]), ([], None, None))
        self.assertTrue(body["needs_review"])

    def test_a_model_failure_is_a_502_that_does_not_leak_the_message(self):
        async def boom(**kwargs):
            raise RuntimeError("upstream exploded with SECRET-DETAIL")

        with mock.patch.object(cmo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=boom))):
            response = self.client.post("/api/cmo", headers=self.headers, json={"message": CAMPAIGN_QUERY})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("SECRET-DETAIL", response.text)
        self.assertNotIn(CAMPAIGN_QUERY, response.text)


    # ---- the Authorization Workflow, end to end over HTTP ----

    def test_a_publishing_request_returns_an_authorization_request_not_an_executed_action(self):
        response, _ = self.ask(model_text(PUBLISH_ANSWER), message=PUBLISH_QUERY)
        self.assertEqual(response.status_code, 200, response.text)
        auth = response.json()["authorization_request"]
        self.assertIsNotNone(auth)
        self.assertEqual(set(auth), AUTH_FIELDS)
        self.assertEqual(auth["action_summary"], "Post the LinkedIn announcement for the RAS feasibility tool tomorrow at 9am.")
        self.assertIn("[CAIP LINK]", auth["proposed_content"])  # the content is TEXT for review
        self.assertEqual(auth["deployment_commands"], ["Open the Century Aqua LinkedIn page", "Schedule the post for tomorrow 09:00 local time"])
        self.assertEqual(auth["rollback_plan"], "Delete the post from the page; screenshots and shares may remain.")

    def test_the_request_can_never_claim_to_be_approved_published_or_executed(self):
        # The mock model claimed status "approved", executed true and no approval needed. The code overrides all three.
        auth = self.ask(model_text(PUBLISH_ANSWER), message=PUBLISH_QUERY)[0].json()["authorization_request"]
        self.assertEqual(auth["status"], "pending_human_approval")
        self.assertIs(auth["executed"], False)
        self.assertIs(auth["requires_human_approval"], True)

    def test_a_guarantee_in_the_draft_raises_the_risk_even_though_the_model_said_low(self):
        auth = self.ask(model_text(PUBLISH_ANSWER), message=PUBLISH_QUERY)[0].json()["authorization_request"]
        self.assertEqual(auth["risk_level"], "critical")  # the mock draft says "We guarantee better survival"

    def test_the_claims_the_model_missed_are_added_by_the_system_for_a_human_to_substantiate(self):
        auth = self.ask(model_text(PUBLISH_ANSWER), message=PUBLISH_QUERY)[0].json()["authorization_request"]
        claims = " | ".join(auth["claims_to_verify"])
        self.assertIn("60%", claims)  # "cuts planning time by 60%" was not listed by the model
        self.assertIn("guarantee", claims.lower())
        self.assertTrue(all(c.startswith("[detected by the system") for c in auth["claims_to_verify"]))

    def test_the_response_text_says_nothing_was_published_and_records_the_request_for_the_audit_log(self):
        body = self.ask(model_text(PUBLISH_ANSWER), message=PUBLISH_QUERY)[0].json()
        self.assertIn("AUTHORIZATION REQUEST (pending human approval; nothing has been published or executed)", body["response"])
        self.assertIn("Risk level: critical", body["response"])
        self.assertIn("Claims a human must substantiate before publishing", body["response"])
        self.assertIn("Recommendation for human review:", body["response"])

    def test_no_published_or_sent_claim_leaks_out_as_a_result(self):
        body = self.ask(model_text(PUBLISH_ANSWER), message=PUBLISH_QUERY)[0].json()
        text = body["response"].lower()
        # The disclaimers legitimately say "nothing has been published": remove those denials, then look for a POSITIVE claim.
        for denial in ("nothing has been published or executed", "nothing has been published"):
            text = text.replace(denial, "")
        for claim in ("i have posted", "i have published", "i have scheduled", "i have sent", "i have launched",
                      "has been posted", "has been published", "has been scheduled", "has been sent"):
            self.assertNotIn(claim, text, claim)
        self.assertIsNone(body["scenarios"])  # a single scheduled post is not a strategic question

    def test_the_positive_claim_check_would_catch_a_model_that_says_it_published(self):
        # Proves the check above has teeth: a reply that really claims to have posted must be detectable.
        lying = dict(PUBLISH_ANSWER, response="I have posted it and it has been published. Confidence: 70%.\n\nRecommendation for human review: none.")
        text = self.ask(model_text(lying), message=PUBLISH_QUERY)[0].json()["response"].lower()
        for denial in ("nothing has been published or executed", "nothing has been published"):
            text = text.replace(denial, "")
        self.assertIn("has been published", text)  # the denial-stripping does not hide a real claim

    def test_a_cut_off_publishing_request_is_never_recovered_or_invented(self):
        full = model_text(PUBLISH_ANSWER)
        cut = full[: full.index('"deployment_commands"')]  # cut mid-request, as a token limit would
        result = asyncio.run(_result_with_stop(cut, "max_tokens"))
        self.assertIsNone(result.authorization_request)
        self.assertIn("length limit", result.reply)
        self.assertIn("was NOT recovered", result.reply)
        self.assertNotIn("Schedule the post", result.reply)  # no half-built step surfaces

    def test_a_request_with_nothing_to_review_is_dropped(self):
        empty = dict(PUBLISH_ANSWER, authorization_request={"action_summary": "Post something", "risk_level": "low"})
        body = self.ask(model_text(empty), message=PUBLISH_QUERY)[0].json()
        self.assertIsNone(body["authorization_request"])
        self.assertNotIn("AUTHORIZATION REQUEST", body["response"])

    def test_the_api_contract_refuses_a_request_that_claims_to_be_approved_or_published(self):
        from pydantic import ValidationError

        from app.api.cmo import CMOAuthorizationRequest

        base = dict(action_summary="x", risk_level="low", impact="y")
        for bad in ({"status": "approved"}, {"status": "published"}, {"executed": True},
                    {"requires_human_approval": False}, {"risk_level": "tiny"}):
            with self.assertRaises(ValidationError, msg=bad):
                CMOAuthorizationRequest(**{**base, **bad})
        self.assertEqual(CMOAuthorizationRequest(**base).status, "pending_human_approval")


async def _result_with_stop(text, stop_reason):
    async def create(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)

    with mock.patch.object(cmo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))):
        return await cmo_agent.get_cmo_result("q", use_rag=False, audit=False)



class CmoKeyGatingTests(unittest.TestCase):
    """/api/cmo is gated by INTERNAL_CMO_KEY only, and never calls the model before the key is checked."""

    HEADERS = ("X-Internal-CMO-Key", "X-Internal-CAO-Key", "X-Internal-COO-Key", "X-Internal-CTO-Key")

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.addCleanup(cmo_env().stop)
        self.calls = []
        for agent in (cmo_agent, cao_agent, coo_agent, cto_agent):
            patcher = mock.patch.object(agent, "_client", fake_client(model_text(CAMPAIGN_ANSWER), self.calls))
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, headers=None, path="/api/cmo"):
        return self.client.post(path, json={"message": CAMPAIGN_QUERY}, headers=headers or {})

    def test_no_key_and_a_wrong_key_are_401_and_never_reach_the_model(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.post({"X-Internal-CMO-Key": "wrong"}).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_the_cao_coo_and_cto_keys_do_not_open_the_cmo_in_any_header(self):
        for key in (CAO_KEY, COO_KEY, CTO_KEY):
            for header in self.HEADERS:
                self.assertEqual(self.post({header: key}).status_code, 401, (header, key))
        self.assertEqual(self.calls, [])

    def test_the_cmo_key_does_not_open_any_other_endpoint(self):
        cross = {"agent_a_id": "cao", "agent_b_id": "cmo", "query": "q", "context": "c"}
        for header in self.HEADERS:
            for path in ("/api/cao", "/api/coo", "/api/cto"):
                self.assertEqual(self.post({header: CMO_KEY}, path).status_code, 401, (header, path))
            self.assertEqual(self.client.post("/api/agents/cross-check", json=cross, headers={header: CMO_KEY}).status_code, 401, header)
        reset = self.client.post("/api/admin/agents/cmo/reset", headers={"X-Internal-CMO-Key": CMO_KEY, "X-Admin-Id": "a"})
        self.assertEqual(reset.status_code, 401)  # the kill switch stays on the CAO key
        self.assertEqual(self.calls, [])

    def test_the_correct_key_opens_the_cmo(self):
        self.assertEqual(self.post({"X-Internal-CMO-Key": CMO_KEY}).status_code, 200)

    def test_the_endpoint_is_closed_503_when_the_cmo_key_is_not_configured(self):
        os.environ.pop("INTERNAL_CMO_KEY")
        response = self.post({"X-Internal-CMO-Key": "anything"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(CMO_KEY, response.text)
        self.assertEqual(self.calls, [])

    def test_an_empty_configured_key_does_not_accept_an_empty_header(self):
        os.environ["INTERNAL_CMO_KEY"] = ""
        self.assertEqual(self.post({"X-Internal-CMO-Key": ""}).status_code, 503)

    def test_each_gate_names_its_own_header_and_the_older_gates_are_unchanged(self):
        security.require_internal_key(CAO_KEY)  # the original one-argument call still works
        security.require_coo_key(COO_KEY)
        security.require_cto_key(CTO_KEY)
        security.require_cmo_key(CMO_KEY)
        for gate, header in ((security.require_internal_key, "X-Internal-CAO-Key"), (security.require_coo_key, "X-Internal-COO-Key"),
                             (security.require_cto_key, "X-Internal-CTO-Key"), (security.require_cmo_key, "X-Internal-CMO-Key")):
            with self.assertRaises(Exception) as caught:
                gate("wrong")
            self.assertEqual(caught.exception.status_code, 401)
            self.assertIn(header, caught.exception.detail)

    def test_the_existing_endpoints_still_work_with_their_own_keys(self):
        self.assertEqual(self.post({"X-Internal-CAO-Key": CAO_KEY}, "/api/cao").status_code, 200)
        self.assertEqual(self.post({"X-Internal-COO-Key": COO_KEY}, "/api/coo").status_code, 200)
        self.assertEqual(self.post({"X-Internal-CTO-Key": CTO_KEY}, "/api/cto").status_code, 200)



class CrossCheckWithTheRealCmoTests(unittest.IsolatedAsyncioTestCase):
    """The real CMO can review the other executives through the existing cross-check (brand, message, reputation)."""

    VERDICT = '{"valid": false, "reasoning": "The plan makes a claim that could not be substantiated publicly.", "confidence": 0.7, "constitutional_alignment": "partially_aligned"}'

    def setUp(self):
        self.addCleanup(cmo_env().stop)
        limiter.reset()

    async def test_cao_to_cmo_cross_check_uses_the_real_cmo_function(self):
        calls = []
        with mock.patch.object(cmo_agent, "_client", fake_client(self.VERDICT, calls)):
            result = await cv.cross_check("cao", "cmo", "Launch the strain publicly?", "CAO recommendation text")
        self.assertFalse(result.valid)
        self.assertEqual((result.constitutional_alignment, result.confidence), ("partially_aligned", 0.7))
        self.assertTrue(calls[0]["system"].startswith("EXECUTIVE CONSTITUTION"))
        self.assertIn("CLAIMS DISCIPLINE", calls[0]["system"])
        self.assertNotIn("KNOWLEDGE BASE EXCERPTS", calls[0]["system"])  # RAG is off for a review
        self.assertIn("RECOMMENDATION UNDER REVIEW", calls[0]["messages"][-1]["content"])

    async def test_every_pairing_with_the_cmo_works_in_both_directions(self):
        for other, module in (("coo", coo_agent), ("cto", cto_agent), ("cao", cao_agent)):
            limiter.reset()
            with mock.patch.object(cmo_agent, "_client", fake_client(self.VERDICT)):
                self.assertFalse((await cv.cross_check(other, "cmo", "q", "ctx")).valid, other)
            limiter.reset()
            with mock.patch.object(module, "_client", fake_client(self.VERDICT)):
                self.assertFalse((await cv.cross_check("cmo", other, "q", "ctx")).valid, other)

    async def test_a_cross_check_review_is_not_written_to_the_audit_log_as_an_answer(self):
        with mock.patch.object(cmo_agent, "_client", fake_client(self.VERDICT)), mock.patch.object(
            audit, "record_response", mock.AsyncMock()
        ) as record:
            await cv.cross_check("cao", "cmo", "q", "ctx")
        record.assert_not_awaited()

    async def test_a_verdict_never_creates_an_authorization_request(self):
        result = await _result_with_stop(self.VERDICT, "end_turn")
        self.assertIsNone(result.authorization_request)
        self.assertIn(self.VERDICT, result.reply)



class RecordsToTheAuditLogAsCmoTests(unittest.IsolatedAsyncioTestCase):
    """No database: prove the call is audit.record_response('cmo', ...) with every field."""

    def setUp(self):
        self.addCleanup(cmo_env().stop)

    async def test_get_cmo_result_records_agent_cmo_with_the_structured_fields_and_no_request_for_a_draft(self):
        record = mock.AsyncMock(return_value=(7, mock.Mock()))
        with mock.patch.object(cmo_agent, "_client", fake_client(model_text(CAMPAIGN_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await cmo_agent.get_cmo_result(CAMPAIGN_QUERY, use_rag=False, user_id="user_staff_1")
        self.assertEqual(result.audit_id, 7)
        args, kwargs = record.call_args
        self.assertEqual(args[:3], ("cmo", "user_staff_1", CAMPAIGN_QUERY))
        self.assertEqual(kwargs["reasoning_chain"], CAMPAIGN_ANSWER["reasoning_chain"])
        self.assertEqual(kwargs["scenarios"], CAMPAIGN_ANSWER["scenarios"])
        self.assertEqual(kwargs["pre_consultation_requests"], NULL_PRE)
        self.assertEqual(kwargs["score"].score, result.constitutional_score)
        self.assertIsNone(kwargs["authorization_request"])  # a draft publishes nothing: nothing to store

    async def test_a_publishing_request_passes_the_full_validated_request_to_the_audit_log(self):
        record = mock.AsyncMock(return_value=(9, mock.Mock()))
        with mock.patch.object(cmo_agent, "_client", fake_client(model_text(PUBLISH_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await cmo_agent.get_cmo_result(PUBLISH_QUERY, use_rag=False)
        stored = record.call_args.kwargs["authorization_request"]
        self.assertEqual(stored, result.authorization_request)  # the same object the API returns
        self.assertEqual(set(stored), AUTH_FIELDS)
        self.assertEqual(stored["status"], "pending_human_approval")  # the HARDENED version, not the model's
        self.assertIs(stored["executed"], False)
        self.assertEqual(stored["risk_level"], "critical")
        self.assertIn("We guarantee better survival", stored["proposed_content"])  # the full content, not a summary
        self.assertTrue(stored["claims_to_verify"])

    async def test_a_request_the_code_dropped_and_a_cut_off_request_are_not_stored(self):
        empty = dict(PUBLISH_ANSWER, authorization_request={"action_summary": "Do something", "risk_level": "low"})
        full = model_text(PUBLISH_ANSWER)
        cut = full[: full.index('"deployment_commands"')]
        for reply, stop in ((model_text(empty), "end_turn"), (cut, "max_tokens")):
            record = mock.AsyncMock(return_value=(10, mock.Mock()))

            async def create(reply=reply, stop=stop, **kwargs):
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)], stop_reason=stop)

            with mock.patch.object(cmo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))), mock.patch.object(
                audit, "record_response", record
            ):
                await cmo_agent.get_cmo_result(PUBLISH_QUERY, use_rag=False)
            self.assertIsNone(record.call_args.kwargs["authorization_request"], stop)

    async def test_audit_false_records_nothing(self):
        record = mock.AsyncMock()
        with mock.patch.object(cmo_agent, "_client", fake_client(model_text(CAMPAIGN_ANSWER))), mock.patch.object(
            audit, "record_response", record
        ):
            result = await cmo_agent.get_cmo_result("q", use_rag=False, audit=False)
        record.assert_not_awaited()
        self.assertIsNone(result.audit_id)

    async def test_a_database_failure_never_blocks_the_cmos_answer_or_its_request(self):
        bad_db = {"GOVERNANCE_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x?sslmode=disable"}
        with mock.patch.dict(os.environ, bad_db), mock.patch.object(
            cmo_agent, "_client", fake_client(model_text(PUBLISH_ANSWER))
        ):
            with self.assertLogs("app.governance.audit", level="ERROR") as logs:
                result = await cmo_agent.get_cmo_result("q", use_rag=False)
        self.assertIn("Recommendation for human review", result.reply)
        self.assertIsNotNone(result.authorization_request)  # the request still reaches the human
        self.assertIsNone(result.audit_id)
        self.assertIn("GovernanceDBError", logs.output[0])

    async def test_get_cmo_answer_returns_text_for_cross_validation(self):
        with mock.patch.object(cmo_agent, "_client", fake_client(model_text(CAMPAIGN_ANSWER))):
            reply = await cmo_agent.get_cmo_answer("q", use_rag=False, audit=False)
        self.assertIsInstance(reply, str)
        self.assertIn("Keynote intro", reply)



# ---------------------------------------------------------------------------------------------
# The real AgentAuditLog row. Local database only (same safety rules as test_governance_db.py).
# Rows are written under the dedicated agent id 'testbot' and deleted afterwards, so the local 'cmo' history is
# never touched. The agent id 'cmo' itself is proven by RecordsToTheAuditLogAsCmoTests above.
# ---------------------------------------------------------------------------------------------
@unittest.skipUnless(DB_READY, "needs the local prisma dev database with the Phase 1 migration applied")
class CmoAuditRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": LOCAL_URL, "INTERNAL_CMO_KEY": CMO_KEY}),
            mock.patch.object(cmo_agent, "AGENT_ID", TEST_AGENT),
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

    async def test_the_campaign_answer_is_logged_with_every_field_populated(self):
        with mock.patch.object(cmo_agent, "_client", fake_client(model_text(CAMPAIGN_ANSWER))):
            result = await cmo_agent.get_cmo_result(CAMPAIGN_QUERY, user_id="user_staff_1")
        self.assertIsInstance(result.audit_id, int)

        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        self.assertEqual((row["agentId"], row["userId"], row["query"]), (TEST_AGENT, "user_staff_1", CAMPAIGN_QUERY))
        self.assertIn("Most RAS feasibility studies take weeks", row["response"])  # the drafts are in the stored response
        self.assertIn("Recommendation for human review:", row["response"])
        self.assertEqual(row["constitutionalScore"], result.constitutional_score)
        self.assertEqual(row["humanAction"], "Pending")
        self.assertIsNotNone(row["timestamp"])
        self.assertEqual(list(row["reasoningChain"]), CAMPAIGN_ANSWER["reasoning_chain"])
        self.assertEqual(_json(row["scenarios"]), CAMPAIGN_ANSWER["scenarios"])
        self.assertEqual(_json(row["preConsultationRequests"]), NULL_PRE)
        self.assertIsNone(row["authorizationRequest"])  # SQL NULL: a draft publishes nothing
        self.assertTrue(row["sourcesCited"])

    async def test_a_publishing_request_populates_the_authorization_request_column_with_the_full_request(self):
        with mock.patch.object(cmo_agent, "_client", fake_client(model_text(PUBLISH_ANSWER))):
            result = await cmo_agent.get_cmo_result(PUBLISH_QUERY, user_id="user_staff_1")
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
        stored = _json(row["authorizationRequest"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored, result.authorization_request)  # exactly what the API returns
        self.assertEqual(set(stored), AUTH_FIELDS)  # the CMO's shape (content), not the CTO's (code)
        self.assertEqual(stored["status"], "pending_human_approval")
        self.assertIs(stored["executed"], False)
        self.assertIs(stored["requires_human_approval"], True)
        self.assertEqual(stored["risk_level"], "critical")
        # The FULL content and steps survive the round trip, and so do the system-detected claims.
        self.assertEqual(stored["proposed_content"], PUBLISH_ANSWER["authorization_request"]["proposed_content"])
        self.assertEqual(stored["deployment_commands"], PUBLISH_ANSWER["authorization_request"]["deployment_commands"])
        self.assertTrue(any("60%" in c for c in stored["claims_to_verify"]))
        self.assertIn("AUTHORIZATION REQUEST (pending human approval; nothing has been published or executed)", row["response"])
        self.assertNotIn("We guarantee better survival", row["response"])  # the content lives in the JSON column only
        self.assertEqual(row["humanAction"], "Pending")
        self.assertIsNone(row["scenarios"])  # a single scheduled post is not strategic: SQL NULL


    async def test_the_stored_request_matches_the_api_response_end_to_end(self):
        from app.main import app

        headers = {"X-Internal-CMO-Key": CMO_KEY, "X-User-Id": "user_staff_1"}
        with mock.patch.object(cmo_agent, "_client", fake_client(model_text(PUBLISH_ANSWER))), mock.patch(
            "app.api.cmo.audit.current_epoch", mock.AsyncMock(return_value=0)
        ):
            response = TestClient(app).post("/api/cmo", headers=headers, json={"message": PUBLISH_QUERY})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsInstance(body["audit_id"], int)
        row = _query('SELECT * FROM "AgentAuditLog" WHERE id = %s', (body["audit_id"],))[0]
        self.assertEqual(_json(row["authorizationRequest"]), body["authorization_request"])
        self.assertEqual(list(row["reasoningChain"]), body["reasoning_chain"])
        self.assertEqual(row["constitutionalScore"], body["constitutional_score"])

    async def test_the_column_is_null_for_a_plain_text_answer_a_dropped_request_and_a_cut_off_request(self):
        full = model_text(PUBLISH_ANSWER)
        dropped = model_text(dict(PUBLISH_ANSWER, authorization_request={"action_summary": "x"}))
        for reply, stop in (("Here is a catchy post.", "end_turn"), (dropped, "end_turn"),
                            (full[: full.index('"deployment_commands"')], "max_tokens")):
            async def create(reply=reply, stop=stop, **kwargs):
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)], stop_reason=stop)

            with mock.patch.object(cmo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))):
                result = await cmo_agent.get_cmo_result(PUBLISH_QUERY)
            row = _query('SELECT "authorizationRequest" IS NULL AS is_null FROM "AgentAuditLog" WHERE id = %s', (result.audit_id,))[0]
            self.assertTrue(row["is_null"], reply[:30])

    async def test_the_agent_state_buffer_and_violation_count_work_for_the_cmo_too(self):
        with mock.patch.object(cmo_agent, "_client", fake_client("Here is a catchy post.")):
            await cmo_agent.get_cmo_result(CAMPAIGN_QUERY, user_id="user_staff_1")
        state = audit.snapshot_sync(TEST_AGENT)
        self.assertEqual(len(state["conversationHistory"]), 1)
        self.assertEqual(state["conversationHistory"][0]["query"], CAMPAIGN_QUERY)
        self.assertEqual(state["constitutionalViolations"], 1)  # the plain-text answer scored below 70

    async def test_the_cmo_honours_the_kill_switch_epoch(self):
        from app.main import app

        headers = {"X-Internal-CMO-Key": CMO_KEY, "X-User-Id": "user_staff_1"}
        history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier answer"}]
        seen = []

        async def create(**kwargs):
            seen.append(kwargs["messages"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=model_text(CAMPAIGN_ANSWER))], stop_reason="end_turn")

        with mock.patch.object(cmo_agent, "_client", SimpleNamespace(messages=SimpleNamespace(create=create))), mock.patch(
            "app.api.cmo.audit.current_epoch", mock.AsyncMock(return_value=3)
        ):
            client = TestClient(app)
            fresh = client.post("/api/cmo", headers=headers, json={"message": "now", "history": history, "reset_epoch": 3})
            stale = client.post("/api/cmo", headers=headers, json={"message": "now", "history": history, "reset_epoch": 2})
        self.assertEqual((fresh.json()["history_discarded"], stale.json()["history_discarded"]), (False, True))
        self.assertEqual(len(seen[0]), 3)
        self.assertEqual(seen[1], [{"role": "user", "content": "now"}])  # the cancelled history never reached the model


if __name__ == "__main__":
    unittest.main()

