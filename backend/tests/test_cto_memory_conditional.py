"""
THE CTO'S MEMORY TOOLS ARE CONDITIONAL, and the memory audit log can never block an answer.

Background: a live "add a comment" task made 6 tool rounds in 90 seconds, three of them memory calls (a save and two searches). Every round carries the
whole system prompt again. Two fixes are proven here:

  1. PROMPT. The CTO's prompt tells it to call save_memory only for a genuine lesson, quirk or architectural decision, search_memory only when past
     experience is directly relevant, and neither for routine edits, file reads or simple configuration changes. The CEO's directive stays word for word,
     and the other three executives still get the shared text. A prompt is an instruction, not a gate: a test cannot make a real model obey it. So the
     tests prove (a) the instruction is in the CTO's prompt and only there, and (b) that nothing in code forces a memory round: with a model that follows
     the instruction, a routine task makes ONE model call and no memory call. The real-model behaviour is watched in the [CTO DEBUG] logs.
  2. AUDIT-LOG GUARD. memory_tools._log used to wait up to 4 s per call on a slow database. It now waits at most LOG_TIMEOUT_SECONDS and then opens a circuit
     breaker, so one answer waits for at most ONE failed write, however many memory calls it makes. The console line is always printed.

    python -m unittest tests.test_cto_memory_conditional   (from backend/)
"""

import asyncio
import contextlib
import io
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cao_agent, cmo_agent, coo_agent, cto_agent, memory_tools  # noqa: E402
from app.governance import db  # noqa: E402
from app.prompts import exec_tools_prompt as prompts  # noqa: E402
from tests.test_cto_reads_before_writing import ScriptedClient, answer, text_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

NO_DB = ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")
DIRECTIVE = ("CONTINUOUS LEARNING: When a new operational rule, lesson learned, or critical insight is established during a chat, you MUST use the "
             "save_memory tool to record it for future reference. Conversely, when reasoning about a plan, use search_memory to check if past experiences "
             "contradict or support your current approach.")
ROUTINE_TASKS = ("Add a comment above the reset handler in AgentChat.tsx", "Fix the typo in the page heading", "Change CTO_MAX_TOKENS in render.yaml to 4000",
                 "Read frontend/src/app/page.tsx and tell me what it renders", "Rename a variable in the audit browser")


def run(coro):
    return asyncio.run(coro)


def plain_answer():
    body = answer({})
    body["authorization_request"] = None
    return body


