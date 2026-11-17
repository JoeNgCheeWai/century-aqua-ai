"""
The CTO's [CTO DEBUG] diagnostic lines: every model call prints what is REALLY sent (max_tokens next to the raw CTO_MAX_TOKENS variable) before the
call, and stop_reason, usage and seconds after it. They go through one helper (cto_agent._logged_create), so no call path can skip them.

    python -m unittest tests.test_cto_debug_logging   (from backend/)
"""

import asyncio
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cto_agent  # noqa: E402
from tests.test_cto_reads_before_writing import ScriptedClient, answer, text_reply, tool_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

NO_DB = ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")
SECRET_QUESTION = "SECRET-QUESTION-TEXT-12345"


def reply_with_usage(reply):
    reply.usage = SimpleNamespace(input_tokens=1234, output_tokens=567)
    return reply


class DebugLoggingTests(GuardedTestCase):
    def ask(self, replies, env_var="4000", **kwargs):
        client = ScriptedClient([reply_with_usage(r) for r in replies])
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        env["CTO_MAX_TOKENS"] = env_var
        out = io.StringIO()
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            asyncio.run(cto_agent.get_cto_result(SECRET_QUESTION, use_rag=False, audit=False, **kwargs))
        return [line for line in out.getvalue().splitlines() if "[CTO DEBUG]" in line], client

    def test_each_call_prints_the_sent_max_tokens_beside_the_env_var_and_then_the_stop_reason_and_usage(self):
        lines, client = self.ask([text_reply(answer({}))], env_var="4000")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(lines), 2)
        self.assertIn("Calling model with max_tokens=4000, env_var=4000", lines[0])
        self.assertIn("Response complete. stop_reason=end_turn", lines[1])
        self.assertIn("input_tokens=1234", lines[1])
        self.assertIn("output_tokens=567", lines[1])
        self.assertIn("seconds=", lines[1])

    def test_a_stale_env_var_is_visible_next_to_the_value_that_was_actually_sent(self):
        lines, client = self.ask([text_reply(answer({}))], env_var="16384")
        self.assertEqual(client.calls[0]["max_tokens"], 4000)  # clamped by the ceiling
        self.assertIn("max_tokens=4000, env_var=16384", lines[0])

    def test_every_round_of_the_tool_loop_is_logged_in_pairs_with_its_own_label(self):
        lines, client = self.ask([tool_reply("README.md"), tool_reply("README.md"), text_reply(answer({}))])
        self.assertEqual(len(client.calls), 3)
        before = [line for line in lines if "Calling model" in line]
        after = [line for line in lines if "Response complete" in line]
        self.assertEqual((len(before), len(after)), (3, 3))
        self.assertTrue(all("max_tokens=4000" in line for line in before))
        for label in ("call=initial", "call=tool-round-1", "call=tool-round-2"):
            self.assertTrue(any(label in line for line in before), label)
        self.assertIn("stop_reason=tool_use", after[0])

    def test_the_final_tools_off_call_and_the_no_tools_review_are_logged_too(self):
        lines, _ = self.ask([*[tool_reply("README.md") for _ in range(cto_agent.MAX_TOOL_ROUNDS + 1)], text_reply(answer({}))])
        self.assertTrue(any("call=final-tools-off" in line and "Calling model" in line for line in lines))
        review, _ = self.ask([text_reply(answer({}))], reader=False)
        self.assertTrue(any("call=review-no-tools" in line and "tools_offered=0" in line for line in review))

    def test_the_lines_never_contain_the_question_or_any_prompt_text(self):
        lines, _ = self.ask([text_reply(answer({}))])
        joined = "\n".join(lines)
        self.assertNotIn(SECRET_QUESTION, joined)
        self.assertNotIn("GITHUB_PAT", joined)
        self.assertRegex(lines[0], r"input_chars=\d+")

    def test_an_unset_env_var_prints_as_none_rather_than_failing(self):
        client = ScriptedClient([reply_with_usage(text_reply(answer({})))])
        env = {k: v for k, v in os.environ.items() if k not in NO_DB and k != "CTO_MAX_TOKENS"}
        out = io.StringIO()
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            asyncio.run(cto_agent.get_cto_result("hi", use_rag=False, audit=False))
        self.assertIn("env_var=None", out.getvalue())

    def test_a_response_with_no_usage_still_logs_instead_of_crashing(self):
        client = ScriptedClient([text_reply(answer({}))])  # no .usage attribute at all
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        out = io.StringIO()
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            asyncio.run(cto_agent.get_cto_result("hi", use_rag=False, audit=False))
        self.assertIn("usage=None", out.getvalue())


if __name__ == "__main__":
    unittest.main()
