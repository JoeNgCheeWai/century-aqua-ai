"""
The Boardroom deep-thinking and cohesion upgrade.

    1. TOKEN LIMITS: a turn is no longer capped at 600 tokens / 150 words. The new caps are what is passed to the model API.
    2. COHESION PROMPTS: active listening, constructive debate, hidden reasoning and "nothing is executed" are in the prompts.
    3. THE CROSS-RESPONSE ROUND: after the initial positions every agent reads the others' proposals and answers each one. It is
       bounded, it can never fail a session, and it can only ADD to what an agent said (a veto or a CEO question is never lost).
    4. PROGRESS REPORTING: a RUNNING session says which phase it is in.

No database, no network: the model is a scripted stand-in (the FakeModel of test_council_protocol.py).
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import council  # noqa: E402
from app.prompts import council_prompts as prompts  # noqa: E402
from tests.test_council_protocol import ALL, FakeModel, QUERY, cross_reply, moderation, run, turn  # noqa: E402

FLOOR = [{"constraint": "Passwords must be hashed", "source": "security policy"}]
CONCISE = "be concise but thorough enough to address"


class TokenLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_the_new_defaults(self):
        self.assertEqual(council.TURN_MAX_TOKENS, 2048)
        self.assertEqual(council.CROSS_MAX_TOKENS, 3072)
        self.assertEqual(council.CHAIR_MAX_TOKENS, 1200)  # the chair's JSON stays short, so it was not raised
        self.assertEqual(council.MAX_CALLS, 16)

    async def test_the_new_token_limit_is_what_is_actually_sent_to_the_api(self):
        model = FakeModel()
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        sent = {}
        for call in model.calls:
            sent.setdefault(call["kind"], set()).add(call["max_tokens"])
        self.assertEqual(sent["turn"], {2048})  # initial positions
        self.assertEqual(sent["cross"], {3072})  # the cross-response round
        self.assertEqual(sent["moderation"], {1200})
        self.assertNotIn(600, {c["max_tokens"] for c in model.calls})  # the old cap is gone everywhere

    async def test_a_revision_turn_gets_the_agent_cap_too(self):
        stuck = [{"topic": "t", "agents": ["cmo", "cto"], "positions": "p", "resolved": False}]
        model = FakeModel(turns={"cmo": turn("oppose", "No.")}, mod=moderation(conflicts=stuck, revise=[{"agent_id": "cmo", "question": "Why?"}]))
        await run(model)
        self.assertEqual({c["max_tokens"] for c in model.calls if c["kind"] == "revision"}, {council.TURN_MAX_TOKENS})

    async def test_a_long_reply_is_still_cut_in_code_at_the_new_cap(self):
        budget = council.Budget()
        with mock.patch.object(council, "_client", FakeModel(text_override="x" * (9000 * council.CHARS_PER_TOKEN), usage=(100, 9000))):
            reply = await council.call_model("sys", "user", max_tokens=council.TURN_MAX_TOKENS, budget=budget)
        self.assertTrue(reply.truncated)
        self.assertLessEqual(len(reply.text), council.TURN_MAX_TOKENS * council.CHARS_PER_TOKEN)
        self.assertEqual(budget.output_tokens, council.TURN_MAX_TOKENS)

    async def test_a_session_whose_every_reply_uses_the_whole_new_cap_still_fits_the_session_ceilings(self):
        result = await run(FakeModel(usage=(3000, council.TURN_MAX_TOKENS)))
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertLessEqual(result["input_tokens"], council.MAX_INPUT_TOKENS)
        self.assertLessEqual(result["output_tokens"], council.MAX_OUTPUT_TOKENS)


class NoStrictWordLimitTests(unittest.TestCase):
    def test_the_150_word_limit_is_gone_from_every_prompt(self):
        for name in ("TURN_CONTRACT", "CROSS_RESPONSE_CONTRACT", "DELIBERATION_CARD", "COHESION_RULES"):
            text = getattr(prompts, name)
            self.assertNotIn("150 words", text, name)
            self.assertNotIn("40 words max", text, name)

    def test_the_replacement_directive_is_in_the_turn_prompts_and_the_system_card(self):
        for name in ("TURN_CONTRACT", "CROSS_RESPONSE_CONTRACT", "DELIBERATION_CARD"):
            self.assertIn(CONCISE, getattr(prompts, name).lower(), name)
        self.assertIn("previous speakers' points", prompts.TURN_CONTRACT)

    def test_no_stale_150_word_wording_is_left_in_the_code_comments_either(self):
        for relative in ("app/agents/council.py", "app/prompts/council_prompts.py", "app/prompts/persona.py"):
            source = (BACKEND_DIR / relative).read_text(encoding="utf-8")
            self.assertNotIn("150 words", source, relative)
            self.assertNotIn("150-word", source, relative)

    def test_concise_is_asked_but_the_size_is_still_enforced_in_code(self):
        self.assertIn("hard size limit", prompts.TURN_CONTRACT)

    def test_every_council_system_prompt_carries_the_rules_and_stays_compact(self):
        for agent in council.ORDER:
            system = council.system_prompt_for(agent)
            self.assertIn("BOARDROOM RULES", system)
            self.assertLess(len(system) // 4, 2500, agent)  # test_council_protocol pins the same ceiling


class CohesionPromptTests(unittest.TestCase):
    """The four behaviours the CEO asked for, each pinned to the text the model is given."""

    def test_active_listening_acknowledge_and_evaluate_the_specific_points(self):
        rules = prompts.COHESION_RULES
        self.assertIn("LISTEN FIRST", rules)
        self.assertIn("Explicitly acknowledge and evaluate their SPECIFIC points", rules)
        self.assertIn("name the executive", rules)

    def test_constructive_debate_agree_adds_a_layer_disagree_names_the_part_and_offers_an_alternative(self):
        rules = prompts.COHESION_RULES
        self.assertIn("IF YOU AGREE", rules)
        self.assertIn("NEW operational, technical or biological layer", rules)
        self.assertIn("IF YOU DISAGREE", rules)
        self.assertIn("exactly WHICH PART you disagree with", rules)
        self.assertIn("cohesive alternative that still solves the CEO's original problem", rules)

    def test_hidden_reasoning_the_agent_analyses_the_previous_speaker_before_speaking(self):
        self.assertIn('ANALYSE IN "reasoning" FIRST', prompts.COHESION_RULES)
        self.assertIn("before you write what you say aloud", prompts.COHESION_RULES)
        for contract in (prompts.TURN_CONTRACT, prompts.CROSS_RESPONSE_CONTRACT):
            self.assertIn("THINK BEFORE YOU SPEAK", contract)
            self.assertIn('"reasoning"', contract)
            self.assertIn("sits behind a toggle", contract)  # the existing UI toggle
        self.assertLess(prompts.TURN_CONTRACT.index('"reasoning"'), prompts.TURN_CONTRACT.index('"stance"'))  # reasoning comes first

    def test_the_thinking_process_is_the_same_three_steps_as_the_solo_agents(self):
        for needed in ("real problem", "2 or 3 options", "recommend and why", "risks and constraints"):
            self.assertIn(needed, prompts.TURN_CONTRACT)

    def test_no_execution_it_is_a_purely_strategic_discussion(self):
        for text in (prompts.TURN_CONTRACT, prompts.CROSS_RESPONSE_CONTRACT):
            self.assertIn("STRATEGIC DISCUSSION, NOT AN ACTION", text)
            self.assertIn("Nothing is executed here: no code is written or run, no image is made, no tool is used", text)
        self.assertIn("Nothing is executed here: no code, no images", prompts.DELIBERATION_CARD)
        self.assertIn("purely strategic discussion to reach alignment", prompts.DELIBERATION_CARD)

    def test_the_cross_response_contract_demands_an_answer_to_each_other_executive(self):
        text = prompts.CROSS_RESPONSE_CONTRACT
        for field in ('"responses"', '"their_point"', '"verdict"', '"adds_or_alternative"'):
            self.assertIn(field, text)
        self.assertIn('ONE entry in "responses" for EACH other executive', text)

    def test_the_cross_response_example_is_a_valid_reply(self):
        text = prompts.CROSS_RESPONSE_CONTRACT
        example = text[text.index('{"reasoning": "CTO says'):text.index("List a hard")].strip()
        parsed = council.normalize_cross_turn("cao", example, set(ALL))
        self.assertTrue(parsed["valid"])
        self.assertEqual({r["to"] for r in parsed["responses"]}, {"cto", "cmo"})

    def test_the_other_contracts_are_not_given_the_debate_rules_they_do_not_use(self):
        for contract in (prompts.MODERATION_CONTRACT, prompts.SYNTHESIS_CONTRACT, prompts.RED_TEAM_CONTRACT):
            self.assertNotIn("COHESION AND DEEP THOUGHT", contract)

    def test_a_revision_turn_is_given_the_cohesion_rules(self):
        source = (BACKEND_DIR / "app" / "agents" / "council.py").read_text(encoding="utf-8")
        self.assertIn("{prompts.COHESION_RULES}", source)


class SpyModel(FakeModel):
    """A FakeModel that keeps the full user prompt each agent was sent in the cross-response round."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cross_prompts = {}

    async def _create(self, **kw):
        user = kw["messages"][0]["content"]
        if "COUNCIL CROSS-RESPONSE" in user:
            self.cross_prompts[next(a for a in ALL if f"You are AI_{a.upper()}" in kw["system"])] = user
        return await super()._create(**kw)


class CrossResponseRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_agent_is_shown_the_others_proposals_but_never_its_own_listed_among_them(self):
        spy = SpyModel(turns={"cto": turn("oppose", "UNIQUE-CTO-POSITION"), "cmo": turn("support", "UNIQUE-CMO-POSITION")})
        await run(spy)
        self.assertEqual(set(spy.cross_prompts), set(ALL))
        shown_to_cao = spy.cross_prompts["cao"].split("<<<TURNS")[1]
        self.assertIn("UNIQUE-CTO-POSITION", shown_to_cao)
        self.assertIn("UNIQUE-CMO-POSITION", shown_to_cao)
        self.assertNotIn("[CAO]", shown_to_cao)  # an agent answers the OTHERS
        self.assertIn("UNIQUE-CTO-POSITION", spy.cross_prompts["cmo"])
        self.assertNotIn("The real problem, the options weighed", spy.cross_prompts["cao"])  # private reasoning is never shown

    async def test_the_round_runs_in_parallel_not_one_after_another(self):
        import time

        model = FakeModel(delay=0.3)
        with mock.patch.object(council, "_client", model):
            session = council.Session("s", QUERY, ALL, "r")
            turns = await council._round1_proposals(session)
            started = time.perf_counter()
            await council._round_cross_response(session, turns)
            elapsed = time.perf_counter() - started
        self.assertEqual(model.max_in_flight, 4)
        self.assertLess(elapsed, 0.3 * 2)  # about one call, not four

    async def test_the_cross_responses_are_stored_under_their_own_round_and_reach_the_result(self):
        result = await run(FakeModel())
        cross = [t for t in result["turns"] if t["round"] == council.CROSS_ROUND]
        self.assertEqual({t["agent_id"] for t in cross}, set(ALL))
        for t in cross:
            self.assertTrue(t["reasoning"])
            self.assertEqual({r["to"] for r in t["responses"]}, set(ALL) - {t["agent_id"]})
        self.assertEqual({t["round"] for t in result["turns"]}, {1, 2, 5, 6, 7})  # the old round numbers keep their meaning

    async def test_a_turn_that_answers_nobody_is_dropped_and_the_agent_keeps_its_initial_position(self):
        silent = cross_reply("", responses=[], position="I AM NOT LISTENING")
        result = await run(FakeModel(turns={"cto": turn("oppose", "CTO-FIRST")}, cross={"cto": silent}))
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertNotIn("cto", {t["agent_id"] for t in result["turns"] if t["round"] == council.CROSS_ROUND})

    async def test_an_entry_that_adds_nothing_or_names_the_wrong_person_does_not_count(self):
        raw = cross_reply("", responses=[
            {"to": "cto", "their_point": "x", "verdict": "agree", "evaluation": "Yes.", "adds_or_alternative": ""},  # adds nothing
            {"to": "cao", "their_point": "x", "verdict": "agree", "evaluation": "Yes.", "adds_or_alternative": "Add a layer."},
            {"to": "nobody", "their_point": "x", "verdict": "agree", "adds_or_alternative": "y"},  # not at the table
            {"to": "cao", "their_point": "again", "verdict": "agree", "adds_or_alternative": "twice"},  # a second answer to the same person
        ])
        parsed = council.normalize_cross_turn("cto", raw, set(ALL))
        self.assertEqual([r["to"] for r in parsed["responses"]], ["cao"])  # not itself, not a stranger, not an empty addition


