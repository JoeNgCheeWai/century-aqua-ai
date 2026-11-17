"""
THE CTO'S HARD max_tokens CAP (2000): every model call the CTO makes is capped, and nothing in the environment can raise the cap.

Why: a 16384-token answer could run for minutes, hit the web host's timeout or be cut off mid-JSON, and a cut-off answer loses its
authorization_request, which leaves the CEO asking again. A concise plan fits in 2000 tokens and finishes in seconds. The "full code" step is a
separate conversational turn with its own fresh 2000-token budget, so no retry logic is needed.

Proven here with a scripted model (no network):
  * the default is 2000, and CTO_MAX_TOKENS in the environment can lower the cap but never raise it (a stale 16384 on the host is clamped);
  * a bad value (not a number, zero, negative, empty) falls back to the cap;
  * the cap is on the first call of the tool loop AND on every later round, on the final tools-off call, and on the no-tools review path;
  * an answer that still hits the cap is handled as before: the length-limit note is added and no half-built authorization request is invented.

    python -m unittest tests.test_cto_max_tokens_cap   (from backend/)
"""

import asyncio
import contextlib
import importlib
import io
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

from app.agents import cto_agent  # noqa: E402
from tests.test_cto_reads_before_writing import ScriptedClient, answer, text_reply, tool_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

CAP = 2000
NO_DB = ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")


def run(coro):
    return asyncio.run(coro)


def reloaded(value):
    """cto_agent re-imported with CTO_MAX_TOKENS set to ``value`` (None means unset), then restored. Returns the cap it computed."""
    env = {k: v for k, v in os.environ.items() if k != "CTO_MAX_TOKENS"}
    if value is not None:
        env["CTO_MAX_TOKENS"] = value
    try:
        with mock.patch.dict(os.environ, env, clear=True):
            return importlib.reload(cto_agent).CTO_MAX_TOKENS
    finally:
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "CTO_MAX_TOKENS"}, clear=True):
            importlib.reload(cto_agent)


class TheCapIsAHardCeilingTests(unittest.TestCase):
    def test_the_default_is_2000_and_the_ceiling_is_2000(self):
        self.assertEqual((cto_agent.DEFAULT_CTO_MAX_TOKENS, cto_agent.CTO_MAX_TOKENS_CEILING), (CAP, CAP))
        self.assertEqual(reloaded(None), CAP)

    def test_the_environment_can_lower_the_cap(self):
        self.assertEqual(reloaded("1200"), 1200)
        self.assertEqual(reloaded("1"), 1)

    def test_the_environment_can_never_raise_it_so_a_stale_host_value_cannot_undo_the_cap(self):
        for stale in ("16384", "8192", "2001", "999999999"):
            self.assertEqual(reloaded(stale), CAP, stale)

    def test_a_bad_value_falls_back_to_the_cap(self):
        for bad in ("", "abc", "0", "-5", "1.5", " "):
            self.assertEqual(reloaded(bad), CAP, repr(bad))

    def test_the_module_is_back_to_normal_after_these_tests_reloaded_it(self):
        self.assertEqual(cto_agent.CTO_MAX_TOKENS, CAP)


class EveryModelCallIsCappedTests(GuardedTestCase):
    def ask(self, replies, **kwargs):
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            result = run(cto_agent.get_cto_result("Add a small button.", use_rag=False, audit=False, **kwargs))
        return result, client

    def test_the_first_call_of_the_tool_loop_is_capped(self):
        _, client = self.ask([text_reply(answer({}))])
        self.assertEqual([c["max_tokens"] for c in client.calls], [CAP])

    def test_every_round_of_the_tool_loop_is_capped_including_after_file_reads(self):
        _, client = self.ask([tool_reply("frontend/src/components/mc/AgentChat.tsx"), tool_reply("README.md"), text_reply(answer({}))])
        self.assertEqual(len(client.calls), 3)
        self.assertEqual({c["max_tokens"] for c in client.calls}, {CAP})

    def test_the_last_call_with_tools_switched_off_is_capped_too(self):
        keeps_asking = [tool_reply("README.md") for _ in range(cto_agent.MAX_TOOL_ROUNDS + 1)]
        _, client = self.ask([*keeps_asking, text_reply(answer({}))])
        self.assertEqual(client.calls[-1].get("tool_choice"), {"type": "none"})
        self.assertEqual({c["max_tokens"] for c in client.calls}, {CAP})

    def test_the_no_tools_review_path_is_capped(self):
        _, client = self.ask([text_reply(answer({}))], reader=False)
        self.assertEqual([c["max_tokens"] for c in client.calls], [CAP])
        self.assertNotIn("tools", client.calls[0])

    def test_a_lowered_cap_applies_to_every_round_the_same_way(self):
        client = ScriptedClient([tool_reply("README.md"), text_reply(answer({}))])
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.object(cto_agent, "CTO_MAX_TOKENS", 900), \
                mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            run(cto_agent.get_cto_result("Add a small button.", use_rag=False, audit=False))
        self.assertEqual({c["max_tokens"] for c in client.calls}, {900})


class AnAnswerThatStillHitsTheCapTests(GuardedTestCase):
    """The prompt asks for concise answers, so hitting 2000 means it ignored that. The result must still be safe, never a half-built request."""

    def cut_result(self):
        full = json.dumps(answer({"frontend/src/components/mc/New.tsx": "export default function New() { return null; }\n"}))
        cut = full[: full.index('"authorization_request"') + 40]  # cut inside the request, as a token limit would
        client = ScriptedClient([SimpleNamespace(content=[SimpleNamespace(type="text", text=cut)], stop_reason="max_tokens")])
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            return run(cto_agent.get_cto_result("Add a small button.", use_rag=False, audit=False))

    def test_no_half_built_authorization_request_is_produced_and_the_ceo_is_told(self):
        result = self.cut_result()
        self.assertIsNone(result.authorization_request)
        self.assertIn("length limit", result.reply)
        self.assertIn("was NOT recovered", result.reply)
        self.assertIn("Ask me to continue", result.reply)


if __name__ == "__main__":
    unittest.main()
