"""
The "Qwen + Cline" CTO persona (persona.CTO_ENGINEER_PROMPT): what the CTO's prompt now says, and proof it did not disturb the
machinery around it. NO TEST HERE CALLS A MODEL. What a model then DOES with the text is checked, as a spot check and not a
guarantee, by scripts/try_super_cto.py.

    python -m unittest tests.test_cto_persona   (from backend/)
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cmo_agent, cto_agent, tools  # noqa: E402
from app.prompts import chat_style, cto_prompt, persona  # noqa: E402

SYSTEM = cto_agent.BASE_SYSTEM_PROMPT
BLOCK = persona.CTO_ENGINEER_PROMPT


class TheEngineerPromptIsThereAndInTheRightPlaceTests(unittest.TestCase):
    def test_it_comes_after_the_constitution_and_the_voice_block_and_before_the_old_role_text(self):
        self.assertTrue(SYSTEM.startswith("EXECUTIVE CONSTITUTION"))
        order = [SYSTEM.index("END OF EXECUTIVE CONSTITUTION"), SYSTEM.index(persona.persona_block("cto")), SYSTEM.index(BLOCK),
                 SYSTEM.index("You are AI_CTO, the Chief Technology Officer"), SYSTEM.index("CRITICAL EXECUTION RULE")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(SYSTEM.count("SOFTWARE ENGINEERING EXPERT: HOW YOU WORK"), 1)

    def test_only_the_cto_gets_it(self):
        from app.agents import cao_agent, coo_agent

        for module in (cao_agent, coo_agent, cmo_agent):
            self.assertNotIn("SOFTWARE ENGINEERING EXPERT", module.BASE_SYSTEM_PROMPT, module.__name__)
            self.assertNotIn("github_read_file", module.BASE_SYSTEM_PROMPT, module.__name__)

    def test_the_role_proactive_intent_and_not_an_order_taker(self):
        for needle in ("You are not an order-taker", "Software Engineering Executive", "Anticipate intent",
                       "underlying problem you think the person is trying to solve", "Be proactive"):
            self.assertIn(needle, BLOCK, needle)

    def test_gentle_pushback_names_the_risks_and_offers_one_or_two_alternatives_with_analogies(self):
        for needle in ("technical debt", "slow the app down", "break the architecture", "plain language, kindly",
                       "1 or 2 better ways", "simple everyday analogy"):
            self.assertIn(needle, BLOCK, needle)

    def test_it_acknowledges_a_genuinely_good_idea_specifically_and_never_flatters(self):
        self.assertIn("genuinely good", BLOCK)
        self.assertIn("Praise only what is really good, specifically", BLOCK)

    def test_a_human_still_decides_so_the_cto_complies_when_it_is_safe_and_permitted(self):
        self.assertIn("A human decides", BLOCK)
        self.assertIn("safe and permitted", BLOCK)

    def test_the_golden_rule_read_before_you_modify_and_never_from_memory(self):
        for needle in ("THE GOLDEN RULE OF EXISTING CODE", "github_read_file", "You MUST read a file before you modify it",
                       "Never rewrite a file from memory", "call github_read_file for it first",
                       "the system rejects a plan that updates an existing file you did not read"):
            self.assertIn(needle, BLOCK, needle)

    def test_the_read_tool_is_described_as_safe_and_needing_no_authorization(self):
        self.assertIn("It only reads, so you may call it freely, any time, with no authorization request", BLOCK)
        self.assertIn("needs no approval", tools.READ_TOOL_SPEC["description"])

    def test_it_says_what_to_do_when_a_read_fails_and_never_to_claim_a_file_it_did_not_read(self):
        self.assertIn("If a read returns ERROR", BLOCK)
        self.assertIn("do not guess the content", BLOCK)
        self.assertIn("Never claim you read a file you did not read", BLOCK)

    def test_the_answer_shape_conversation_in_the_json_fields_and_the_code_only_in_the_execution_plan(self):
        for needle in ('"response"', '"chat_reply"', "natural conversational text", "authorization_request.execution_plan",
                       "COMPLETE new file text", "Do not put anything outside the JSON object"):
            self.assertIn(needle, BLOCK, needle)


class TheOldNoToolsWordingWasCorrectedNotLeftContradictingTests(unittest.TestCase):
    def test_the_prompt_no_longer_says_it_has_no_tools_at_all_it_names_the_one_it_has(self):
        text = cto_prompt.CTO_SYSTEM_PROMPT
        self.assertNotIn("You have NO tools: no shell", text)
        self.assertIn("You have NO tools except ONE: github_read_file", text)
        self.assertIn("which only READS a single file", text)

    def test_every_other_prohibition_is_unchanged_it_still_cannot_write_run_deploy_or_change_anything(self):
        text = cto_prompt.CTO_SYSTEM_PROMPT
        for needle in ("no repository write access", "no deploy access", "no database access",
                       "You cannot run, deploy, commit, migrate, restart, block, delete, rotate or change anything",
                       "Never state or imply that you did it", "Never ask for, repeat or store a secret", "Never invent a file path"):
            self.assertIn(needle, text, needle)

    def test_it_may_only_claim_to_have_read_a_file_it_read_with_the_tool(self):
        self.assertIn("or did not read it with the tool", cto_prompt.CTO_SYSTEM_PROMPT)
        self.assertIn("apart from reading single repository files with github_read_file", cto_prompt.CTO_SYSTEM_PROMPT)

    def test_the_execution_plan_template_and_rules_are_untouched(self):
        for needle in ("CRITICAL EXECUTION RULE", "files_to_update", "You MUST use the files_to_update key (not files_to_create)",
                       "state those assumptions in the pr_body", "OVERWRITES the whole file"):
            self.assertIn(needle, SYSTEM, needle)
        example = json.loads(chat_style.CTO_EXECUTION_TEMPLATE[chat_style.CTO_EXECUTION_TEMPLATE.index('{\n  "authorization_request"'):
                                                                chat_style.CTO_EXECUTION_TEMPLATE.index("\n}\n", chat_style.CTO_EXECUTION_TEMPLATE.index('{\n  "authorization_request"')) + 2])
        self.assertEqual(set(example["authorization_request"]["execution_plan"]), {"branch_name", "files_to_update", "pr_title", "pr_body"})

    def test_the_json_example_in_the_reasoning_prompt_and_the_authorization_workflow_are_untouched(self):
        prompt = cto_prompt.CTO_REASONING_PROMPT
        example = json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])
        self.assertIsNone(example["authorization_request"])
        for needle in ("AUTHORIZATION WORKFLOW", "you MUST output a structured authorization_request", "pending_human_approval"):
            self.assertIn(needle, cto_prompt.CTO_SYSTEM_PROMPT, needle)

    def test_the_voice_block_and_the_scope_redirect_are_still_there_and_the_prompt_stays_one_block_each(self):
        self.assertEqual(SYSTEM.count("VOICE, PLAIN LANGUAGE AND SCOPE"), 1)
        self.assertIn(persona.REDIRECTS["cto"], SYSTEM)

    def test_the_prompt_stays_in_a_sane_size(self):
        self.assertLess(len(SYSTEM) // 4, 16000)  # about 11k tokens now; far below the model's context window


class TheCtoStillAnswersInTheSharedFormatTests(unittest.TestCase):
    def test_a_conversational_answer_with_pushback_parses_and_keeps_its_plan(self):
        from app.agents import cao_reasoning, cto_authorization

        answer = {
            "response": ("That's a great instinct to want one place for this. One thing I'd gently flag: putting it in the shared form would be "
                         "like adding another room to a house with no support wall, it works today but gets harder to change. I'd suggest a small "
                         "separate component instead. Confidence: 78%.\n\nRecommendation for human review: approve the small component."),
            "reasoning_chain": ["First I read the form.", "Data and sources: the file I read.", "Assumptions: none.", "Limitations: not run."],
            "scenarios": None, "pre_consultation_requests": {"coo_query": None, "cto_query": None},
            "chat_reply": "Good instinct. I would add a small separate component rather than growing the form.",
            "authorization_request": {"action_summary": "Add a small component", "risk_level": "low", "impact": "One new file.",
                                      "proposed_code_diff": "new file", "proposed_commands": [], "rollback_plan": "Close the PR.",
                                      "execution_plan": {"branch_name": "agent/small-component", "pr_title": "Add a small component", "pr_body": "I read the form first.",
                                                         "files_to_update": [{"path": "frontend/src/components/mc/New.tsx", "content": "export default function New() { return null; }\n", "commit_message": "Add New"}]}},
        }
        parsed = cao_reasoning.parse_cao_output(json.dumps(answer))
        self.assertTrue(parsed.structured, parsed.findings)
        request, findings = cto_authorization.normalize_authorization_request(answer["authorization_request"])
        self.assertIsNotNone(request["execution_plan"], findings)
        self.assertEqual((request["status"], request["executed"]), ("pending_human_approval", False))


if __name__ == "__main__":
    unittest.main()