class CrossResponseSafetyTests(unittest.IsolatedAsyncioTestCase):
    """The round is an addition: whatever goes wrong inside it, the session must behave as it did without it."""

    async def test_a_failed_cross_response_degrades_it_never_fails_the_session(self):
        class Flaky(FakeModel):
            async def _create(self, **kw):
                if "COUNCIL CROSS-RESPONSE" in kw["messages"][0]["content"] and "AI_CMO" in kw["system"]:
                    raise RuntimeError("upstream exploded")
                return await super()._create(**kw)

        result = await run(Flaky())
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertNotIn("cmo", {t["agent_id"] for t in result["turns"] if t["round"] == council.CROSS_ROUND})

    async def test_every_cross_response_failing_still_resolves_on_the_initial_positions(self):
        result = await run(FakeModel(cross={a: "not json at all" for a in ALL}))
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])

    async def test_the_round_is_skipped_when_the_budget_cannot_also_cover_the_rest(self):
        with mock.patch.object(council, "MAX_CALLS", 9):  # 4 proposals + 4 answers + moderation + synthesis + red team = 11
            model = FakeModel()
            result = await run(model)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertNotIn("cross", model.kinds())
        self.assertLessEqual(result["model_calls"], 9)

    async def test_the_round_is_skipped_when_much_of_the_deadline_is_already_gone(self):
        session = council.Session("s", QUERY, ["cao", "coo"], "r")
        session.started -= council.DEADLINE_SECONDS * 0.9
        model = FakeModel()
        turns = [council.normalize_turn(a, turn()) for a in ("cao", "coo")]
        with mock.patch.object(council, "_client", model):
            after = await council._round_cross_response(session, turns)
        self.assertEqual(after, turns)
        self.assertEqual(model.calls, [])  # no call was made

    async def test_a_two_agent_table_still_has_a_cross_response(self):
        model = FakeModel()
        result = await run(model, agents=["cao", "coo"])
        self.assertEqual(model.kinds().count("cross"), 2)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])

    async def test_a_cross_turn_that_collapses_confidence_escalates_like_any_other_turn(self):
        result = await run(FakeModel(cross={"cto": cross_reply("", confidence=10)}))  # answers everyone, then its confidence collapses
        self.assertEqual(result["status"], "ESCALATED_TO_CEO")
        self.assertIn("confidence collapse", result["escalation_reason"])

    async def test_the_worst_case_session_stays_inside_the_call_ceiling(self):
        # A conflict that never resolves: 4 proposals + 4 cross-responses + moderation + 2 revisions = 11 calls, then it escalates.
        stuck = [{"topic": "t", "agents": ["cmo", "cto"], "positions": "p", "resolved": False}]
        model = FakeModel(turns={"cmo": turn("oppose", "No.")}, mod=moderation(conflicts=stuck, revise=[{"agent_id": "cmo", "question": "Why?"}] * 3))
        result = await run(model)
        self.assertEqual(result["status"], "ESCALATED_TO_CEO")
        self.assertEqual(model.kinds().count("revision"), council.MAX_LOOPS)  # still never a third loop
        self.assertLessEqual(len(model.calls), council.MAX_CALLS)
        self.assertLessEqual(result["model_calls"], council.MAX_CALLS)

    async def test_a_healthy_four_agent_session_makes_the_expected_number_of_calls_and_leaves_room(self):
        model = FakeModel()
        result = await run(model)
        self.assertEqual(model.kinds(), ["turn"] * 4 + ["cross"] * 4 + ["moderation", "synthesis", "red_team"])  # 11 calls
        self.assertEqual(result["model_calls"], 11)
        self.assertLess(result["model_calls"], council.MAX_CALLS)

    async def test_a_cross_response_cannot_make_the_council_act(self):
        raw = cross_reply("", proposed_action={"action_summary": "Delete the database", "status": "approved", "executed": True})
        result = await run(FakeModel(cross={"cto": raw}))
        self.assertFalse(result["executed"])
        self.assertTrue(result["requires_human_approval"])
        forced = council.normalize_turn("cto", raw)["proposed_action"]
        self.assertEqual((forced["status"], forced["executed"]), ("pending_human_approval", False))


