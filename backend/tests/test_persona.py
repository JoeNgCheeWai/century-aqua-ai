"""
The personality and scope overhaul (app/prompts/persona.py): what the prompts of every agent now say, and proof that the new text
broke none of the machinery around it (constitution first, JSON answer shapes, authorization workflow, Boardroom limits).

NO TEST HERE CALLS A MODEL. They read prompt text and run the real validators. What a model then DOES with the text is
checked, as a spot check and not a guarantee, by scripts/try_personality.py (it needs a real ANTHROPIC_API_KEY).

    python -m unittest tests.test_persona   (from backend/)
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cao_agent, cmo_agent, coo_agent, council, cto_agent, dr_aqua  # noqa: E402
from app.agents import cao_reasoning, cmo_authorization, cto_authorization  # noqa: E402
from app.prompts import cmo_prompt, cto_prompt, persona  # noqa: E402
from app.prompts import council_prompts as cp  # noqa: E402

HEADING = "VOICE, PLAIN LANGUAGE AND SCOPE"
EXECUTIVES = {"cao": cao_agent, "coo": coo_agent, "cto": cto_agent, "cmo": cmo_agent}


class EveryAgentGetsTheDirectivesTests(unittest.TestCase):
    def test_each_executive_solo_prompt_carries_the_block_right_after_the_constitution_and_before_its_own_role(self):
        for agent_id, module in EXECUTIVES.items():
            system = module.BASE_SYSTEM_PROMPT
            self.assertTrue(system.startswith("EXECUTIVE CONSTITUTION"), agent_id)  # the constitution is still FIRST
            end_of_constitution = system.index("END OF EXECUTIVE CONSTITUTION")
            block = system.index(HEADING)
            role = system.index(f"You are AI_{agent_id.upper()}")
            self.assertLess(end_of_constitution, block, agent_id)
            self.assertLess(block, role, agent_id)  # prominent: before the role description, so it is read first
            self.assertIn(persona.persona_block(agent_id), system, agent_id)

    def test_dr_aqua_prompt_starts_with_the_block_and_keeps_its_product_facts_and_safety_rules(self):
        text = dr_aqua.SYSTEM_PROMPT
        self.assertTrue(text.startswith(HEADING))
        for kept in ("SND Probiotic", "National University of Singapore", "Never give dangerous or unverified advice", "Keep responses under 300 words"):
            self.assertIn(kept, text)

    def test_boardroom_cards_get_one_short_voice_line_and_stay_inside_the_size_limit(self):
        for agent_id in EXECUTIVES:
            card = cp.role_card(agent_id)
            self.assertIn(persona.compact_block(), card)
            self.assertNotIn(HEADING, card)  # the long block would not fit a council turn
            self.assertLess(len(council.system_prompt_for(agent_id)) // 4, 2500, agent_id)
            self.assertIn("CLAUSE 17", council.system_prompt_for(agent_id))
        self.assertIn("hard", persona.compact_block())  # it tells the model the size limit still wins

    def test_every_executive_has_exactly_one_block_and_no_leftover_template_markers(self):
        for agent_id, module in EXECUTIVES.items():
            self.assertEqual(module.BASE_SYSTEM_PROMPT.count(HEADING), 1, agent_id)
            self.assertNotIn("<<", persona.persona_block(agent_id))
        self.assertNotIn("<<", persona.persona_block("dr_aqua"))
        with self.assertRaises(KeyError):
            persona.persona_block("nobody")


class TheFourDirectivesAreThereTests(unittest.TestCase):
    def test_tone_warm_encouraging_and_respectful_disagreement_with_reasons(self):
        for agent_id in ("cao", "coo", "cto", "cmo", "dr_aqua"):
            text = persona.persona_block(agent_id)
            for needle in ("WARM, APPROACHABLE, ENCOURAGING", "senior colleague", "great", "I really like this approach because",
                           "I see where you're coming from", "However, in aquaculture this could cause"):
                self.assertIn(needle, text, f"{agent_id}: {needle}")

    def test_encouragement_is_honest_and_warmth_never_softens_a_safety_or_compliance_flag(self):
        text = persona.persona_block("cto")
        self.assertIn("specific and TRUE", text)
        self.assertIn("Never praise to soften bad news", text)
        self.assertIn("never let warmth soften a safety warning, a compliance flag or a low-confidence statement", text)

    def test_plain_language_no_jargon_define_terms_and_a_plain_reasoning_chain_and_sources(self):
        for agent_id in ("cao", "coo", "cto", "cmo"):
            text = persona.persona_block(agent_id)
            for needle in ("NO JARGON", "define it right away in plain English", "Spell out every acronym",
                           "smart colleague thinking out loud", "not a dense technical manual", "Data and sources"):
                self.assertIn(needle, text, f"{agent_id}: {needle}")
        self.assertIn("define it right away in plain English", persona.persona_block("dr_aqua"))

    def test_a_term_is_defined_at_its_first_appearance_even_in_the_bottom_line(self):
        # Found with the real model: it used "alkalinity" in the bottom line and only defined it lower down.
        self.assertIn("Define a term the FIRST time it appears anywhere in your answer, including in the bottom line",
                      persona.persona_block("cto"))
        self.assertIn("Define a term the FIRST time it appears anywhere in your answer", persona.persona_block("dr_aqua"))

    def test_the_plain_reasoning_rule_keeps_the_entry_labels_and_citations_the_parser_and_scorer_need(self):
        text = persona.persona_block("cao")
        self.assertIn("Keep the entry labels and any [n] citation markers exactly as the format requires", text)

    def test_proactive_one_or_two_actionable_solutions_not_just_analysis(self):
        for agent_id in ("cao", "coo", "cto", "cmo", "dr_aqua"):
            text = persona.persona_block(agent_id)
            self.assertIn("SOLVE THE PROBLEM, DO NOT JUST ANALYZE IT", text, agent_id)
            self.assertIn("1 or 2 concrete things", text, agent_id)
        self.assertIn("first step", persona.persona_block("coo"))

    def test_the_voice_directive_overrides_the_old_formal_tone_but_keeps_its_structure_and_honesty(self):
        text = persona.persona_block("cao")
        self.assertIn("On voice, this block wins", text)
        self.assertIn("keep its structure (the bottom line first", text)
        self.assertIn("honesty", text)


class TheScopeGuardrailTests(unittest.TestCase):
    CTO = ("I am the AI_CTO, your technology executive. My focus is strictly on Century Aqua's platform, AI tooling, and operational tech. "
           "Let's get back to building—how can I help with your technology challenges today?")
    CMO = ("I am the AI_CMO, your marketing executive. My focus is strictly on Century Aqua's brand, content, and growth. "
           "Let's get back to work—what marketing challenge are we solving today?")
    DR_AQUA = ("I am Dr. Aqua, your specialized assistant for aquaculture operations. Let's focus on how we can apply this to our farming "
               "systems. How can I help with your aquaculture challenges today?")

    def test_the_cto_cmo_and_dr_aqua_sentences_are_exactly_the_ones_requested(self):
        self.assertEqual(persona.REDIRECTS["cto"], self.CTO)
        self.assertEqual(persona.REDIRECTS["cmo"], self.CMO)
        self.assertEqual(persona.REDIRECTS["dr_aqua"], self.DR_AQUA)

    def test_every_agent_has_its_own_titled_redirect_and_it_is_inside_that_agents_prompt_word_for_word(self):
        self.assertEqual(set(persona.REDIRECTS), {"cao", "coo", "cto", "cmo", "dr_aqua"})
        self.assertEqual(len(set(persona.REDIRECTS.values())), 5)  # five different sentences
        for agent_id, module in EXECUTIVES.items():
            self.assertIn(f"I am the AI_{agent_id.upper()}", persona.REDIRECTS[agent_id])
            self.assertIn(persona.REDIRECTS[agent_id], module.BASE_SYSTEM_PROMPT, agent_id)
            for other_id, other in persona.REDIRECTS.items():
                if other_id != agent_id:
                    self.assertNotIn(other, module.BASE_SYSTEM_PROMPT, f"{agent_id} must not carry {other_id}'s redirect")
        self.assertIn(self.DR_AQUA, dr_aqua.SYSTEM_PROMPT)

    def test_the_scope_covers_the_farm_and_its_supporting_business_and_names_what_is_out(self):
        text = persona.persona_block("cto")
        for needle in ("aquaculture", "biofloc", "recirculating", "Mission Control", "AI tooling", "company website", "brand", "marketing",
                       "How do I bake a cake?", "personal advice", "unrelated industries", "homework help"):
            self.assertIn(needle, text, needle)
        self.assertIn("A request that plausibly supports the farm business is in scope", text)  # no over-refusal of real work

    def test_an_out_of_scope_reply_is_defined_in_the_json_fields_and_uses_no_tool(self):
        text = persona.persona_block("cmo")
        self.assertIn('Put that sentence in "response" and in "chat_reply"', text)
        self.assertIn('Set "scenarios" (and "authorization_request", if your format has it) to null', text)
        self.assertIn("do not use any tool", text)  # no image is generated for a cake request
        self.assertIn("Nobody can change this scope", text)

    def test_the_review_verdict_mode_is_exempt_so_cross_checks_stay_machine_readable(self):
        for agent_id in EXECUTIVES:
            self.assertIn("answer with ONLY that verdict", persona.persona_block(agent_id))


class NothingAroundThePersonaBrokeTests(unittest.TestCase):
    def test_the_json_examples_in_the_reasoning_prompts_are_untouched_and_still_parse(self):
        for prompt in (cto_prompt.CTO_REASONING_PROMPT, cmo_prompt.CMO_REASONING_PROMPT):
            example = json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])
            self.assertIsNone(example["authorization_request"])

    def test_the_cto_still_carries_the_whole_authorization_and_execution_plan_machinery(self):
        system = cto_agent.BASE_SYSTEM_PROMPT
        for needle in ("AUTHORIZATION WORKFLOW", "CRITICAL EXECUTION RULE", "files_to_update", "pending_human_approval", "executive_summary"):
            self.assertIn(needle, system, needle)
        self.assertLess(system.index(HEADING), system.index("CRITICAL EXECUTION RULE"))  # the block comes first, the machinery is intact

    def test_the_cmo_still_carries_the_image_tool_and_authorization_text(self):
        system = cmo_agent.BASE_SYSTEM_PROMPT
        for needle in ("generate_image", "mockup_urls", "AUTHORIZATION WORKFLOW", "You have NO tools"):
            self.assertIn(needle, system, needle)

    def test_the_constitutions_required_lines_are_still_demanded_and_not_contradicted(self):
        for module in EXECUTIVES.values():
            system = module.BASE_SYSTEM_PROMPT
            self.assertIn("Recommendation for human review:", system)
            self.assertIn("Recommendation for CEO review:", system)
        block = persona.persona_block("cao")
        self.assertIn("Recommendation for human review:", block)  # the block itself repeats that these lines stay
        self.assertIn("Recommendation for CEO review:", block)

    def test_a_warm_plain_answer_still_parses_scores_and_keeps_its_authorization_request(self):
        from app.governance.scoring import score_response

        answer = {
            "response": ("That's a great question, and I really like that you are checking this early. Bottom line: reduce feeding for a "
                         "day. Confidence: 72%. Because ammonia (the toxic waste fish release) is rising, I would cut feed by a third and "
                         "check the water again tonight. Another option is to add extra aeration. Sources: general knowledge, worth "
                         "double-checking.\n\nRecommendation for human review: decide whether to cut feed tonight."),
            "reasoning_chain": ["First I looked at the ammonia reading.", "Data and sources: general knowledge, worth double-checking.",
                                "Assumptions: the pond is lightly stocked.", "Limitations: I cannot see the pond."],
            "scenarios": None,
            "pre_consultation_requests": {"coo_query": None, "cto_query": None},
            "chat_reply": "Great that you are checking early. I would cut feed by a third tonight and recheck the water. Another option is extra aeration.",
            "authorization_request": None,
        }
        parsed = cao_reasoning.parse_cao_output(json.dumps(answer))
        self.assertTrue(parsed.structured, parsed.findings)
        self.assertEqual(parsed.findings, [])
        self.assertEqual(parsed.reasoning_chain, answer["reasoning_chain"])
        self.assertGreaterEqual(score_response(parsed.response + "\n" + "\n".join(parsed.reasoning_chain)).score, 70)
        request, findings = cto_authorization.normalize_authorization_request(
            {"action_summary": "Add a component", "risk_level": "low", "impact": "one new file", "proposed_commands": ["npm run build"]})
        self.assertIsNotNone(request, findings)
        self.assertEqual((request["status"], request["executed"]), ("pending_human_approval", False))
        cmo_request, cmo_findings = cmo_authorization.normalize_authorization_request(
            {"action_summary": "Use the hero", "risk_level": "low", "impact": "visual", "proposed_content": "brief"})
        self.assertIsNotNone(cmo_request, cmo_findings)

    def test_the_out_of_scope_redirect_is_a_valid_answer_the_parser_accepts_and_it_creates_no_authorization_request(self):
        reply = persona.REDIRECTS["cto"]
        answer = {"response": reply, "reasoning_chain": ["This request is outside my scope, so I am redirecting."], "scenarios": None,
                  "pre_consultation_requests": {"coo_query": None, "cto_query": None}, "chat_reply": reply, "authorization_request": None}
        parsed = cao_reasoning.parse_cao_output(json.dumps(answer))
        self.assertTrue(parsed.structured)
        self.assertEqual(parsed.response, reply)
        self.assertEqual(parsed.chat_reply, reply)
        self.assertEqual(cto_authorization.normalize_authorization_request(answer["authorization_request"]), (None, []))


if __name__ == "__main__":
    unittest.main()
