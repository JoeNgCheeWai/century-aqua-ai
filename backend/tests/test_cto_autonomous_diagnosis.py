"""
The CTO's AUTONOMOUS SELF-DIAGNOSIS and its SELF-CHECK DECLARATION.

  * When a process fails, the CTO runs a real, read-only diagnostic IN THE SAME ANSWER and the cause and exact fix come first, written by
    code from the report (the model cannot soften or skip it). The prompt forbids "I can investigate if you'd like".
  * The CTO cannot run tests, so "I have verified this" is never the model's claim: CODE runs real static checks on the plan, then adds a
    declaration that lists what was checked and what was NOT run. A plan that fails a check is dropped, with the reason first.

A scripted model stands in for Anthropic and the model call the probe makes is replaced; no database, no network.

    python -m unittest tests.test_cto_autonomous_diagnosis   (from backend/)
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

from app.agents import council, cto_agent, diagnostics, plan_checks  # noqa: E402
from app.prompts import persona  # noqa: E402
from tests.test_cto_reads_before_writing import ScriptedClient, answer, text_reply, tool_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

NEW_UI = "frontend/src/components/mc/SessionsBadge.tsx"
NEW_PY = "backend/app/prompts/new_prompt.py"
QUERY = "The Boardroom returned 0 usable proposals. What happened?"
PROMPT = persona.CTO_ENGINEER_PROMPT


class TheCtoPromptEnforcesAutonomousBehaviourTests(unittest.TestCase):
    def test_it_is_told_to_investigate_in_the_same_answer_and_never_to_offer(self):
        for needle in ("AUTONOMOUS SELF-DIAGNOSIS", "you do not ask permission to look into it and you do not offer to",
                       "your FIRST reply already contains the cause and the exact fix", "call diagnose_boardroom_session at once",
                       'NEVER say "I can investigate if you\'d like"', "Never end on an offer to investigate"):
            self.assertIn(needle, PROMPT, needle)

    def test_it_is_told_to_present_the_cause_and_the_exact_fix_in_a_fixed_order(self):
        self.assertIn("what failed in one plain sentence, what I found", PROMPT)
        self.assertIn("the exact fix and who does it", PROMPT)

    def test_it_is_told_to_be_honest_about_what_it_ran_and_that_it_cannot_run_tests_or_scripts(self):
        self.assertIn("Say you ran diagnostics ONLY if you called the tool", PROMPT)
        self.assertIn("Never claim to have run a script, a test or a build", PROMPT)

    def test_it_is_told_the_system_writes_the_verification_line_and_it_must_not(self):
        for needle in ("SELF-CHECK BEFORE AN EXECUTION PLAN", "you must never write that you have verified, tested or confirmed a solution",
                       'adds a "Self-check" line to your reply that lists exactly what was checked and what was not',
                       "do not write your own verification sentence", "the database schema (frontend/prisma/schema.prisma)"):
            self.assertIn(needle, PROMPT, needle)

    def test_it_is_not_told_to_hide_its_reasoning_the_wording_the_real_model_refused(self):
        lowered = PROMPT.lower()
        for phrase in plan_checks.REFUSED_WORDING:
            self.assertNotIn(phrase, lowered, phrase)

    def test_it_must_not_claim_the_coo_approved_or_label_a_task_safe_itself(self):
        self.assertIn('Never label a task "safe", "minor" or "approved" yourself', PROMPT)

    def test_the_one_tool_wording_is_gone_it_now_says_two_read_only_tools(self):
        self.assertIn("You have TWO tools and both only READ", PROMPT)
        self.assertNotIn("You have ONE tool", PROMPT)


class TheDiagnosticFindsTheCauseInCodeTests(unittest.TestCase):
    def test_a_refusal_in_the_probe_is_diagnosed_as_confirmed_with_the_exact_fix(self):
        cause = diagnostics.derive_cause(None, [], [{"seat": "cao", "stop_reason": "refusal", "usable": False}])
        self.assertEqual(cause["confidence"], "confirmed")
        self.assertIn("refusing", cause["cause"])
        self.assertIn("council_prompts.py", cause["fix"])
        self.assertIn("try_boardroom_turn.py", cause["fix"])

    def test_refused_wording_found_in_a_prompt_is_confirmed_and_named(self):
        cause = diagnostics.derive_cause(None, ["TURN_CONTRACT contains 'private working'"], None)
        self.assertEqual(cause["confidence"], "confirmed")
        self.assertIn("private working", cause["fix"])

    def test_a_recorded_refusal_that_nothing_reproduces_is_only_likely_and_never_called_confirmed(self):
        cause = diagnostics.derive_cause({"reason": "only 0 usable proposals (2 reply(ies) refused by the model)"}, [],
                                         [{"seat": "cao", "stop_reason": "end_turn", "usable": True}])
        self.assertEqual(cause["confidence"], "likely")
        self.assertIn("may already be gone", cause["fix"])

    def test_truncation_a_failed_call_and_bad_json_each_get_their_own_cause(self):
        cut = diagnostics.derive_cause(None, [], [{"seat": "coo", "stop_reason": "max_tokens", "usable": False}])
        self.assertIn("COUNCIL_TURN_MAX_TOKENS", cut["fix"])
        err = diagnostics.derive_cause(None, [], [{"seat": "coo", "error": "TimeoutError", "usable": False}])
        self.assertIn("ANTHROPIC_API_KEY", err["fix"])
        bad = diagnostics.derive_cause(None, [], [{"seat": "coo", "stop_reason": "end_turn", "usable": False}])
        self.assertIn("TURN_CONTRACT", bad["fix"])

    def test_when_everything_works_it_says_it_could_not_reproduce_it_and_never_invents_a_cause(self):
        cause = diagnostics.derive_cause(None, [], [{"seat": "cao", "stop_reason": "end_turn", "usable": True}])
        self.assertEqual(cause["confidence"], "unknown")
        self.assertIn("could not reproduce", cause["cause"])
        self.assertIn("cannot see Render's logs", cause["fix"])

    def test_the_stored_reason_is_parsed_into_counts(self):
        counts = diagnostics.parse_reason("(2 reply(ies) refused by the model) (1 call(s) failed or timed out)")
        self.assertEqual(counts, {"refused": 2, "errored": 1})

    def test_the_real_council_prompts_contain_none_of_the_refused_wording(self):
        self.assertEqual(diagnostics.scan_prompts(), [])

    def test_the_scan_really_finds_refused_wording_when_it_is_present(self):
        from app.prompts import council_prompts

        with mock.patch.object(council_prompts, "TURN_CONTRACT", council_prompts.TURN_CONTRACT + "\nWrite your private working here."):
            hits = diagnostics.scan_prompts()
        self.assertTrue(any("TURN_CONTRACT" in h and "private working" in h for h in hits), hits)


class ProbeWorld:
    """Stands in for what the probe calls (the model and the parser) and for the session record, so no network or database is used."""

    def __init__(self, stop_reason="end_turn", valid=True, error=None):
        self.stop_reason, self.valid, self.error, self.calls = stop_reason, valid, error, 0

    async def call_model(self, system, user, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(text="reply", stop_reason=self.stop_reason, output_tokens=900, truncated=self.stop_reason == "max_tokens")

    async def read_session(self, session_id):
        return ({"session_id": "11111111-1111-1111-1111-111111111111", "status": "FAILED", "seats": ["cao", "coo"], "model_calls": 4,
                 "total_tokens": 0, "turns_by_round": {1: 0}, "reason": "only 0 usable proposals (2 reply(ies) refused by the model)"}, "")

    def __enter__(self):
        self.patches = [mock.patch.object(council, "call_model", self.call_model),
                        mock.patch.object(council, "normalize_turn", lambda agent, text: {"valid": self.valid, "reasoning": "x" if self.valid else ""}),
                        mock.patch.object(diagnostics, "read_session", self.read_session), mock.patch.object(diagnostics, "_probe_times", [])]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()


def diagnosing_reply(chat_text):
    payload = answer({})
    payload["authorization_request"] = None
    payload["chat_reply"] = chat_text
    return payload


def diagnostic_call(*, count=1, **tool_input):
    """The model asking for the diagnostic ``count`` times in one step."""
    blocks = [SimpleNamespace(type="tool_use", id=f"tu_{i}", name="diagnose_boardroom_session", input=tool_input) for i in range(count)]
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


class TheDiagnosticRunsInTheFirstAnswerTests(GuardedTestCase):
    def ask(self, replies, world, query=QUERY):
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        with world, mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(cto_agent.get_cto_result(query, use_rag=False, audit=False, memory=False))
        return result, client

    def test_a_failed_session_triggers_the_real_diagnostic_and_the_fix_is_in_the_first_reply(self):
        world = ProbeWorld(stop_reason="refusal", valid=False)
        result, client = self.ask([diagnostic_call(), text_reply(diagnosing_reply("Here is what I found."))], world)
        self.assertEqual(world.calls, 2)  # the probe really ran: one real call per seat (cao, coo)
        self.assertTrue(result.reply.startswith("### What I found (diagnostics already run)"))
        self.assertIn("I ran the Boardroom diagnostic myself just now", result.reply)
        self.assertIn("**The exact fix:**", result.reply)
        self.assertIn("council_prompts.py", result.reply)
        self.assertTrue(result.chat_reply.startswith("### What I found (diagnostics already run)"))
        sent = client.calls[1]["messages"][-1]["content"][0]["content"]  # the report the model was shown is code-written facts
        self.assertIn("DIAGNOSTIC REPORT (produced by code that just ran", sent)

    def test_code_puts_the_findings_first_even_if_the_model_wrongly_offers_to_investigate(self):
        world = ProbeWorld(stop_reason="refusal", valid=False)
        result, _ = self.ask([diagnostic_call(), text_reply(diagnosing_reply("I can investigate if you'd like. Just say the word."))], world)
        self.assertLess(result.chat_reply.index("The exact fix"), result.chat_reply.index("I can investigate if you'd like"))

    def test_the_reply_says_only_what_the_diagnostic_really_ran_including_what_it_could_not_see(self):
        world = ProbeWorld()
        result, _ = self.ask([diagnostic_call(run_probe=False), text_reply(diagnosing_reply("It looks fine."))], world)
        self.assertEqual(world.calls, 0)  # run_probe false: no model calls were spent
        self.assertIn("did not probe the model (run_probe was false)", result.reply)
        self.assertIn("I cannot see Render's logs", result.reply)

    def test_the_diagnostic_runs_at_most_once_per_answer_and_a_second_request_is_refused(self):
        world = ProbeWorld(stop_reason="refusal", valid=False)
        result, client = self.ask([diagnostic_call(count=2), text_reply(diagnosing_reply("ok"))], world)
        self.assertEqual(world.calls, 2)  # one diagnostic = one probe per seat, not two
        results = client.calls[1]["messages"][-1]["content"]
        self.assertTrue(results[1]["is_error"])
        self.assertIn("already ran", results[1]["content"])

    def test_the_probe_is_rate_limited_per_hour_and_says_so(self):
        world = ProbeWorld()
        with world:
            for _ in range(diagnostics.PROBES_PER_HOUR):
                asyncio.run(diagnostics.diagnose(None, True))
            limited = asyncio.run(diagnostics.diagnose(None, True))
        self.assertEqual(world.calls, 2 * diagnostics.PROBES_PER_HOUR)
        self.assertTrue(any("already probed" in step for step in limited["steps"]))

    def test_a_failing_model_call_in_the_probe_is_reported_as_an_error_never_raised(self):
        world = ProbeWorld(error=TimeoutError("slow"))
        with world:
            report = asyncio.run(diagnostics.diagnose(None, True))
        self.assertTrue(report["ok"])
        self.assertTrue(all(p["error"] == "TimeoutError" for p in report["probes"]))

    def test_the_report_carries_counts_and_stop_reasons_never_the_text_a_model_wrote(self):
        world = ProbeWorld()
        world_text = "SECRET-MODEL-TEXT-" + "x" * 40

        async def talkative(system, user, **kwargs):
            return SimpleNamespace(text=world_text, stop_reason="end_turn", output_tokens=5, truncated=False)

        with world, mock.patch.object(council, "call_model", talkative):
            report = asyncio.run(diagnostics.diagnose(None, True))
        self.assertNotIn(world_text, diagnostics.render_report(report))
        self.assertNotIn(world_text, repr(report))

    def test_an_invalid_session_id_is_refused_without_touching_the_database(self):
        session, note = asyncio.run(diagnostics.read_session("not-a-uuid'; DROP TABLE x;--"))
        self.assertIsNone(session)
        self.assertIn("not a valid session id", note)


GOOD_PY = "GREETING = 'hello'\n\n\ndef shout(text):\n    return text.upper()\n"
SCHEMA_PATH = "frontend/prisma/schema.prisma"


class TheSelfCheckDeclarationIsWrittenByCodeAfterRealChecksTests(GuardedTestCase):
    def ask(self, replies, query="Add a small prompt helper."):
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(cto_agent.get_cto_result(query, use_rag=False, audit=False, memory=False)), client

    def test_a_plan_that_passes_gets_a_declaration_that_lists_what_was_checked_and_what_was_not(self):
        result, _ = self.ask([text_reply(answer({NEW_PY: GOOD_PY}))])
        self.assertIsNotNone(result.authorization_request["execution_plan"])
        for text in (result.reply, result.chat_reply):
            self.assertIn("**Self-check:** I have run these checks on the plan", text)
            self.assertIn("it parses as Python", text)
            self.assertIn("I have not run the full test suite or the frontend build", text)
            self.assertIn("they run on the pull request before anything is merged", text)

    def test_it_never_claims_the_one_sentence_it_cannot_back_up(self):
        result, _ = self.ask([text_reply(answer({NEW_PY: GOOD_PY}))])
        for claim in ("verified this solution against our current schema/tests", "maintains system integrity"):
            self.assertNotIn(claim, result.reply)
            self.assertNotIn(claim, result.chat_reply)

    def test_the_declaration_names_the_schema_and_tests_only_when_they_were_really_read(self):
        none_read, _ = self.ask([text_reply(answer({NEW_PY: GOOD_PY}))])
        self.assertNotIn("I also read", none_read.reply)
        with mock.patch.object(cto_agent.tools, "github_read_file", lambda path, branch="main": "model Thing { id Int }\n" if "schema" in path else "x = 1\n"):
            read, _ = self.ask([tool_reply(SCHEMA_PATH, "backend/tests/test_thing.py"), text_reply(answer({NEW_PY: GOOD_PY}))])
        self.assertIn("I also read the database schema and the existing tests", read.reply)

    def test_a_plan_with_a_python_syntax_error_is_dropped_and_the_reason_comes_first(self):
        result, _ = self.ask([text_reply(answer({NEW_PY: "def broken(:\n    pass\n"}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])
        self.assertTrue(result.reply.startswith("**One important correction:** my own checks found a problem"))
        self.assertIn("Python syntax error", result.reply)
        self.assertNotIn("Self-check:** I have run", result.reply)  # a failed plan is never "verified"

    def test_a_plan_cut_short_with_a_placeholder_is_dropped_because_overwriting_would_delete_code(self):
        cut = "export default function A() {\n  // ... rest of the file\n}\n"
        result, _ = self.ask([text_reply(answer({NEW_UI: cut}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])
        self.assertIn("placeholder comment", result.reply)

    def test_a_rewrite_that_shrinks_a_file_that_was_read_is_dropped_as_truncated(self):
        original = "line of code that matters\n" * 100
        with mock.patch.object(cto_agent.tools, "github_read_file", lambda path, branch="main": original):
            result, _ = self.ask([tool_reply(NEW_PY), text_reply(answer({NEW_PY: "short\n"}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])
        self.assertIn("truncated rewrite", result.reply)

    def test_a_prompt_file_with_the_wording_the_real_model_refused_is_dropped(self):
        bad = "PROMPT = 'Write your private working here, it sits behind a toggle.'\n"
        result, _ = self.ask([text_reply(answer({NEW_PY: bad}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])
        self.assertIn("refused in the Boardroom", result.reply)

    def test_a_failed_plan_with_nothing_else_to_review_drops_the_whole_request(self):
        result, _ = self.ask([text_reply(answer({NEW_PY: "def broken(:\n"}))])
        self.assertIsNone(result.authorization_request)

    def test_a_reply_with_no_plan_gets_no_declaration(self):
        payload = answer({})
        payload["authorization_request"] = None
        result, _ = self.ask([text_reply(payload)])
        self.assertNotIn("Self-check", result.reply)


class ThePlanChecksThemselvesTests(unittest.TestCase):
    def plan(self, path, content):
        return {"branch_name": "agent/x", "pr_title": "t", "pr_body": "", "files_to_update": [{"path": path, "content": content, "commit_message": "m"}]}

    def test_an_additive_migration_passes_and_each_destructive_one_fails(self):
        path = "frontend/prisma/migrations/20261115000000_add_note/migration.sql"
        good = 'CREATE TABLE "Note" ("id" SERIAL NOT NULL, "text" TEXT NOT NULL);\nCREATE INDEX "Note_text_idx" ON "Note"("text");\n'
        self.assertTrue(plan_checks.check_plan(self.plan(path, good)).passed)
        for bad in ('DROP TABLE "Note";', 'ALTER TABLE "Note" DROP COLUMN "text";', 'DELETE FROM "Note";', 'UPDATE "Note" SET "text"=\'x\';',
                    'ALTER TABLE "Note" ADD COLUMN "age" INTEGER NOT NULL;', 'ALTER TABLE "Note" RENAME COLUMN "a" TO "b";', 'TRUNCATE "Note";'):
            self.assertFalse(plan_checks.check_plan(self.plan(path, bad)).passed, bad)

    def test_a_nullable_or_defaulted_new_column_is_allowed(self):
        path = "frontend/prisma/migrations/20261115000000_add_col/migration.sql"
        self.assertTrue(plan_checks.check_plan(self.plan(path, 'ALTER TABLE "Note" ADD COLUMN "age" INTEGER;')).passed)
        self.assertTrue(plan_checks.check_plan(self.plan(path, 'ALTER TABLE "Note" ADD COLUMN "n" INTEGER NOT NULL DEFAULT 0;')).passed)

    def test_a_schema_that_removes_or_changes_a_line_fails_but_one_that_only_adds_passes(self):
        old = "model A {\n  id Int @id\n  name String\n}\n"
        added = "model A {\n  id Int @id\n  name String\n  note String?\n}\n"
        removed = "model A {\n  id Int @id\n}\n"
        self.assertTrue(plan_checks.check_plan(self.plan(SCHEMA_PATH, added), {SCHEMA_PATH: old}).passed)
        self.assertFalse(plan_checks.check_plan(self.plan(SCHEMA_PATH, removed), {SCHEMA_PATH: old}).passed)

    def test_invalid_json_and_merge_conflict_markers_fail(self):
        self.assertFalse(plan_checks.check_plan(self.plan("frontend/data/x.json", "{not json")).passed)
        self.assertFalse(plan_checks.check_plan(self.plan(NEW_UI, "a\n<<<<<<< HEAD\nb\n=======\nc\n>>>>>>> x\n")).passed)

    def test_a_checker_that_crashes_never_passes_a_plan(self):
        broken = {"files_to_update": [{"path": 5, "content": None}]}
        result = plan_checks.check_plan(broken)
        self.assertFalse(result.passed)
        self.assertEqual(plan_checks.declaration(result, False, False), "")  # no declaration unless it passed


if __name__ == "__main__":
    unittest.main()