class CrossResponseOnlyAddsTests(unittest.IsolatedAsyncioTestCase):
    """Listening can change a stance. It must never silently erase a veto's basis or a question for the CEO."""

    def merged(self, initial, cross):
        return council.merge_cross_turn(council.normalize_turn("cto", initial), council.normalize_turn("cto", cross))

    def test_a_hard_constraint_the_agent_did_not_repeat_is_not_withdrawn(self):
        merged = self.merged(turn("oppose", "No.", constraints=FLOOR), turn("conditional", "Maybe, patched first."))
        self.assertEqual([c["constraint"] for c in merged["hard_constraints"]], ["Passwords must be hashed"])
        self.assertEqual(merged["stance"], "conditional")  # the updated stance does win
        self.assertEqual(merged["position"], "Maybe, patched first.")

    def test_a_question_for_the_ceo_and_an_objection_carry_over_without_duplicates(self):
        objection = [{"to": "cmo", "point": "No evidence of demand."}]
        merged = self.merged(turn(missing=["What is the budget cap?"], objections=objection),
                             turn(missing=["what is the budget cap?", "Which market first?"], objections=objection))
        self.assertEqual(merged["missing_critical_data"], ["what is the budget cap?", "Which market first?"])
        self.assertEqual(len(merged["objections"]), 1)

    def test_a_first_proposed_action_stays_unless_the_cross_turn_states_its_own(self):
        action = {"action_summary": "Patch the login"}
        self.assertEqual(self.merged(turn(action=action), turn())["proposed_action"]["action_summary"], "Patch the login")
        newer = {"action_summary": "Patch and test the login"}
        self.assertEqual(self.merged(turn(action=action), turn(action=newer))["proposed_action"]["action_summary"], "Patch and test the login")

    async def test_a_veto_still_stands_when_the_vetoing_agent_stops_repeating_its_constraint(self):
        # The chair rules a veto only for an agent that STATED a hard constraint. After listening the CTO says none: it must still count.
        silent_on_constraint = cross_reply("", base=turn("conditional", "OK after the patch."))
        vetoes = [{"agent_id": "cto", "constraint": FLOOR[0]["constraint"], "source": "security policy", "overrides": ["cmo"]}]
        model = FakeModel(turns={"cto": turn("oppose", "No plaintext logins.", constraints=FLOOR)}, cross={"cto": silent_on_constraint},
                          mod=moderation(vetoes=vetoes))
        result = await run(model)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertEqual([v["agent_id"] for v in result["hard_constraint_vetoes"]], ["cto"])

    async def test_a_question_flagged_in_round_one_still_reaches_the_chair_after_the_cross_response(self):
        question = "What is the approved budget cap in USD?"
        model = FakeModel(turns={"cmo": turn(missing=[question])})
        result = await run(model)
        self.assertIn(question, result["agent_flagged_questions"])


class ProgressReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_running_session_reports_each_phase_in_order_and_forgets_it_at_the_end(self):
        seen = []
        original = council.set_phase

        def spy(session_id, phase):
            seen.append(phase)
            original(session_id, phase)

        with mock.patch.object(council, "set_phase", spy):
            result = await run(FakeModel())
        self.assertEqual(seen, ["initial_positions", "cross_response", "moderation", "revisions", "synthesis", "red_team"])
        self.assertIsNone(council.current_phase(result["session_id"]))  # nothing is left behind

    def test_only_known_phases_are_accepted_and_each_has_a_message_in_the_ui(self):
        council.set_phase("sid", "not_a_phase")
        self.assertIsNone(council.current_phase("sid"))
        council.set_phase("sid", "cross_response")
        self.assertEqual(council.current_phase("sid"), "cross_response")
        council._phases.pop("sid", None)
        ui = (BACKEND_DIR.parent / "frontend" / "src" / "lib" / "mc-boardroom.ts").read_text(encoding="utf-8")
        for phase in council.PHASES:
            self.assertIn(f"{phase}:", ui, phase)
        self.assertIn("Agents are reviewing each other's proposals and deliberating", ui)

    def test_the_polling_endpoint_reports_the_phase_only_while_running(self):
        from app.api import council as council_api

        council.set_phase("11111111-1111-4111-8111-111111111111", "cross_response")
        try:
            running = council_api._payload({"id": "11111111-1111-4111-8111-111111111111", "status": "RUNNING", "startedAt": None}, [])
            self.assertEqual(running["phase"], "cross_response")
            self.assertNotIn("options", running)
        finally:
            council._phases.clear()
        unknown = council_api._payload({"id": "22222222-2222-4222-8222-222222222222", "status": "RUNNING", "startedAt": None}, [])
        self.assertIsNone(unknown["phase"])  # after a restart the UI falls back to its generic message


class DebateTabLabelTests(unittest.TestCase):
    def setUp(self):
        self.source = (BACKEND_DIR.parent / "frontend" / "src" / "components" / "mc" / "DebateFlow.tsx").read_text(encoding="utf-8")

    def test_the_round_labels_name_initial_positions_and_cross_response(self):
        self.assertIn("Initial Positions", self.source)
        self.assertIn("Cross-Response & Deliberation", self.source)

    def test_the_cross_response_is_shown_right_after_the_initial_positions_and_reasoning_stays_behind_a_toggle(self):
        self.assertIn("roundOrder", self.source)
        self.assertIn("Show internal reasoning", self.source)
        self.assertIn("<details", self.source)


if __name__ == "__main__":
    unittest.main()