class ThePromptNarrowsTheCtosMemoryUseTests(unittest.TestCase):
    def test_the_cto_prompt_carries_the_ceos_narrowing_rule(self):
        text = cto_agent.BASE_SYSTEM_PROMPT
        for needle in ("Only call save_memory when a genuine operational lesson, system quirk, or architectural decision is established.",
                       "Only call search_memory when past experience is directly relevant to the current task.",
                       "Do NOT call these tools for routine code edits, file reads, or simple configuration changes."):
            self.assertEqual(text.count(needle), 1, needle)

    def test_the_ceos_directive_is_still_in_the_ctos_prompt_word_for_word_exactly_once_and_before_the_narrowing_rule(self):
        text = cto_agent.BASE_SYSTEM_PROMPT
        self.assertEqual(text.count(DIRECTIVE), 1)
        self.assertLess(text.index(DIRECTIVE), text.index("Only call save_memory when a genuine"))
        self.assertGreater(text.index(DIRECTIVE), text.index("END OF EXECUTIVE CONSTITUTION"))

    def test_the_old_blanket_second_paragraph_is_gone_from_the_cto_only(self):
        shared_second_paragraph = prompts.MEMORY_PROMPT.split("\n", 1)[1]
        self.assertNotIn(shared_second_paragraph, cto_agent.BASE_SYSTEM_PROMPT)
        for module in (cao_agent, coo_agent, cmo_agent):
            self.assertIn(shared_second_paragraph, module.BASE_SYSTEM_PROMPT, module.__name__)

    def test_the_other_three_executives_are_unchanged_and_never_get_the_narrowing_rule(self):
        for module in (cao_agent, coo_agent, cmo_agent):
            self.assertIn(prompts.MEMORY_PROMPT, module.BASE_SYSTEM_PROMPT, module.__name__)
            self.assertNotIn("Only call save_memory when a genuine", module.BASE_SYSTEM_PROMPT, module.__name__)

    def test_the_ctos_memory_prompt_is_the_directive_plus_the_rule_and_nothing_that_hides_reasoning(self):
        from app.agents.plan_checks import REFUSED_WORDING

        self.assertTrue(prompts.CTO_MEMORY_PROMPT.startswith(DIRECTIVE))
        for wording in REFUSED_WORDING:
            self.assertNotIn(wording, prompts.CTO_MEMORY_PROMPT.lower(), wording)
        self.assertLess(len(cto_agent.BASE_SYSTEM_PROMPT) // 4, 16_500)


class ARoutineTaskMakesNoMemoryCallTests(GuardedTestCase):
    """What the prompt is for. The model is scripted as one that FOLLOWS the instruction: the point is that nothing in code forces a memory round."""

    def ask(self, query):
        client = ScriptedClient([text_reply(plain_answer())])
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(memory_tools, "handle", mock.AsyncMock()) as handle, mock.patch.object(memory_tools, "save", mock.AsyncMock()) as save, \
                mock.patch.object(memory_tools, "search", mock.AsyncMock()) as search:
            result = run(cto_agent.get_cto_result(query, use_rag=False, audit=False))
        return client, handle, save, search, result

    def test_a_routine_code_task_makes_one_model_call_and_no_memory_call(self):
        for query in ROUTINE_TASKS:
            client, handle, save, search, result = self.ask(query)
            self.assertEqual(len(client.calls), 1, query)  # no tool round at all
            handle.assert_not_awaited()
            save.assert_not_awaited()
            search.assert_not_awaited()
            self.assertIsNotNone(result.reply, query)

    def test_code_never_forces_a_memory_call_the_memory_tools_run_only_when_the_model_asks(self):
        client, *_ = self.ask(ROUTINE_TASKS[0])
        self.assertEqual([t["name"] for t in client.calls[0]["tools"]][-2:], ["save_memory", "search_memory"])  # still offered
        self.assertNotIn("tool_choice", client.calls[0])  # never required: the model decides

    def test_the_memory_tools_still_work_when_the_model_does_ask_for_one(self):
        def memory_call(name, args, call_id):
            block = SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)
            return SimpleNamespace(content=[block], stop_reason="tool_use")

        replies = [memory_call("search_memory", {"query": "how do we handle the reset epoch"}, "tu_s"), text_reply(plain_answer())]
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        found = mock.AsyncMock(return_value=("1 note(s) from the executives' memory", True, "memory:search", 1))
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(memory_tools, "handle", found):
            run(cto_agent.get_cto_result("Why does the kill switch reset the epoch?", use_rag=False, audit=False))
        found.assert_awaited_once()
        self.assertEqual(found.await_args.args[:2], ("cto", "search_memory"))  # the tool is not removed, only discouraged for routine work
        self.assertEqual(len(client.calls), 2)


ENTRY = {"tool": "search_memory", "ok": True, "vector_id": ""}


