"""
THE CTO'S CONCISE, EXECUTIVE-STYLE RESPONSE FORMAT (fix/cto-concise-response-format).

The CEO's first read ("chat_reply") is three short parts (can I do it, is it sound, how I will do it) with no Assumptions or What to check section, no
"Confidence: NN%" and no "Recommendation for human review:" line. The prompt asks for that; CODE (chat_reply.strip_boilerplate) enforces it on the chat
reply only. The audited "response" behind the details button keeps the two lines, so the Constitution score does not move.

    python -m unittest tests.test_cto_concise_format   (from backend/)
"""

import asyncio
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cto_agent  # noqa: E402
from app.agents.chat_reply import strip_boilerplate  # noqa: E402
from app.governance.scoring import score_response  # noqa: E402
from app.prompts import chat_style, cto_prompt  # noqa: E402
from tests.test_cto_reads_before_writing import QUERY, ScriptedClient, answer, text_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

NEW_FILE = "frontend/src/components/mc/Note.tsx"
VERBOSE = ("Yes, I can add this comment. It is safe because it is documentation only.\n\n### What the comment says\n// Handles retries.\n\n"
           "### Assumptions\n- The file uses two-space indents.\n- Nobody else edits it.\n\n### What to check\n- The comment sits above the function.\n\n"
           "Confidence: 88%.\n\nRecommendation for human review: approve the comment.")


class PromptTests(unittest.TestCase):
    SYSTEM = cto_agent.BASE_SYSTEM_PROMPT

    def test_the_response_format_block_is_in_the_ctos_real_prompt_once(self):
        self.assertEqual(self.SYSTEM.count("RESPONSE FORMAT (for a plan or a change"), 1)
        for needle in ("**Can I do it?**", "**Is it sound?**", "**How I'll do it.**", "an Assumptions section", "a What to check section", '"Confidence: NN%"',
                       'the "Recommendation for human review:" line', "any explanation of what you already said", "GOOD:", "BAD:", "Ready for your approval"):
            self.assertIn(needle, cto_prompt.CTO_SYSTEM_PROMPT, needle)

    def test_the_other_executives_do_not_get_it(self):
        from app.agents import cao_agent, cmo_agent, coo_agent

        for module in (cao_agent, coo_agent, cmo_agent):
            self.assertNotIn("RESPONSE FORMAT (for a plan", module.BASE_SYSTEM_PROMPT, module.__name__)

    def test_the_required_lines_are_kept_in_response_and_kept_out_of_the_chat_reply_by_the_prompt(self):
        self.assertIn('they live in "response" only, never in "chat_reply"', cto_prompt.CTO_REASONING_PROMPT)
        self.assertIn('"Recommendation for human review:" line here: those stay in "response"', chat_style.CTO_CHAT_REPLY_PROMPT)
        self.assertIn("is a ceiling, not a target", chat_style.CTO_CHAT_REPLY_PROMPT)

    def test_nothing_in_the_execution_block_asks_for_an_assumptions_section_any_more(self):
        self.assertNotIn('under an "Assumptions:" heading', chat_style.CTO_EXECUTION_PLAN_PROMPT)
        self.assertIn('note those assumptions in "pr_body" only', chat_style.CTO_EXECUTION_PLAN_PROMPT)
        self.assertIn("RULE 2, MAKE REASONABLE ASSUMPTIONS, DO NOT DEFER", chat_style.CTO_EXECUTION_PLAN_PROMPT)  # the rule itself is unchanged

    def test_the_plan_approval_the_execution_plan_and_the_memory_rule_are_still_there(self):
        for needle in ("PLAN APPROVAL:", "call submit_plan_for_approval once", "EXECUTION PLAN (ONE EXTRA FIELD", "RULE 1, FORCE THE JSON EXECUTION PLAN",
                       "CONTINUOUS LEARNING"):
            self.assertIn(needle, self.SYSTEM, needle)


class StripBoilerplateTests(unittest.TestCase):
    def test_it_removes_the_sections_the_rating_and_the_review_line_and_keeps_the_answer(self):
        out = strip_boilerplate(VERBOSE)
        for gone in ("### Assumptions", "two-space indents", "### What to check", "Confidence: 88%", "Recommendation for human review"):
            self.assertNotIn(gone, out, gone)
        self.assertIn("Yes, I can add this comment.", out)
        self.assertIn("### What the comment says\n// Handles retries.", out)  # a real section before the boilerplate is kept whole

    def test_a_low_confidence_is_kept_because_the_ceo_must_be_told_when_an_answer_is_shaky(self):
        self.assertIn("Confidence: 45%", strip_boilerplate("Maybe. Confidence: 45%. Worth your own judgement."))

    def test_an_escalation_line_is_never_removed(self):
        text = "Recommendation for CEO review: a secret was exposed.\n\nRotate it now."
        self.assertEqual(strip_boilerplate(text), text)

    def test_bold_headings_and_quoted_lines_are_handled(self):
        out = strip_boilerplate("Done.\n\n**Assumptions**\n- a\n\n**What to check**\n- b\n\n> Recommendation for human review: x")
        self.assertTrue(out.startswith("Done."))
        for gone in ("Assumptions", "What to check", "Recommendation for human review", "- a", "- b"):
            self.assertNotIn(gone, out, gone)

    def test_empty_text_comes_back_as_it_came_and_a_reply_that_is_only_boilerplate_is_not_blanked(self):
        self.assertIsNone(strip_boilerplate(None))
        self.assertEqual(strip_boilerplate("  "), "  ")
        only = "Recommendation for human review: approve it."
        self.assertEqual(strip_boilerplate(only), only)  # never leave the CEO with an empty reply

    def test_a_clean_reply_is_unchanged(self):
        clean = "Yes, I can do it. It is safe: documentation only. Here is the plan. Ready for your approval."
        self.assertEqual(strip_boilerplate(clean), clean)

class ThroughTheRealCtoTests(GuardedTestCase):
    def ask(self, chat_reply):
        payload = answer({NEW_FILE: "export default function Note() { return null; }\n"})
        payload["chat_reply"] = chat_reply
        client = ScriptedClient([text_reply(payload)])
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(cto_agent.get_cto_result(QUERY, use_rag=False, audit=False, memory=False)), payload

    def test_the_ceo_reads_the_short_reply_and_the_full_response_keeps_the_scored_lines(self):
        result, payload = self.ask(VERBOSE)
        for gone in ("### Assumptions", "### What to check", "Confidence: 88%", "Recommendation for human review"):
            self.assertNotIn(gone, result.chat_reply, gone)
        self.assertIn("Yes, I can add this comment.", result.chat_reply)
        self.assertIn("Recommendation for human review:", result.reply)  # behind the details button, where the Constitution reads it
        self.assertIn("Confidence: 80%", result.reply)

    def test_the_constitution_score_is_exactly_what_it_was_before_the_stripping(self):
        result, payload = self.ask(VERBOSE)
        scored = payload["response"] + "\n\n" + "\n".join(payload["reasoning_chain"])
        self.assertEqual(result.constitutional_score, score_response(scored).score)
        self.assertFalse(result.needs_review)


if __name__ == "__main__":
    unittest.main()