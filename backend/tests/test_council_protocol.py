"""
Phase 5 tests: the AI Executive Council Protocol. No database and NO real model: a scripted fake model stands in
for Anthropic, and an in-memory store stands in for the council tables.

Covers: happy path, hard-constraint veto, NEEDS_CEO_INPUT, ESCALATED_TO_CEO (loop guard), option-count and red-team
enforcement, authorization non-bypass, the per-turn token cap, the session budget, parallel fan-out, the async
POST/GET pattern, the orphan rule, the concurrency cap, and key separation.
"""

import asyncio
import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import base_constitution as bc  # noqa: E402
from app.agents import council  # noqa: E402
from app.governance import council_store  # noqa: E402

ALL = ["cao", "coo", "cto", "cmo"]
QUERY = "Should we launch the new customer portal this quarter?"


def turn(stance="support", position="Proceed with care.", confidence=70, constraints=None, missing=None,
         action=None, objections=None):
    return json.dumps({
        "stance": stance, "position": position, "hard_constraints": constraints or [], "confidence": confidence,
        "sources": [], "objections": objections or [], "missing_critical_data": missing or [],
        "proposed_action": action,
    })


def option(option_id, **extra):
    base = {"option_id": option_id, "title": f"Option {option_id}", "summary": f"Do thing {option_id}.",
            "supported_by": ["cao"], "dissent_from": [], "unresolved_objection": "", "proposed_action": None}
    return {**base, **extra}


def synthesis(n=3, **extra):
    return json.dumps({"options": [option("ABCD"[i], **extra) for i in range(n)]})


def red_team(ids=("A", "B", "C"), mode="Vendor delay pushes the launch past the budget window."):
    return json.dumps({"findings": [{"option_id": i, "most_likely_failure_mode": mode} for i in ids]})


def moderation(conflicts=None, vetoes=None, revise=None, critical=None, on_topic=True):
    """``critical`` is the COO's critical_data_required: the ONLY thing that can halt a session for missing data."""
    return json.dumps({"on_topic": on_topic, "conflicts": conflicts or [], "vetoes": vetoes or [],
                       "critical_data_required": critical or [], "revise": revise or [], "notes": "Chair notes."})


class FakeModel:
    """Scripted stand-in for AsyncAnthropic. ``turns`` maps agent id to replies (the last one repeats)."""

    def __init__(self, turns=None, mod=None, synth=None, red=None, delay=0.0, text_override=None, usage=(50, 50),
                 thinking_only=False, stop_reason="end_turn"):
        self.turns = {a: list(v if isinstance(v, list) else [v]) for a, v in (turns or {}).items()}
        self.mod, self.synth, self.red = mod or moderation(), synth or synthesis(), red or red_team()
        self.delay, self.text_override, self.usage = delay, text_override, usage
        # thinking_only: behave like claude-sonnet-5-5 with thinking ON, which spent the whole cap on an empty
        # thinking block and returned no text (the first live run). stop_reason="max_tokens" simulates a cut-off reply.
        self.thinking_only, self.stop_reason = thinking_only, stop_reason
        self.calls, self.in_flight, self.max_in_flight = [], 0, 0
        self.messages = SimpleNamespace(create=self._create)

    def _kind(self, user):
        if "You are chairing" in user:
            return "moderation"
        if "synthesizing for the human CEO" in user:
            return "synthesis"
        if "RED TEAM" in user:
            return "red_team"
        return "revision" if "THE CHAIR ASKS YOU" in user else "turn"

    async def _create(self, **kw):
        user, system = kw["messages"][0]["content"], kw["system"]
        kind = self._kind(user)
        agent = next((a for a in ALL if f"You are AI_{a.upper()}" in system), "?")
        self.calls.append({"kind": kind, "agent": agent, "max_tokens": kw["max_tokens"], "system": system,
                           "thinking": kw.get("thinking", "<not sent>")})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        if self.text_override is not None:
            text = self.text_override
        elif kind == "moderation":
            text = self.mod
        elif kind == "synthesis":
            text = self.synth
        elif kind == "red_team":
            text = self.red
        else:
            queue = self.turns.get(agent) or [turn()]
            text = queue.pop(0) if len(queue) > 1 else queue[0]
        if self.thinking_only and kw.get("thinking") != {"type": "between_tools"}:
            # Exactly what the real API returned: one EMPTY thinking block, all of max_tokens spent thinking.
            return SimpleNamespace(
                content=[SimpleNamespace(type="thinking", thinking="")], stop_reason="max_tokens",
                usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=kw["max_tokens"],
                                      output_tokens_details=SimpleNamespace(thinking_tokens=kw["max_tokens"])))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1],
                                                     output_tokens_details=SimpleNamespace(thinking_tokens=0)))

    def kinds(self):
        return [c["kind"] for c in self.calls]


async def run(model, agents=ALL, query=QUERY):
    with mock.patch.object(council, "_client", model):
        return await council.run_council_session(query, agents)


class Clause17Tests(unittest.TestCase):
    def test_clauses_0_to_15_untouched_and_17_only_in_council_prompts(self):
        self.assertEqual(bc.clause_numbers(), list(range(16)))
        self.assertNotIn("CLAUSE 17", bc.EXECUTIVE_CONSTITUTION)
        solo, with_council = bc.build_system_prompt("ROLE"), bc.build_system_prompt("ROLE", include_council=True)
        self.assertNotIn("CLAUSE 17", solo)
        self.assertIn("CLAUSE 17: COUNCIL PROTOCOL & COLLECTIVE REASONING", with_council)
        self.assertLess(with_council.index("CLAUSE 17"), with_council.index("ROLE"))

    def test_clause_17_states_all_eight_rules(self):
        for phrase in ("NO IMPASSE", "SOLUTION OVER EGO", "COO MODERATION", "HARD CONSTRAINTS VETO SOFT PREFERENCES",
                       "MISSING DATA IS NOT A COMPROMISE", "STRANGE BEHAVIOUR ESCALATES", "RED TEAM IS MANDATORY",
                       "NO AUTHORIZATION BYPASS"):
            self.assertIn(phrase, bc.COUNCIL_CLAUSE_17)


class HappyPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_agents_conflict_moderation_two_to_three_red_teamed_options_resolved(self):
        model = FakeModel(
            turns={"cmo": [turn("support", "Launch now."), turn("conditional", "Stage the launch.")],
                   "cto": turn("oppose", "Not ready: unpatched auth.")},
            mod=moderation(conflicts=[{"topic": "launch timing", "agents": ["cmo", "cto"], "positions": "now vs later",
                                       "resolved": False}], revise=[{"agent_id": "cmo", "question": "Can you stage it?"}]),
        )
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertTrue(2 <= len(result["options"]) <= 3)
        for opt in result["options"]:
            self.assertTrue(opt["most_likely_failure_mode"])
        self.assertEqual(len(result["red_team_findings"]), len(result["options"]))
        self.assertEqual(model.kinds()[:4], ["turn"] * 4)
        self.assertIn("moderation", model.kinds())
        self.assertEqual(model.kinds()[-2:], ["synthesis", "red_team"])
        self.assertLessEqual(result["model_calls"], 10)
        self.assertFalse(result["executed"])

    async def test_each_call_carries_clause_17_and_a_compact_prompt(self):
        model = FakeModel()
        await run(model)
        for call in model.calls:
            self.assertIn("CLAUSE 17", call["system"])
            self.assertLess(len(call["system"]) // 4, 2500)  # compact role card, not a 5-7k token solo prompt

    async def test_dissent_is_preserved_and_labelled(self):
        model = FakeModel(synth=synthesis(2, dissent_from=["cmo"], unresolved_objection="CMO wants an earlier date"),
                          red=red_team(("A", "B")))
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED")
        self.assertTrue(all(o["unresolved_objection"] for o in result["options"]))
        self.assertTrue(result["dissent_record"])



class HardConstraintVetoTests(unittest.IsolatedAsyncioTestCase):
    async def test_cmo_preference_overridden_by_cto_security_floor_and_debate_closed(self):
        floor = [{"constraint": "Passwords must be hashed (security floor)", "source": "company security policy"}]
        model = FakeModel(
            turns={"cto": turn("oppose", "Cannot ship plaintext login.", constraints=floor),
                   "cmo": turn("support", "Ship the one-click login, it looks better.")},
            mod=moderation(
                conflicts=[{"topic": "login design", "agents": ["cto", "cmo"], "positions": "security vs polish", "resolved": False}],
                vetoes=[{"agent_id": "cto", "constraint": floor[0]["constraint"], "source": "company security policy",
                         "overrides": ["cmo"]}],
                revise=[{"agent_id": "cmo", "question": "Still want it?"}]),
        )
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertEqual(result["hard_constraint_vetoes"][0]["agent_id"], "cto")
        self.assertIn("cmo", result["hard_constraint_vetoes"][0]["overrides"])
        self.assertNotIn("revision", model.kinds())  # debate closed: a vetoed topic is never re-argued
        self.assertTrue(any("overruled_by_veto" in d for d in result["dissent_record"]))

    async def test_a_preference_without_a_named_hard_constraint_is_not_a_veto(self):
        model = FakeModel(mod=moderation(vetoes=[{"agent_id": "cmo", "constraint": "", "source": "", "overrides": ["cto"]}]))
        self.assertEqual((await run(model))["hard_constraint_vetoes"], [])

    async def test_an_agent_that_stated_no_hard_constraint_cannot_be_given_a_veto(self):
        model = FakeModel(mod=moderation(vetoes=[{"agent_id": "cmo", "constraint": "tone", "source": "taste", "overrides": ["cto"]}]))
        self.assertEqual((await run(model))["hard_constraint_vetoes"], [])


BUDGET_Q = "What is the approved budget cap in USD?"
LICENCE_Q = "Which regulator's licence applies?"


class NeedsCeoInputTests(unittest.IsolatedAsyncioTestCase):
    """Option B: agents only FLAG questions; the COO's critical_data_required is the ONLY thing that halts."""

    async def test_the_coo_confirming_a_flagged_question_as_critical_halts_with_that_question_and_no_synthesis(self):
        model = FakeModel(turns={"coo": turn("conditional", "Cannot size this.", missing=[BUDGET_Q])},
                          mod=moderation(critical=[BUDGET_Q]))  # the mock COO EXPLICITLY confirms it is critical
        result = await run(model)
        self.assertEqual(result["status"], "NEEDS_CEO_INPUT")
        self.assertEqual(result["ceo_questions"], [BUDGET_Q])
        self.assertIsNone(result["options"])
        self.assertIn("COO ruled", result["escalation_reason"])
        self.assertIn("moderation", model.kinds())  # the chair was consulted: it is the COO who halts
        for banned in ("synthesis", "red_team"):
            self.assertNotIn(banned, model.kinds())

    async def test_the_halt_questions_are_exactly_the_coos_list_not_the_agents_raw_flags(self):
        model = FakeModel(
            turns={"coo": turn("conditional", "x", missing=[BUDGET_Q, "Which colour logo?"]),
                   "cmo": turn("conditional", "y", missing=[BUDGET_Q, "Which fonts?"])},
            mod=moderation(critical=["What hard budget cap applies to the pilot, in USD?"]))
        result = await run(model)
        self.assertEqual(result["status"], "NEEDS_CEO_INPUT")
        self.assertEqual(result["ceo_questions"], ["What hard budget cap applies to the pilot, in USD?"])  # consolidated
        self.assertNotIn("Which colour logo?", result["ceo_questions"])  # an agent's flag is not the CEO's question
        self.assertEqual(len(result["agent_flagged_questions"]), 3)  # the record keeps every flag, de-duplicated

    async def test_the_coos_critical_list_is_capped_at_three(self):
        model = FakeModel(mod=moderation(critical=[f"Question {i}?" for i in range(8)]))
        self.assertEqual(len((await run(model))["ceo_questions"]), council.MAX_CRITICAL_QUESTIONS)

    async def test_agents_flagging_questions_does_not_halt_when_the_coo_rules_none_critical(self):
        flags = {a: turn("conditional", f"{a} position", missing=[BUDGET_Q, LICENCE_Q]) for a in ("cao", "coo", "cmo")}
        model = FakeModel(turns=flags, mod=moderation(critical=[]))  # three agents flag, the chair says "proceed"
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertEqual(result["ceo_questions"], [])
        self.assertIn("synthesis", model.kinds())
        self.assertIn("red_team", model.kinds())
        self.assertEqual(result["open_questions"], [BUDGET_Q, LICENCE_Q])  # carried forward, de-duplicated
        self.assertEqual(result["agent_flagged_questions"], [BUDGET_Q, LICENCE_Q])

    async def test_non_critical_flags_reach_the_cao_synthesis_as_open_questions_to_state_as_assumptions(self):
        seen = {}

        class Spy(FakeModel):
            async def _create(self, **kw):
                if "synthesizing for the human CEO" in kw["messages"][0]["content"]:
                    seen["synthesis_prompt"] = kw["messages"][0]["content"]
                return await super()._create(**kw)

        model = Spy(turns={"cmo": turn("conditional", "y", missing=[BUDGET_Q])}, mod=moderation(critical=[]),
                    synth=synthesis(2, assumptions=["Assumes a USD 50k pilot budget (assumption)."]),
                    red=red_team(("A", "B")))
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED")
        self.assertIn(BUDGET_Q, seen["synthesis_prompt"])
        self.assertIn("assumption", seen["synthesis_prompt"])
        for opt in result["options"]:
            self.assertEqual(opt["assumptions"], ["Assumes a USD 50k pilot budget (assumption)."])

    async def test_the_coo_is_shown_every_agent_flag_so_it_can_rule_and_consolidate_them(self):
        seen = {}

        class Spy(FakeModel):
            async def _create(self, **kw):
                if "You are chairing" in kw["messages"][0]["content"]:
                    seen["moderation_prompt"] = kw["messages"][0]["content"]
                return await super()._create(**kw)

        model = Spy(turns={"cao": turn("conditional", "a", missing=[BUDGET_Q]),
                           "cmo": turn("conditional", "b", missing=[BUDGET_Q, LICENCE_Q])})
        await run(model)
        prompt = seen["moderation_prompt"]
        self.assertIn(BUDGET_Q, prompt)
        self.assertIn(LICENCE_Q, prompt)
        self.assertEqual(prompt.count(BUDGET_Q), 1)  # de-duplicated before the chair sees it
        self.assertIn("critical_data_required", prompt)
        self.assertIn("YOU alone decide", prompt)

    def test_flags_are_collected_in_order_without_duplicates(self):
        turns = [{"missing_critical_data": ["b", "a"]}, {"missing_critical_data": ["a", "c"]}, {}]
        self.assertEqual(council.collect_flags(turns), ["b", "a", "c"])

    async def test_an_agent_flag_alone_can_never_halt_even_from_the_coo_agent_itself(self):
        # The COO's PROPOSAL turn flags a question, but its moderation ruling says nothing is critical.
        model = FakeModel(turns={"coo": turn("conditional", "x", missing=[BUDGET_Q])}, mod=moderation(critical=[]))
        self.assertEqual((await run(model))["status"], "RESOLVED")

    async def test_a_halt_before_the_coos_ruling_is_impossible_so_no_agent_flag_stops_round_two(self):
        model = FakeModel(turns={a: turn("conditional", "x", missing=[BUDGET_Q]) for a in ("cao", "coo")})
        await run(model)
        self.assertIn("moderation", model.kinds())

    async def test_a_new_flag_raised_during_a_revision_becomes_an_open_question_and_cannot_halt(self):
        model = FakeModel(
            turns={"cmo": [turn("support", "Launch now."), turn("conditional", "Staged.", missing=["Which launch date?"])]},
            mod=moderation(conflicts=[{"topic": "launch timing", "agents": ["cmo", "cto"], "positions": "p", "resolved": False}],
                           revise=[{"agent_id": "cmo", "question": "Can you stage it?"}]))
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertIn("Which launch date?", result["open_questions"])



STUCK = [{"topic": "launch timing", "agents": ["cmo", "cto"], "positions": "now vs later", "resolved": False}]
ASK_CMO = [{"agent_id": "cmo", "question": "Will you move?"}]


class EscalationTests(unittest.IsolatedAsyncioTestCase):
    async def test_forced_contradiction_loop_halts_at_two_with_no_infinite_loop(self):
        # The CMO keeps the same stance every time it is asked, so the conflict never converges.
        model = FakeModel(turns={"cmo": turn("oppose", "No.")}, mod=moderation(conflicts=STUCK, revise=ASK_CMO * 5))
        result = await asyncio.wait_for(run(model), timeout=5)
        self.assertEqual(result["status"], "ESCALATED_TO_CEO")
        self.assertIn("circular contradiction", result["escalation_reason"])
        self.assertEqual(model.kinds().count("revision"), council.MAX_LOOPS)  # halts at 2, never a third
        self.assertNotIn("synthesis", model.kinds())
        self.assertIsNone(result["options"])
        self.assertLessEqual(result["model_calls"], council.MAX_CALLS)

    async def test_off_topic_drift_escalates(self):
        result = await run(FakeModel(mod=moderation(on_topic=False)))
        self.assertEqual(result["status"], "ESCALATED_TO_CEO")
        self.assertIn("off-topic", result["escalation_reason"])

    async def test_confidence_collapse_in_round_one_escalates(self):
        result = await run(FakeModel(turns={"cto": turn("conditional", "I do not know.", confidence=5)}))
        self.assertEqual(result["status"], "ESCALATED_TO_CEO")
        self.assertIn("confidence collapse", result["escalation_reason"])

    async def test_confidence_falling_sharply_during_a_revision_escalates(self):
        model = FakeModel(turns={"cmo": [turn("oppose", "No.", confidence=90), turn("conditional", "Hmm.", confidence=40)]},
                          mod=moderation(conflicts=STUCK, revise=ASK_CMO))
        result = await run(model)
        self.assertEqual(result["status"], "ESCALATED_TO_CEO")
        self.assertIn("confidence", result["escalation_reason"])


class OptionCountTests(unittest.IsolatedAsyncioTestCase):
    def test_validate_options_accepts_two_and_three_only(self):
        self.assertEqual(len(council.validate_options([option("A"), option("B")])), 2)
        self.assertEqual(len(council.validate_options([option("A"), option("B"), option("C")])), 3)
        for bad in ([], [option("A")], [option(i) for i in "ABCD"], None, "x"):
            with self.assertRaises(council.CouncilRuleViolation):
                council.validate_options(bad)

    async def test_one_option_is_rejected_and_the_session_does_not_resolve(self):
        result = await run(FakeModel(synth=synthesis(1)))
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("1 options", result["escalation_reason"])
        self.assertIsNone(result["options"])

    async def test_four_options_are_rejected_and_the_session_does_not_resolve(self):
        result = await run(FakeModel(synth=synthesis(4), red=red_team(tuple("ABCD"))))
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("4 options", result["escalation_reason"])
        self.assertIsNone(result["options"])


class RedTeamTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_option_with_an_empty_failure_mode_is_rejected(self):
        bad = json.dumps({"findings": [{"option_id": "A", "most_likely_failure_mode": "Supplier slips."},
                                       {"option_id": "B", "most_likely_failure_mode": ""},
                                       {"option_id": "C", "most_likely_failure_mode": "Budget overrun hits."}]})
        result = await run(FakeModel(red=bad))
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("no red-team failure mode", result["escalation_reason"])
        self.assertIsNone(result["options"])

    async def test_a_missing_finding_or_a_token_answer_is_rejected(self):
        for findings in ([{"option_id": "A", "most_likely_failure_mode": "Supplier slips badly."}],
                         [{"option_id": i, "most_likely_failure_mode": "n/a"} for i in "ABC"]):
            result = await run(FakeModel(red=json.dumps({"findings": findings})))
            self.assertEqual(result["status"], "FAILED")

    async def test_every_resolved_option_carries_the_coo_failure_mode(self):
        result = await run(FakeModel())
        self.assertTrue(all(o["most_likely_failure_mode"] for o in result["options"]))



LYING_ACTION = {"action_summary": "Deploy the new login to production", "status": "approved", "executed": True,
                "requires_human_approval": False}


class AuthorizationNonBypassTests(unittest.IsolatedAsyncioTestCase):
    async def test_council_proposed_code_change_is_pending_and_not_executed(self):
        # The mock model CLAIMS the action is approved and executed. The code must override that.
        model = FakeModel(turns={"cto": turn("support", "Ship it.", action=LYING_ACTION)},
                          synth=synthesis(2, proposed_action=LYING_ACTION), red=red_team(("A", "B")))
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED")
        self.assertFalse(result["executed"])
        self.assertTrue(result["requires_human_approval"])
        proposed = [o["proposed_action"] for o in result["options"]]
        self.assertTrue(all(p is not None for p in proposed))
        for action in proposed:
            self.assertEqual(action["status"], "pending_human_approval")
            self.assertIs(action["executed"], False)
            self.assertIs(action["requires_human_approval"], True)

    def test_force_pending_overrides_whatever_the_model_wrote(self):
        out = council.force_pending(LYING_ACTION)
        self.assertEqual((out["status"], out["executed"], out["requires_human_approval"]),
                         ("pending_human_approval", False, True))
        self.assertIsNone(council.force_pending({"status": "approved"}))  # no summary: no action at all
        self.assertIsNone(council.force_pending("deploy now"))

    def test_the_council_module_has_no_execution_tools(self):
        source = (BACKEND_DIR / "app" / "agents" / "council.py").read_text(encoding="utf-8")
        for banned in ("subprocess", "os.system", "exec(", "eval(", "tools="):
            self.assertNotIn(banned, source)
        self.assertEqual(council.build_result(council.Session("x", "q", ["cao", "coo"], "r"))["executed"], False)


class TurnCapTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_is_cut_in_code_even_when_the_model_returns_5000_tokens(self):
        long_text = "x" * (5000 * council.CHARS_PER_TOKEN)  # ~5000 tokens
        budget = council.Budget()
        with mock.patch.object(council, "_client", FakeModel(text_override=long_text, usage=(100, 5000))):
            reply = await council.call_model("sys", "user", max_tokens=council.TURN_MAX_TOKENS, budget=budget)
        self.assertTrue(reply.truncated)
        self.assertLessEqual(len(reply.text), council.TURN_MAX_TOKENS * council.CHARS_PER_TOKEN)
        self.assertEqual(reply.output_tokens, council.TURN_MAX_TOKENS)
        self.assertEqual(budget.output_tokens, council.TURN_MAX_TOKENS)  # charged at the cap, not 5000

    async def test_the_api_cap_is_600_for_turns_and_1200_for_chair_turns_not_the_solo_16384(self):
        model = FakeModel()
        await run(model)
        for call in model.calls:
            expected = council.TURN_MAX_TOKENS if call["kind"] in ("turn", "revision") else council.CHAIR_MAX_TOKENS
            self.assertEqual(call["max_tokens"], expected)
            self.assertLess(call["max_tokens"], 16384)
        self.assertEqual((council.TURN_MAX_TOKENS, council.CHAIR_MAX_TOKENS), (600, 1200))

    async def test_a_session_whose_model_always_returns_5000_tokens_cannot_resolve_and_stays_bounded(self):
        model = FakeModel(text_override="y" * 20000, usage=(100, 5000))
        result = await run(model)
        self.assertIn(result["status"], ("FAILED", "ESCALATED_TO_CEO"))
        self.assertLessEqual(len(model.calls), council.MAX_CALLS)
        self.assertLessEqual(result["output_tokens"], council.MAX_OUTPUT_TOKENS)



class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_budget_refuses_the_eleventh_call_and_never_overspends(self):
        b = council.Budget()
        for _ in range(10):
            b.reserve()
        with self.assertRaises(council.BudgetExceeded):
            b.reserve()
        self.assertEqual(b.calls, 10)

    def test_budget_keeps_the_reserved_calls_for_synthesis_and_red_team(self):
        b = council.Budget(calls=8)
        with self.assertRaises(council.BudgetExceeded):
            b.reserve(1, keep=council.RESERVED_CALLS)  # a revision may not eat the last two calls
        self.assertEqual(b.calls, 8)

    def test_input_and_output_tokens_are_capped_separately(self):
        b = council.Budget(max_input=1000, max_output=100)
        with self.assertRaises(council.BudgetExceeded):
            b.reserve(1, estimated_input=1001)
        b.charge(10, 100)
        with self.assertRaises(council.BudgetExceeded):
            b.reserve(1)

    async def test_a_revision_that_would_exceed_the_budget_halts_with_failed(self):
        model = FakeModel(turns={"cmo": turn("oppose", "No.")}, mod=moderation(conflicts=STUCK, revise=ASK_CMO * 3))
        # A 7-call ceiling: round 1 (4) + moderation (1) = 5 calls. A revision would be the 6th, and the 2 calls
        # reserved for synthesis and red team would then pass 7 (5 + 1 + 2 = 8), so the revision is refused.
        with mock.patch.object(council, "MAX_CALLS", 7):
            result = await run(model)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("budget", result["escalation_reason"])
        self.assertEqual(model.kinds().count("revision"), 0)
        self.assertLessEqual(result["model_calls"], 7)
        self.assertIsNone(result["options"])

    async def test_a_full_four_agent_session_stays_inside_ten_calls_and_the_token_caps(self):
        model = FakeModel(turns={"cmo": turn("oppose", "No.")}, mod=moderation(conflicts=STUCK, revise=ASK_CMO * 3))
        result = await run(model)
        self.assertLessEqual(len(model.calls), 10)
        self.assertLessEqual(result["model_calls"], 10)
        self.assertLessEqual(result["input_tokens"], council.MAX_INPUT_TOKENS)
        self.assertLessEqual(result["output_tokens"], council.MAX_OUTPUT_TOKENS)

    async def test_a_failed_session_is_logged_not_silent(self):
        with self.assertLogs("app.agents.council", level="ERROR") as logs:
            result = await run(FakeModel(synth=synthesis(1)))
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(any("FAILED" in line for line in logs.output))


class ParallelFanOutTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_one_takes_about_one_call_of_wall_clock_not_four(self):
        delay = 0.4
        model = FakeModel(delay=delay)
        with mock.patch.object(council, "_client", model):
            session = council.Session("s", QUERY, ALL, "r")
            started = time.perf_counter()
            turns = await council._round1_proposals(session)
            elapsed = time.perf_counter() - started
        self.assertEqual(len(turns), 4)
        self.assertEqual(model.max_in_flight, 4)  # all four were in flight at the same moment
        self.assertLess(elapsed, delay * 2)  # ~1x a single call
        self.assertLess(elapsed, delay * 4 * 0.6)  # clearly not the 4x of a sequential loop
        self.assertGreaterEqual(elapsed, delay)

    async def test_round_one_uses_gather_not_a_sequential_loop(self):
        source = (BACKEND_DIR / "app" / "agents" / "council.py").read_text(encoding="utf-8")
        self.assertIn("asyncio.gather(*(_agent_turn(s, a, 1, user) for a in s.agents)", source)

    async def test_one_agent_failing_in_round_one_degrades_instead_of_failing_the_session(self):
        class Flaky(FakeModel):
            async def _create(self, **kw):
                if "AI_CMO" in kw["system"] and "COUNCIL TURN" in kw["messages"][0]["content"]:
                    raise RuntimeError("upstream exploded")
                return await super()._create(**kw)

        result = await run(Flaky())
        self.assertEqual(result["status"], "RESOLVED")
        self.assertNotIn("cmo", [t["agent_id"] for t in result["turns"] if t["round"] == 1])

    async def test_too_few_usable_proposals_fails(self):
        result = await run(FakeModel(text_override="not json at all"))
        self.assertEqual(result["status"], "FAILED")


class ThinkingRegressionTests(unittest.IsolatedAsyncioTestCase):
    """
    The first LIVE run failed because claude-sonnet-5-5 thinks by default and thinking tokens count against
    max_tokens: all 600 went on one empty thinking block and every agent returned NO TEXT. The mock tests had
    returned clean JSON, so they could not see it. These tests make that failure mode visible and permanent.
    """

    async def test_every_council_call_turns_thinking_off(self):
        model = FakeModel()
        await run(model)
        self.assertTrue(model.calls)
        for call in model.calls:
            self.assertEqual(call["thinking"], {"type": "between_tools"}, call["kind"])  # NOT {"type": "disabled"}

    async def test_the_thinking_parameter_is_never_the_rejected_disabled_value(self):
        self.assertNotEqual(council.THINKING_MODE, "disabled")  # claude-sonnet-5-5 answers 400 to "disabled"

    async def test_thinking_can_be_omitted_by_configuration_for_a_model_that_takes_no_parameter(self):
        model = FakeModel()
        with mock.patch.object(council, "THINKING_MODE", "omit"):
            await run(model)
        self.assertTrue(all(call["thinking"] == "<not sent>" for call in model.calls))

    async def test_a_thinking_only_reply_is_a_clear_failed_session_not_a_silent_empty_one(self):
        # The model is simulated with thinking ON, which is what the live run hit: no text, all tokens spent.
        model = FakeModel(thinking_only=True)
        with mock.patch.object(council, "THINKING_MODE", "adaptive"), self.assertLogs("app.agents.council", level="ERROR") as logs:
            result = await run(model)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("0 usable proposals", result["escalation_reason"])
        self.assertIsNone(result["options"])
        joined = " ".join(logs.output)
        self.assertIn("returned no text", joined)
        self.assertIn("thinking_tokens=600", joined)  # the log says WHY: the cap went on hidden reasoning
        self.assertIn("stop_reason=max_tokens", joined)

    async def test_with_thinking_off_the_same_model_resolves(self):
        result = await run(FakeModel(thinking_only=True))  # the default THINKING_MODE turns thinking off
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])

    async def test_call_model_reports_the_stop_reason_and_thinking_tokens(self):
        budget = council.Budget()
        with mock.patch.object(council, "THINKING_MODE", "adaptive"), mock.patch.object(
            council, "_client", FakeModel(thinking_only=True)
        ), self.assertLogs("app.agents.council", level="ERROR"):
            reply = await council.call_model("sys", "user", max_tokens=600, budget=budget)
        self.assertEqual((reply.text, reply.stop_reason, reply.thinking_tokens), ("", "max_tokens", 600))
        self.assertTrue(reply.truncated)

    async def test_a_reply_cut_off_at_the_cap_is_flagged_truncated_and_does_not_parse_as_a_proposal(self):
        cut = '{"stance": "conditional", "position": "Sound if scoped.", "confidence": 62, "objections": [{"to": "cm'
        with mock.patch.object(council, "_client", FakeModel(text_override=cut, stop_reason="max_tokens")):
            reply = await council.call_model("sys", "user", max_tokens=600, budget=council.Budget())
        self.assertTrue(reply.truncated)  # the API said max_tokens even though the text is short
        self.assertFalse(council.normalize_turn("cao", reply.text)["valid"])

    def test_the_turn_contract_is_tight_and_its_own_example_is_valid_and_short(self):
        from app.prompts import council_prompts as prompts

        contract = prompts.TURN_CONTRACT
        for limit in ("AT MOST 150 words", "40 words max", "2 items max", "No text before or after it"):
            self.assertIn(limit, contract)
        example = contract[contract.index('{"stance": "conditional"'):contract.index("List a hard")].strip()
        turn_ = council.normalize_turn("cao", example)
        self.assertTrue(turn_["valid"])
        self.assertLess(len(example.split()), 150)
        self.assertLess(len(contract) // council.CHARS_PER_TOKEN, 500)




COUNCIL_KEY = "council-key-aaaa"
AGENT_KEYS = {"INTERNAL_CAO_KEY": "cao-key-1", "INTERNAL_COO_KEY": "coo-key-2", "INTERNAL_CTO_KEY": "cto-key-3",
              "INTERNAL_CMO_KEY": "cmo-key-4"}
AGENT_HEADERS = {"INTERNAL_CAO_KEY": "X-Internal-CAO-Key", "INTERNAL_COO_KEY": "X-Internal-COO-Key",
                 "INTERNAL_CTO_KEY": "X-Internal-CTO-Key", "INTERNAL_CMO_KEY": "X-Internal-CMO-Key"}
AGENT_PATHS = ["/api/cao", "/api/coo", "/api/cto", "/api/cmo"]


class MemoryStore:
    """In-memory stand-in for council_store, so the API tests need no database."""

    # The pure (no database) helpers are the REAL ones, so these tests exercise the real parsing and the real record.
    split_questions = staticmethod(council_store.split_questions)
    question_record = staticmethod(council_store.question_record)
    is_orphaned = staticmethod(council_store.is_orphaned)
    ORPHAN_REASON = council_store.ORPHAN_REASON

    def __init__(self):
        self.rows, self.turns = {}, {}

    async def create_session(self, session_id, query, agents, rationale):
        self.rows[session_id] = {"id": session_id, "query": query, "participatingAgents": agents,
                                 "selectionRationale": rationale, "status": "RUNNING", "startedAt": datetime.utcnow(),
                                 "modelCalls": 0, "humanAction": "Pending"}

    async def finish_session(self, session_id, result):
        row = self.rows[session_id]
        if row["status"] != "RUNNING":
            return
        row.update(status=result["status"], caoSynthesizedOptions=result["options"], conflictMatrix=result["conflict_matrix"],
                   hardConstraintVetoes=result["hard_constraint_vetoes"], cooModerationNotes=result["coo_moderation_notes"],
                   redTeamFindings=result["red_team_findings"], dissentRecord=result["dissent_record"],
                   ceoQuestions=council_store.question_record(result), escalationReason=result["escalation_reason"],
                   modelCalls=result["model_calls"], totalTokens=result["total_tokens"], completedAt=datetime.utcnow())
        self.turns[session_id] = [{"round": t["round"], "agentId": t["agent_id"], "position": t["position"],
                                  "hardConstraints": t["hard_constraints"], "confidence": t["confidence"],
                                  "sourcesCited": t["sources_cited"], "tokensUsed": t["tokens_used"]} for t in result["turns"]]

    async def fail_session(self, session_id, reason, calls=0):
        row = self.rows[session_id]
        if row["status"] == "RUNNING":
            row.update(status="FAILED", escalationReason=reason, completedAt=datetime.utcnow())

    async def get_session(self, session_id):
        row = self.rows.get(session_id)
        if row is not None and council_store.is_orphaned(row):
            await self.fail_session(session_id, council_store.ORPHAN_REASON)
        return row, self.turns.get(session_id, [])


class CouncilApiBase(unittest.TestCase):
    def setUp(self):
        from app.main import app

        self.client = TestClient(app)
        self.store = MemoryStore()
        for patcher in (
            mock.patch.dict(os.environ, {"INTERNAL_COUNCIL_KEY": COUNCIL_KEY, **AGENT_KEYS, "GOVERNANCE_DATABASE_URL": "postgres://u:p@localhost:1/x"}),
            mock.patch("app.api.council.council_store", self.store),
            mock.patch.object(council, "_active_sessions", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.headers = {"X-Internal-Council-Key": COUNCIL_KEY}

    def post(self, body=None, headers=None):
        payload = {"query": QUERY} if body is None else body  # {} is a real (invalid) body, not "no body"
        return self.client.post("/api/council", json=payload, headers=self.headers if headers is None else headers)



class AsyncJobApiTests(CouncilApiBase):
    def test_post_returns_202_with_a_session_id_at_once_while_the_work_runs_in_the_background(self):
        # The model takes 0.6 s per call (about 2.4 s for a whole session). POST must not wait for any of it.
        model = FakeModel(delay=0.6)
        with mock.patch.object(council, "_client", model), TestClient(self.client.app) as client:
            started = time.perf_counter()
            response = client.post("/api/council", json={"query": QUERY}, headers=self.headers)
            elapsed = time.perf_counter() - started
            self.assertEqual(response.status_code, 202)
            body = response.json()
            self.assertEqual(body["status"], "RUNNING")
            self.assertLess(elapsed, 0.5)
            first = client.get(f"/api/council/{body['session_id']}", headers=self.headers).json()
            self.assertEqual(first["status"], "RUNNING")
            self.assertNotIn("options", first)  # no result payload while running
            for _ in range(60):  # poll until terminal
                polled = client.get(f"/api/council/{body['session_id']}", headers=self.headers).json()
                if polled["status"] != "RUNNING":
                    break
                time.sleep(0.2)
        self.assertEqual(polled["status"], "RESOLVED", polled.get("escalation_reason"))
        self.assertTrue(2 <= len(polled["options"]) <= 3)
        self.assertFalse(polled["executed"])
        self.assertTrue(polled["turns"])
        self.assertEqual(council.active_sessions(), 0)  # the slot was released

    def test_get_for_an_unknown_or_malformed_id_is_404(self):
        self.assertEqual(self.client.get("/api/council/not-a-uuid", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/api/council/00000000-0000-4000-8000-000000000000", headers=self.headers).status_code, 404)

    def test_validation_rejects_bad_input_before_any_session_is_created(self):
        for body in ({}, {"query": ""}, {"query": "x" * 4001}, {"query": QUERY, "agents": ["cao", "nobody"]},
                     {"query": QUERY, "agents": ["cao"] * 5}):
            self.assertEqual(self.post(body).status_code, 422, body)
        self.assertEqual(self.store.rows, {})

    def test_a_running_row_older_than_five_minutes_is_reported_failed_orphaned_by_restart(self):
        sid = "11111111-1111-4111-8111-111111111111"
        self.store.rows[sid] = {
            "id": sid, "query": "q", "participatingAgents": ["cao", "coo"], "selectionRationale": "r",
            "status": "RUNNING", "startedAt": datetime.utcnow() - timedelta(minutes=6), "modelCalls": 2,
            "humanAction": "Pending"}
        body = self.client.get(f"/api/council/{sid}", headers=self.headers).json()
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(body["escalation_reason"], "orphaned by restart")

    def test_a_recent_running_row_is_not_orphaned(self):
        row = {"status": "RUNNING", "startedAt": datetime.utcnow() - timedelta(minutes=4)}
        self.assertFalse(council_store.is_orphaned(row))
        self.assertTrue(council_store.is_orphaned({**row, "startedAt": datetime.utcnow() - timedelta(seconds=301)}))
        self.assertFalse(council_store.is_orphaned({"status": "RESOLVED", "startedAt": datetime.utcnow() - timedelta(hours=1)}))
        self.assertLess(council.DEADLINE_SECONDS, council_store.ORPHAN_AFTER_SECONDS)

    def test_concurrent_session_cap_answers_503_with_retry_after_and_creates_no_row(self):
        with mock.patch.object(council, "_active_sessions", council.MAX_CONCURRENT_SESSIONS):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn("Retry-After", response.headers)
        self.assertEqual(self.store.rows, {})

    def test_the_slot_is_released_even_when_the_session_crashes(self):
        crash = mock.AsyncMock(side_effect=RuntimeError("boom"))
        with mock.patch.object(council, "run_council_session", crash), TestClient(self.client.app) as client:
            sid = client.post("/api/council", json={"query": QUERY}, headers=self.headers).json()["session_id"]
            for _ in range(30):
                body = client.get(f"/api/council/{sid}", headers=self.headers).json()
                if body["status"] != "RUNNING":
                    break
                time.sleep(0.1)
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(council.active_sessions(), 0)

    def test_an_unconfigured_database_closes_the_endpoint(self):
        with mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": "", "DATABASE_URL": ""}):
            self.assertEqual(self.post().status_code, 503)


class QuestionPersistenceTests(CouncilApiBase):
    """The ceoQuestions column holds {critical, open, agent_flagged} for EVERY status (no schema change)."""

    def _run_through_the_api(self, model):
        with mock.patch.object(council, "_client", model), TestClient(self.client.app) as client:
            sid = client.post("/api/council", json={"query": QUERY}, headers=self.headers).json()["session_id"]
            for _ in range(60):
                body = client.get(f"/api/council/{sid}", headers=self.headers).json()
                if body["status"] != "RUNNING":
                    return sid, body
                time.sleep(0.1)
        self.fail("session did not finish")

    def test_a_resolved_session_stores_and_returns_the_assumptions_that_were_carried_forward(self):
        model = FakeModel(turns={"cmo": turn("conditional", "y", missing=[BUDGET_Q, LICENCE_Q])}, mod=moderation(critical=[]))
        sid, body = self._run_through_the_api(model)
        self.assertEqual(body["status"], "RESOLVED")
        self.assertEqual(body["open_questions"], [BUDGET_Q, LICENCE_Q])  # flagged, not critical, carried forward
        self.assertEqual(body["agent_flagged_questions"], [BUDGET_Q, LICENCE_Q])
        self.assertEqual(body["ceo_questions"], [])  # nothing blocked
        stored = self.store.rows[sid]["ceoQuestions"]
        self.assertEqual(stored, {"critical": [], "open": [BUDGET_Q, LICENCE_Q], "agent_flagged": [BUDGET_Q, LICENCE_Q]})

    def test_a_needs_ceo_input_session_stores_the_critical_questions_and_the_full_flag_record(self):
        model = FakeModel(turns={"cmo": turn("conditional", "y", missing=[BUDGET_Q, "Which fonts?"])},
                          mod=moderation(critical=[BUDGET_Q]))
        sid, body = self._run_through_the_api(model)
        self.assertEqual(body["status"], "NEEDS_CEO_INPUT")
        self.assertEqual(body["ceo_questions"], [BUDGET_Q])  # what the CEO must answer
        self.assertEqual(body["agent_flagged_questions"], [BUDGET_Q, "Which fonts?"])  # the audit record keeps both
        self.assertEqual(body["open_questions"], [])  # a halted session carries no assumptions forward
        self.assertEqual(self.store.rows[sid]["ceoQuestions"]["critical"], [BUDGET_Q])

    def test_a_session_where_nothing_was_flagged_stores_null_and_returns_empty_lists(self):
        sid, body = self._run_through_the_api(FakeModel())
        self.assertEqual(body["status"], "RESOLVED")
        self.assertIsNone(self.store.rows[sid]["ceoQuestions"])
        self.assertEqual((body["ceo_questions"], body["open_questions"], body["agent_flagged_questions"]), ([], [], []))

    def test_the_store_functions_round_trip_every_shape_including_older_bare_list_rows(self):
        result = {"ceo_questions": ["c1"], "open_questions": ["o1", "o2"], "agent_flagged_questions": ["c1", "o1", "o2"]}
        record = council_store.question_record(result)
        self.assertEqual(record, {"critical": ["c1"], "open": ["o1", "o2"], "agent_flagged": ["c1", "o1", "o2"]})
        self.assertEqual(council_store.split_questions(record), record)
        self.assertEqual(council_store.split_questions(json.dumps(record)), record)  # a driver may return jsonb as text
        self.assertEqual(council_store.split_questions(["old question"]),  # a row written before the record existed
                         {"critical": ["old question"], "open": [], "agent_flagged": []})
        for empty in (None, "", "not json", 5):
            self.assertEqual(council_store.split_questions(empty), {"critical": [], "open": [], "agent_flagged": []})
        self.assertIsNone(council_store.question_record({"ceo_questions": [], "open_questions": [], "agent_flagged_questions": []}))
        self.assertIsNone(council_store.question_record({}))

    def test_the_real_store_writes_the_record_into_the_ceoquestions_column_and_nothing_else(self):
        captured = {}

        class Cursor:
            def execute(self, sql, params=()):
                captured.setdefault("sql", []).append((sql, params))

        class Conn:
            def cursor(self):
                return Cursor()

        import contextlib

        @contextlib.contextmanager
        def fake_tx():
            yield Conn()

        result = {"status": "RESOLVED", "conflict_matrix": [], "hard_constraint_vetoes": [], "coo_moderation_notes": "n",
                  "options": [{"option_id": "A"}], "red_team_findings": [], "dissent_record": [], "ceo_questions": [],
                  "open_questions": [BUDGET_Q], "agent_flagged_questions": [BUDGET_Q], "escalation_reason": None,
                  "model_calls": 7, "total_tokens": 100, "turns": []}
        with mock.patch.object(council_store.db, "transaction", fake_tx):
            council_store._finish_sync("sid", result)
        sql, params = captured["sql"][0]
        self.assertIn('"ceoQuestions"=CAST(%s AS jsonb)', sql)
        self.assertNotIn("openQuestions", sql)  # there is NO new column: the existing JSON field is reused
        stored = json.loads(params[7])
        self.assertEqual(stored, {"critical": [], "open": [BUDGET_Q], "agent_flagged": [BUDGET_Q]})




class KeySeparationTests(CouncilApiBase):
    def test_no_key_a_wrong_key_and_an_unset_key(self):
        self.assertEqual(self.post(headers={}).status_code, 401)
        self.assertEqual(self.post(headers={"X-Internal-Council-Key": "wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/api/council/00000000-0000-4000-8000-000000000000").status_code, 401)
        with mock.patch.dict(os.environ, {"INTERNAL_COUNCIL_KEY": ""}):
            self.assertEqual(self.post().status_code, 503)
            self.assertEqual(self.post(headers={"X-Internal-Council-Key": ""}).status_code, 503)
        self.assertEqual(self.store.rows, {})  # a refused call never creates a session

    def test_each_agent_key_is_refused_by_the_council_in_every_header(self):
        for env, key in AGENT_KEYS.items():
            for header in ("X-Internal-Council-Key", *AGENT_HEADERS.values()):
                self.assertEqual(self.post(headers={header: key}).status_code, 401, (env, header))
        self.assertEqual(self.store.rows, {})

    def test_the_council_key_is_refused_by_all_four_agent_endpoints(self):
        every_header = {"X-Internal-Council-Key": COUNCIL_KEY, **{h: COUNCIL_KEY for h in AGENT_HEADERS.values()}}
        for path in AGENT_PATHS:
            response = self.client.post(path, json={"message": "hello"}, headers=every_header)
            self.assertEqual(response.status_code, 401, path)

    def test_the_council_key_does_not_open_cross_check_or_the_kill_switch(self):
        headers = {"X-Internal-CAO-Key": COUNCIL_KEY, "X-Admin-Id": "u1"}
        self.assertEqual(self.client.post("/api/admin/agents/cao/reset", headers=headers).status_code, 401)

    def test_the_council_has_its_own_env_var_and_header(self):
        from app.api import security

        self.assertEqual((security.COUNCIL_KEY_ENV, security.COUNCIL_KEY_HEADER), ("INTERNAL_COUNCIL_KEY", "X-Internal-Council-Key"))
        for env in AGENT_KEYS:
            self.assertNotEqual(env, security.COUNCIL_KEY_ENV)


class SelectionTests(unittest.TestCase):
    def test_cao_and_coo_always_sit_and_the_size_is_two_to_four(self):
        for query in ("How do we treat white spot in tilapia?", QUERY, "Launch an API security campaign for the market"):
            agents, why = council.select_participants(query)
            self.assertTrue(2 <= len(agents) <= 4)
            self.assertTrue({"cao", "coo"} <= set(agents))
            self.assertTrue(why)

    def test_cto_and_cmo_join_when_the_question_touches_their_field(self):
        self.assertEqual(council.select_participants("Plain pond question")[0], ["cao", "coo"])
        self.assertIn("cto", council.select_participants("Change the database security settings")[0])
        self.assertIn("cmo", council.select_participants("Plan the brand campaign")[0])

    def test_explicit_agents_are_validated_and_cao_coo_are_added(self):
        self.assertEqual(council.resolve_participants("q", ["cmo"])[0], ["cao", "coo", "cmo"])
        with self.assertRaises(ValueError):
            council.resolve_participants("q", ["ceo"])


if __name__ == "__main__":
    unittest.main()