class TheAuditLogGuardTests(unittest.TestCase):
    """memory_tools._log: a slow or failing audit write must never hold up the answer, and must never raise."""

    def setUp(self):
        memory_tools._reset_log_breaker()
        self.addCleanup(memory_tools._reset_log_breaker)
        patches = [mock.patch.dict(os.environ, {"GOVERNANCE_DATABASE_URL": "postgresql://u:p@localhost:1/x"}), mock.patch("builtins.print")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def log(self, write, timeout=0.05, cooldown=60.0):
        """Seconds the CALLER waited for one _log. Timed INSIDE the event loop: asyncio.run() itself waits for an abandoned worker thread when it shuts
        down, which the long-lived server loop never does, so timing around run() would measure the test harness and not the guard."""

        async def timed():
            started = time.perf_counter()
            await memory_tools._log("cto", dict(ENTRY))
            return time.perf_counter() - started

        with mock.patch.object(memory_tools, "_log_sync", write), mock.patch.object(memory_tools, "LOG_TIMEOUT_SECONDS", timeout), \
                mock.patch.object(memory_tools, "LOG_COOLDOWN_SECONDS", cooldown):
            return run(timed())

    def test_a_write_that_hangs_is_abandoned_at_the_timeout_and_the_answer_goes_on(self):
        with self.assertLogs("app.agents.memory_tools", level="ERROR") as logs:
            waited = self.log(lambda *a: time.sleep(0.6))
        self.assertLess(waited, 0.4)  # it did not wait for the 0.6 s write
        self.assertIn("could not log (TimeoutError)", " ".join(logs.output))

    def test_a_write_that_raises_is_swallowed_and_never_reaches_the_caller(self):
        def broken(*args):
            raise ConnectionRefusedError("down")

        with self.assertLogs("app.agents.memory_tools", level="ERROR") as logs:
            self.log(broken)  # no exception escapes
        self.assertIn("ConnectionRefusedError", " ".join(logs.output))

    def test_after_one_failure_the_next_calls_skip_the_database_so_an_answer_waits_for_at_most_one_failed_write(self):
        calls = []

        def slow(*args):
            calls.append(1)
            time.sleep(0.4)

        with self.assertLogs("app.agents.memory_tools", level="ERROR"):
            first = self.log(slow)
        second, third = self.log(slow), self.log(slow)
        self.assertEqual(len(calls), 1)  # the breaker is open: the 2nd and 3rd made no database call at all
        self.assertLess(second + third, 0.1)
        self.assertGreater(first, 0.04)

    def test_the_breaker_closes_again_after_the_cooldown_and_a_good_write_goes_through(self):
        written = []
        with self.assertLogs("app.agents.memory_tools", level="ERROR"):
            self.log(lambda *a: time.sleep(0.4), cooldown=0.1)
        time.sleep(0.15)
        self.log(lambda role, entry: written.append(entry["tool"]), cooldown=0.1)
        self.assertEqual(written, ["search_memory"])

    def test_a_healthy_database_writes_every_call_and_never_opens_the_breaker(self):
        written = []
        for _ in range(3):
            self.log(lambda role, entry: written.append(1))
        self.assertEqual(len(written), 3)
        self.assertEqual(memory_tools._log_skip_until, 0.0)

    def test_the_console_line_is_printed_even_when_the_write_is_skipped_or_there_is_no_database(self):
        printed = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(a[0])):
            memory_tools._log_skip_until = time.monotonic() + 60  # breaker open
            run(memory_tools._log("cto", dict(ENTRY)))
            memory_tools._reset_log_breaker()
            with mock.patch.object(db, "is_configured", return_value=False):
                run(memory_tools._log("cto", dict(ENTRY)))
        self.assertEqual(len(printed), 2)
        self.assertTrue(all("CTO memory: search_memory ok=True" in line for line in printed))

    def test_the_timeout_is_short_and_the_cooldown_is_bounded(self):
        self.assertLessEqual(memory_tools.LOG_TIMEOUT_SECONDS, 3.0)
        self.assertLessEqual(memory_tools.LOG_COOLDOWN_SECONDS, 120.0)

    def test_a_slow_audit_log_does_not_delay_a_memory_tool_result_through_the_real_handler(self):
        memory_tools._reset_log_breaker()
        found = SimpleNamespace(ok=True, step="", reason="", message="", hits=[])
        outcome = SimpleNamespace(memory_saves=0, memory_searches=0)
        async def three_calls():  # one event loop, as in the server
            started = time.perf_counter()
            results = [await memory_tools.handle("cto", "search_memory", {"query": f"question {i}"}, outcome) for i in range(3)]
            return results, time.perf_counter() - started

        with mock.patch.object(memory_tools, "search", mock.AsyncMock(return_value=found)), mock.patch.object(memory_tools, "_log_sync", lambda *a: time.sleep(0.5)), \
                mock.patch.object(memory_tools, "LOG_TIMEOUT_SECONDS", 0.05), self.assertLogs("app.agents.memory_tools", level="ERROR"):
            results, elapsed = run(three_calls())
        self.assertTrue(all(ok for _, ok, _, _ in results))
        self.assertLess(elapsed, 0.3)  # three memory calls, ONE failed log write (about 0.05 s), the other two skipped: nothing waited for the 0.5 s write
