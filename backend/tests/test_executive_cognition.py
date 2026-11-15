"""
The cognitive upgrade's prompt side: the proactive expert roles (CAO, COO, CMO), the Strategic Thinking Process every executive
follows (the CTO included), and the guarantee that none of it touched the JSON schemas or the authorization workflow.

These are checks on the TEXT the model is given. They prove the instructions are present, in the right place and consistent with
the rest of the prompt. They cannot prove the model follows them: no real-model run has checked that yet.
No database, no network.
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cao_agent, cmo_agent, coo_agent, cto_agent, council  # noqa: E402
from app.agents.cao_reasoning import MAX_CHAIN_ENTRIES  # noqa: E402
from app.prompts import cao_prompt, cmo_prompt, coo_prompt, cto_prompt, persona  # noqa: E402

AGENTS = {"cao": cao_agent, "coo": coo_agent, "cmo": cmo_agent, "cto": cto_agent}
ROLE_TITLE = {"cao": "PROACTIVE AQUACULTURE CONSULTANT", "coo": "TOP OPERATIONS EXPERT", "cmo": "CREATIVE GURU", "cto": "SOFTWARE ENGINEERING EXPERT"}
SCHEMA_PROMPTS = {"cao": cao_prompt.CAO_REASONING_PROMPT, "coo": coo_prompt.COO_REASONING_PROMPT, "cmo": cmo_prompt.CMO_REASONING_PROMPT,
                  "cto": cto_prompt.CTO_REASONING_PROMPT}


class ExpertRoleTests(unittest.TestCase):
    def test_every_executive_prompt_carries_its_own_role_once_and_not_the_others(self):
        for agent, module in AGENTS.items():
            prompt = module.BASE_SYSTEM_PROMPT
            self.assertEqual(prompt.count(ROLE_TITLE[agent]), 1, agent)
            for other, title in ROLE_TITLE.items():
                if other != agent:
                    self.assertNotIn(title, prompt, f"{agent} prompt contains the {other} role")

    def test_the_cao_is_a_proactive_aquaculture_consultant(self):
        role = persona.expert_role("cao")
        for needed in ("ANTICIPATE", "biological and environmental risks", "water-quality optimisations", "feed alternatives", "disease-prevention"):
            self.assertIn(needed, role)

    def test_the_coo_is_a_top_operations_expert(self):
        role = persona.expert_role("coo")
        for needed in ("ANTICIPATE bottlenecks", "efficiency", "supply chain", "allocated", "PROPOSE operational solutions"):
            self.assertIn(needed, role)

    def test_the_cmo_is_a_creative_guru(self):
        role = persona.expert_role("cmo")
        for needed in ("market trends", "consumer behaviour", "novel campaigns", "A/B tests", "brand-positioning"):
            self.assertIn(needed, role)

    def test_the_cto_keeps_its_engineer_role_and_gets_the_same_thinking_process(self):
        self.assertIn("SOFTWARE ENGINEERING EXPERT", cto_agent.BASE_SYSTEM_PROMPT)
        self.assertIn("STRATEGIC THINKING PROCESS", cto_agent.BASE_SYSTEM_PROMPT)

    def test_there_is_no_expert_role_for_an_agent_that_is_not_one_of_the_three(self):
        for agent in ("cto", "dr_aqua", "nobody"):
            with self.assertRaises(KeyError):
                persona.expert_role(agent)

    def test_no_role_lets_the_ai_decide_or_act_on_its_own(self):
        for agent in ("cao", "coo", "cmo"):
            role = persona.expert_role(agent).lower()
            self.assertNotIn("you decide", role)
            self.assertNotIn("without approval", role)
            self.assertNotIn("execute", role)
        self.assertIn("A human decides", persona.THINKING_PROCESS_PROMPT)

    def test_each_role_says_the_existing_rules_below_it_still_bind(self):
        self.assertIn("SND PROBIOTIC rules below still binds you", persona.expert_role("cao"))
        self.assertIn("rules below still binds you", persona.expert_role("coo"))
        self.assertIn("claims discipline below still binds you", persona.expert_role("cmo"))


class ThinkingProcessTests(unittest.TestCase):
    def test_all_four_executives_are_given_the_process_exactly_once(self):
        for agent, module in AGENTS.items():
            self.assertEqual(module.BASE_SYSTEM_PROMPT.count("STRATEGIC THINKING PROCESS"), 1, agent)

    def test_it_asks_for_the_real_problem_two_or_three_options_and_a_recommended_path(self):
        text = persona.THINKING_PROCESS_PROMPT
        self.assertIn("Step 1: The real problem", text)
        self.assertIn("You asked for X, but the underlying issue looks like Y", text)
        self.assertIn("Step 2: The options", text)
        self.assertIn("2 or 3 genuinely different ways", text)
        self.assertIn("Step 3: The best path", text)
        self.assertIn("which option you recommend and why", text)
        self.assertIn("risks and the constraints", text)
        self.assertLess(text.index("Step 1"), text.index("Step 2"))
        self.assertLess(text.index("Step 2"), text.index("Step 3"))

    def test_the_thinking_goes_in_the_existing_reasoning_chain_the_ui_already_hides(self):
        text = persona.THINKING_PROCESS_PROMPT
        self.assertIn('"reasoning_chain"', text)
        self.assertIn("internal reasoning", text)  # the toggle's name
        self.assertIn("do not copy the three steps into the response", text)  # so the main chat is not cluttered

    def test_it_happens_before_the_recommendation_and_before_any_proposed_action(self):
        self.assertIn("Before you write your recommendation or any action you propose", persona.THINKING_PROCESS_PROMPT)

    def test_the_cmo_and_cto_are_told_it_applies_before_their_authorization_request_and_the_cto_before_its_plan(self):
        self.assertIn("before you write an authorization_request", persona.thinking_process("cmo"))
        self.assertNotIn("execution_plan", persona.thinking_process("cmo"))
        self.assertIn("authorization_request or an execution_plan", persona.thinking_process("cto"))

    def test_the_cao_and_coo_are_never_told_about_plans_or_image_tools_they_do_not_have(self):
        # tests/test_phase_6_6_prompts.py pins this for the real prompts; this checks the new text on its own.
        for agent in ("cao", "coo"):
            text = persona.thinking_process(agent) + persona.expert_role(agent)
            for word in ("execution_plan", "generate_image", "authorization_request"):
                self.assertNotIn(word, text, f"{agent} was told about {word}")

    def test_the_voice_blocks_redirect_sentences_still_have_their_real_em_dashes(self):
        # A byte-level rewrite once turned these into garbage characters; tests/test_persona.py pins the exact sentences.
        for agent in ("cto", "cmo", "cao", "coo"):
            self.assertIn("\u2014", persona.REDIRECTS[agent])
            self.assertNotIn("\u00e2\u20ac", persona.REDIRECTS[agent])

    def test_it_comes_after_the_constitution_and_voice_and_before_the_role_specific_format(self):
        for agent, module in AGENTS.items():
            prompt = module.BASE_SYSTEM_PROMPT
            marks = [prompt.index("END OF EXECUTIVE CONSTITUTION"), prompt.index("VOICE, PLAIN LANGUAGE AND SCOPE"),
                     prompt.index("STRATEGIC THINKING PROCESS"), prompt.index(f"You are AI_{agent.upper()}")]
            self.assertEqual(marks, sorted(marks), agent)

    def test_the_steps_fit_inside_the_chain_length_the_parser_accepts(self):
        # Up to 5 Step entries plus Data and sources, Assumptions and Limitations = 8, and the parser keeps up to MAX_CHAIN_ENTRIES.
        self.assertGreaterEqual(MAX_CHAIN_ENTRIES, 5 + 3)
        self.assertIn("up to 5 in all", persona.THINKING_PROCESS_PROMPT)
        for agent, schema in SCHEMA_PROMPTS.items():
            self.assertIn("3 to 5 entries", schema, agent)  # the existing schema still says the same thing

    def test_a_cross_check_review_may_still_answer_with_only_its_json_verdict(self):
        self.assertIn("answer with only that verdict", persona.THINKING_PROCESS_PROMPT)

    def test_a_trivial_question_does_not_have_to_be_padded(self):
        self.assertIn("do not pad", persona.THINKING_PROCESS_PROMPT)


class SchemasAreUntouchedTests(unittest.TestCase):
    """The upgrade only ADDS prompt text. These pin the contracts that the rest of the platform depends on."""

    def test_the_json_field_names_are_still_demanded_by_each_agent(self):
        for agent, module in AGENTS.items():
            for field in ('"response"', '"reasoning_chain"', '"chat_reply"'):
                self.assertIn(field, module.BASE_SYSTEM_PROMPT, f"{agent} lost {field}")
        for agent in ("cao", "coo", "cmo"):
            self.assertIn('"scenarios"', AGENTS[agent].BASE_SYSTEM_PROMPT)
        for agent in ("cto", "cmo"):
            self.assertIn('"authorization_request"', AGENTS[agent].BASE_SYSTEM_PROMPT)
        self.assertIn('"execution_plan"', cto_agent.BASE_SYSTEM_PROMPT)

    def test_the_constitutions_required_lines_are_still_there(self):
        for agent, module in AGENTS.items():
            self.assertIn("Recommendation for human review:", module.BASE_SYSTEM_PROMPT, agent)
            self.assertIn("ESCALATE TO HUMAN", module.BASE_SYSTEM_PROMPT, agent)

    def test_the_constitution_still_comes_before_everything_this_upgrade_added(self):
        for agent, module in AGENTS.items():
            prompt = module.BASE_SYSTEM_PROMPT
            self.assertLess(prompt.index("EXECUTIVE CONSTITUTION"), prompt.index("STRATEGIC THINKING PROCESS"), agent)
            self.assertLess(prompt.index("EXECUTIVE CONSTITUTION"), prompt.index(ROLE_TITLE[agent]), agent)

    def test_the_new_text_has_no_braces_so_it_cannot_break_the_format_calls_that_fill_in_retrieved_excerpts(self):
        for text in (*persona._EXPERT_ROLES.values(), persona.THINKING_PROCESS_PROMPT):
            self.assertNotIn("{", text)
            self.assertNotIn("}", text)

    def test_the_boardroom_prompts_are_not_changed_by_this_upgrade(self):
        for agent in ("cao", "coo", "cto", "cmo"):
            prompt = council.system_prompt_for(agent)
            self.assertNotIn("STRATEGIC THINKING PROCESS", prompt)
            self.assertNotIn("PROACTIVE AQUACULTURE CONSULTANT", prompt)

    def test_the_prompts_stay_inside_a_sane_size(self):
        for agent, module in AGENTS.items():
            self.assertLess(len(module.BASE_SYSTEM_PROMPT) // 4, 16_000, f"{agent} prompt is over ~16k tokens")


if __name__ == "__main__":
    unittest.main()
