"""
Phase 6.6 prompt tests: the CTO is told to output an execution_plan, the CMO is told about mock_generate_image and that
mockups are placeholders, and neither is ever told that approving deploys or publishes anything.

    python -m unittest tests.test_phase_6_6_prompts   (from backend/)
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cao_agent, cmo_agent, coo_agent, cto_agent  # noqa: E402
from app.prompts import chat_style  # noqa: E402


class CtoPromptTests(unittest.TestCase):
    def test_the_cto_is_told_to_output_an_execution_plan_with_the_four_parts(self):
        text = chat_style.CTO_EXECUTION_PLAN_PROMPT
        for needle in ("execution_plan", "branch_name", "files_to_update", "pr_title", "pr_body", "COMPLETE NEW CONTENT", "EXACT repository path"):
            self.assertIn(needle, text)
        self.assertIn(chat_style.CTO_EXECUTION_PLAN_PROMPT.strip(), cto_agent.BASE_SYSTEM_PROMPT)

    def test_the_cto_is_warned_that_the_tool_overwrites_whole_files_and_not_to_guess_a_file_it_was_not_shown(self):
        text = chat_style.CTO_EXECUTION_PLAN_PROMPT
        self.assertIn("OVERWRITES the whole file", text)
        self.assertIn("only update a file whose current full text you were shown", text)

    def test_the_cto_is_told_approving_opens_a_pull_request_and_does_not_deploy(self):
        text = chat_style.CTO_EXECUTION_PLAN_PROMPT
        self.assertIn("Approving does NOT deploy anything", text)
        self.assertIn("opens a pull request", text)
        self.assertIn("never say the change is live", text)

    def test_the_cto_is_told_which_files_the_system_refuses(self):
        text = chat_style.CTO_EXECUTION_PLAN_PROMPT
        for needle in (".env", ".github", "secret", "main"):
            self.assertIn(needle, text)

    def test_the_cto_must_output_the_json_plan_inside_authorization_request_and_not_describe_it_in_text(self):
        text = chat_style.CTO_EXECUTION_PLAN_PROMPT
        self.assertIn("RULE 1", text)
        self.assertIn("MUST output a valid, parseable JSON object named \"execution_plan\"", text)
        self.assertIn("inside \"authorization_request\"", text)
        self.assertIn("exactly these keys", text)
        self.assertIn("starting with agent/", text)
        self.assertIn("Do not just describe the plan in text", text)
        # The key the validator really reads. "files_to_create" would be dropped as "files_to_update is empty".
        self.assertIn("files_to_update", text)
        # "files_to_create" may appear only in the prohibition ("... (not files_to_create)"), never as a key the model is shown.
        self.assertNotIn('"files_to_create"', text)
        self.assertEqual(text.count("files_to_create"), 1)
        self.assertIn("(not files_to_create)", text)

    def test_the_cto_must_assume_and_not_defer_to_the_human_on_open_design_questions(self):
        text = chat_style.CTO_EXECUTION_PLAN_PROMPT
        self.assertIn("RULE 2", text)
        self.assertIn("DO NOT defer the code generation to the human", text)
        self.assertIn("reasonable, standard industry assumptions", text)
        self.assertIn("generate the execution_plan anyway", text)
        self.assertIn("draft the code for review", text)
        self.assertIn("override every earlier instruction", text)
        # The safety limits still stand.
        self.assertIn("never overwrite an existing file you were not shown", text)

    def test_the_cto_is_no_longer_told_to_ask_for_a_file_and_leave_the_plan_null(self):
        self.assertNotIn('ask for the file text and leave "execution_plan" null', chat_style.CTO_EXECUTION_PLAN_PROMPT)

    def test_the_cto_gets_the_critical_execution_rule_and_the_template_inside_its_prompt(self):
        text = chat_style.CTO_EXECUTION_TEMPLATE
        for needle in (
            "CRITICAL EXECUTION RULE",
            "MUST output a valid JSON object named execution_plan inside your authorization_request",
            "Do not just describe the plan in text",
            "You must fill out this exact schema",
            "You MUST provide the full file content in the content string",
            "You MUST use the files_to_update key (not files_to_create)",
            "state those assumptions in the pr_body, and generate the JSON anyway. Do not defer to the human",
        ):
            self.assertIn(needle, text)
        self.assertIn(text.strip(), chat_style.CTO_EXECUTION_PLAN_PROMPT)
        self.assertIn(text.strip(), cto_agent.BASE_SYSTEM_PROMPT)

    @staticmethod
    def _template_json() -> dict:
        text = chat_style.CTO_EXECUTION_TEMPLATE
        start = text.index("{\n  \"authorization_request\"")
        end = text.index("\n}\n", start) + 2
        return json.loads(text[start:end])  # must be valid JSON as the model sees it (the \n and \" are escapes)

    def test_the_template_is_valid_json_and_its_plan_has_exactly_the_four_keys(self):
        request = self._template_json()["authorization_request"]
        plan = request["execution_plan"]
        self.assertEqual(set(plan), {"branch_name", "files_to_update", "pr_title", "pr_body"})
        self.assertTrue(plan["branch_name"].startswith("agent/"))
        self.assertNotIn("files_to_create", plan)
        self.assertIn("\n", plan["files_to_update"][0]["content"])  # the file text is complete and multi-line

    def test_the_template_is_accepted_by_the_real_backend_validators(self):
        from app.agents import cto_authorization

        request = self._template_json()["authorization_request"]
        normalised, findings = cto_authorization.normalize_authorization_request(request)
        self.assertIsNotNone(normalised, findings)
        self.assertIsNotNone(normalised["execution_plan"], findings)
        self.assertEqual(normalised["execution_plan"]["files_to_update"][0]["path"], "frontend/src/components/mc/PrivacyToggle.tsx")
        self.assertIsNotNone(normalised["executive_summary"], findings)  # an object of three parts, not dropped
        self.assertEqual(findings, [])

    def test_the_other_executives_are_not_given_the_plan_or_ui_instructions(self):
        for module in (cao_agent, coo_agent):
            self.assertNotIn("execution_plan", module.BASE_SYSTEM_PROMPT, module.__name__)
            self.assertNotIn("mock_generate_image", module.BASE_SYSTEM_PROMPT, module.__name__)
        self.assertNotIn("mock_generate_image", cto_agent.BASE_SYSTEM_PROMPT)
        self.assertNotIn("CTO_EXECUTION_PLAN", cmo_agent.BASE_SYSTEM_PROMPT)


class CmoPromptTests(unittest.TestCase):
    def test_the_cmo_is_told_it_can_use_mock_generate_image_for_ui_mockups(self):
        text = chat_style.CMO_UI_PROMPT
        self.assertIn("mock_generate_image", text)
        self.assertIn("mockup_urls", text)
        self.assertIn("https://placehold.co/", text)
        self.assertIn(chat_style.CMO_UI_PROMPT.strip(), cmo_agent.BASE_SYSTEM_PROMPT)

    def test_the_cmo_is_told_a_mockup_is_a_placeholder_not_a_design_and_never_to_invent_an_address(self):
        text = chat_style.CMO_UI_PROMPT
        self.assertIn("PLACEHOLDER", text)
        self.assertIn("NOT a real design", text)
        self.assertIn("Never invent", text)

    def test_the_cmo_may_include_a_pull_request_plan_for_ui_files_and_is_told_approving_publishes_nothing(self):
        text = chat_style.CMO_UI_PROMPT
        for needle in ("execution_plan", "branch_name", "files_to_update", "UI and content files", "OVERWRITES whole files"):
            self.assertIn(needle, text)
        self.assertIn("Approving does NOT publish anything", text)
        self.assertIn("never say the change is live", text)


class SharedShapeTests(unittest.TestCase):
    def test_the_example_plan_in_both_prompts_is_the_same_shape_the_validator_accepts(self):
        from app.agents import execution_plan as ep

        block = chat_style.EXECUTION_PLAN_FIELDS.strip()
        example = json.loads("{" + block + "}")["execution_plan"]
        self.assertEqual(set(example), {"branch_name", "files_to_update", "pr_title", "pr_body"})
        self.assertEqual(set(example["files_to_update"][0]), {"path", "content", "commit_message"})
        filled = {**example, "files_to_update": [{**example["files_to_update"][0], "path": "frontend/src/a.ts", "content": "x"}]}
        plan, findings = ep.normalize_execution_plan(filled)
        self.assertIsNotNone(plan, findings)

    def test_the_reasoning_prompts_json_examples_are_untouched_by_phase_6_6(self):
        from app.prompts import cmo_prompt, cto_prompt

        for prompt in (cto_prompt.CTO_REASONING_PROMPT, cmo_prompt.CMO_REASONING_PROMPT):
            example = json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])
            self.assertNotIn("chat_reply", example)
            self.assertIsNone(example["authorization_request"])  # still null in the example: the plan is an optional extra


if __name__ == "__main__":
    unittest.main()
