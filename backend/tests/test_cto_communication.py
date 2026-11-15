"""
The CTO's communication overhaul, as CODE (the prompt wording is pinned in tests/test_executive_cognition.py):

  * a structured, friendly chat_reply of up to 5000 characters survives a whole CTO call, while the other executives keep their 1200;
  * when the read-before-write rule drops the plan, the friendly wording must never promise a plan that no longer exists: code adds a
    correction FIRST, and never turns "no chat reply" into a reply that is only the correction;
  * partner wording is not mistaken for claiming an action, and real claims are still removed.

A scripted model stands in for Anthropic (the helpers of test_cto_reads_before_writing.py). No database, no network.
"""

import asyncio
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cto_agent  # noqa: E402
from tests.test_cto_reads_before_writing import CHAT_PATH, QUERY, ScriptedClient, answer, text_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

NEW_FILE = "frontend/src/components/mc/SessionsBadge.tsx"
STRUCTURED = (
    "Happy to help. I have prepared the plan for your final approval. Nothing goes live until you approve it and the change is merged.\n\n"
    "### Why This Matters\nThe badge is the quickest way to see activity, because it sits where you already look.\n\n"
    "### Step 1\n1. Open the **Boardroom** page.\n2. Look at the top right for the **Sessions today** badge.\n\n"
    "### What to Look For\n- A small pill with a number.\n- The file **frontend/src/components/mc/Badge.tsx** in the pull request."
)


def reply_with(chat_reply, files):
    payload = answer(files)
    payload["chat_reply"] = chat_reply
    return payload


class CtoCommunicationCodeTests(GuardedTestCase):
    def ask(self, replies):
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(cto_agent.get_cto_result(QUERY, use_rag=False, audit=False, memory=False)), client

    def test_a_structured_friendly_reply_reaches_the_ceo_whole(self):
        result, _ = self.ask([text_reply(reply_with(STRUCTURED, {NEW_FILE: "export default function B() { return null; }\n"}))])
        self.assertEqual(result.chat_reply, STRUCTURED)  # headings, numbers and bold are all still there, in order
        self.assertIsNotNone(result.authorization_request["execution_plan"])

    def test_a_reply_longer_than_the_old_limit_is_kept_up_to_the_ctos_own_limit_and_no_further(self):
        long_text = "### Step 1\n" + "1. **Open** the page.\n" * 200  # about 4,200 characters
        result, _ = self.ask([text_reply(reply_with(long_text, {NEW_FILE: "x\n"}))])
        self.assertGreater(len(result.chat_reply), 1200)
        self.assertLessEqual(len(result.chat_reply), cto_agent.CTO_MAX_CHAT_REPLY_CHARS)
        result, _ = self.ask([text_reply(reply_with("word " * 3000, {NEW_FILE: "x\n"}))])  # a runaway reply is cut in code
        self.assertLessEqual(len(result.chat_reply), cto_agent.CTO_MAX_CHAT_REPLY_CHARS)

    def test_the_other_three_executives_still_cut_their_chat_reply_at_1200(self):
        from app.agents import cao_reasoning

        raw = json.dumps({"response": "x. Recommendation for human review: y.", "chat_reply": "word " * 3000})
        self.assertEqual(len(cao_reasoning.parse_cao_output(raw).chat_reply), cao_reasoning.MAX_CHAT_REPLY_CHARS)

    def test_a_reply_that_claims_an_action_is_still_removed_even_when_nicely_formatted(self):
        bad = "### All done\nI have deployed the badge and I sent the update to the team."
        result, _ = self.ask([text_reply(reply_with(bad, {NEW_FILE: "x\n"}))])
        self.assertIsNone(result.chat_reply)  # dropped: the screen then shows the full recommendation instead

    def test_partner_wording_is_not_mistaken_for_claiming_an_action(self):
        from app.agents.chat_reply import vetted_chat_reply

        for sentence in ("I have outlined the safest path forward and the trade-offs.",
                         "Once you confirm this fits your vision, I will prepare the precise execution plan for your final approval.",
                         "I have prepared the plan for your final approval. Nothing goes live until you approve it and the change is merged."):
            self.assertEqual(vetted_chat_reply(sentence), sentence)


class DroppedPlanIsCorrectedFirstTests(GuardedTestCase):
    ask = CtoCommunicationCodeTests.ask

    def dropped(self, chat_reply):
        # AgentChat.tsx exists in the fake repository and was not read, so the plan is dropped in code.
        return self.ask([text_reply(reply_with(chat_reply, {CHAT_PATH: "export default function AgentChat() { return null; }\n"}))])[0]

    def test_the_correction_comes_before_the_models_promise_in_the_chat_reply_and_in_the_full_reply(self):
        result = self.dropped(STRUCTURED)
        self.assertTrue(result.chat_reply.startswith(cto_agent.PLAN_DROPPED_NOTE))
        self.assertLess(result.chat_reply.index("One important correction"), result.chat_reply.index("I have prepared the plan"))
        self.assertTrue(result.reply.startswith(cto_agent.PLAN_DROPPED_NOTE))
        self.assertIn(STRUCTURED.split("\n")[0], result.chat_reply)  # the model's own friendly text is kept after it

    def test_the_correction_is_friendly_honest_and_says_nothing_is_waiting(self):
        for note in (cto_agent.PLAN_DROPPED_NOTE, cto_agent.PLAN_DROPPED_NOTE_REQUEST_KEPT):
            for needle in ("**One important correction:**", "could not be prepared", "I will read the file first"):
                self.assertIn(needle, note)
            for phrase in ("a human must", "a human should", "pending human approval", "Nothing has been changed by me"):
                self.assertNotIn(phrase, note)
        self.assertIn("Nothing is waiting for you to approve", cto_agent.PLAN_DROPPED_NOTE)
        self.assertNotIn("Nothing is waiting", cto_agent.PLAN_DROPPED_NOTE_REQUEST_KEPT)

    def test_with_no_chat_reply_from_the_model_the_reply_is_left_empty_not_replaced_by_the_correction_alone(self):
        result = self.dropped(None)
        self.assertIsNone(result.chat_reply)  # the screen falls back to the full recommendation, which starts with the correction
        self.assertTrue(result.reply.startswith(cto_agent.PLAN_DROPPED_NOTE))

    def test_a_good_answer_gets_no_correction(self):
        result, _ = self.ask([text_reply(reply_with(STRUCTURED, {NEW_FILE: "x\n"}))])
        self.assertNotIn("One important correction", result.chat_reply)
        self.assertNotIn("One important correction", result.reply)

    def test_a_dropped_plan_with_nothing_else_left_to_review_leaves_no_request_and_the_correction_says_so(self):
        # The plan was the whole request (no diff or commands), so the existing rule drops the entire request: nothing is waiting.
        result = self.dropped(STRUCTURED)
        self.assertIsNone(result.authorization_request)
        self.assertTrue(result.chat_reply.startswith(cto_agent.PLAN_DROPPED_NOTE))
        self.assertIn("Nothing is waiting for you to approve", result.chat_reply)  # and that is exactly true: no request is left

    def test_a_dropped_plan_that_still_has_a_diff_keeps_a_pending_request_that_executes_nothing(self):
        payload = answer({CHAT_PATH: "export default function AgentChat() { return null; }\n"}, with_diff=True)
        payload["chat_reply"] = STRUCTURED
        result, _ = self.ask([text_reply(payload)])
        request = result.authorization_request
        self.assertEqual((request["status"], request["executed"], request["requires_human_approval"]), ("pending_human_approval", False, True))
        self.assertIsNone(request["execution_plan"])
        # A request IS still waiting here, so the note must NOT say nothing is waiting: it says approving opens no pull request.
        self.assertTrue(result.chat_reply.startswith(cto_agent.PLAN_DROPPED_NOTE_REQUEST_KEPT))
        self.assertNotIn("Nothing is waiting for you to approve", result.chat_reply)
        self.assertIn("will NOT open a pull request", result.chat_reply)
        self.assertTrue(result.reply.startswith(cto_agent.PLAN_DROPPED_NOTE_REQUEST_KEPT))


if __name__ == "__main__":
    unittest.main()
