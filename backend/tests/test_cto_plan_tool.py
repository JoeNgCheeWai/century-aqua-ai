"""
THE CTO'S submit_plan_for_approval TOOL (app/agents/cto_tools.py) and the CONDITIONAL RAG GATE (cto_agent.needs_domain_knowledge).

Proven here with a scripted model, no database and no network:
  * the tool is on the CTO's tool list and on nobody else's (the COO's approval tools and the other roles' lists are unchanged);
  * it validates in code: summary length (500 words), paths an agent may not write, at most 8 files, secrets, bad risk levels, and it raises the risk by
    the same floors the JSON route uses; status/executed are forced and no execution_plan is ever built from it;
  * a plan submitted through it is stored with the answer, and SURVIVES a final answer that is cut off at the token limit, which is the whole point;
  * the request number in the reply is the real audit id, written by code; with no audit row it says so instead of inventing one;
  * a request in the answer's own JSON (one carrying file contents) is kept over the tool's, and it is one request per answer;
  * RAG runs only when the message asks for domain knowledge, never for a code edit, a file read or a configuration change.

    python -m unittest tests.test_cto_plan_tool   (from backend/)
"""

import asyncio
import contextlib
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

from app.agents import cto_agent, cto_tools, exec_tools  # noqa: E402
from tests.test_cto_reads_before_writing import ScriptedClient, answer, text_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

NO_DB = ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")
PLAN = "Add a Clear all button to the chat. Put it beside Send, wire it to the existing reset handler, and leave the layout alone."
PATHS = ["frontend/src/components/mc/AgentChat.tsx", "frontend/src/components/mc/ClearButton.tsx"]


def run(coro):
    return asyncio.run(coro)


def plan_call(plan=PLAN, files=None, risk="low", call_id="tu_plan"):
    args = {"plan_summary": plan, "files_to_change": PATHS if files is None else files, "risk_level": risk}
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id=call_id, name="submit_plan_for_approval", input=args)], stop_reason="tool_use")


class TheToolValidatesInCodeTests(unittest.TestCase):
    def submit(self, **args):
        out = cto_agent.ToolOutcome()
        text, ok = cto_tools.submit({"plan_summary": PLAN, "files_to_change": PATHS, "risk_level": "low", **args}, out)
        return text, ok, out

    def test_a_good_plan_is_staged_pending_with_the_status_fields_forced_and_no_file_plan(self):
        text, ok, out = self.submit()
        self.assertTrue(ok, text)
        request = out.plan_request
        self.assertEqual((request["status"], request["requires_human_approval"], request["executed"]), ("pending_human_approval", True, False))
        self.assertIsNone(request["execution_plan"])  # approving this runs nothing: no file contents, no pull request
        self.assertEqual((request["files_to_change"], request["submitted_via"]), (PATHS, "submit_plan_for_approval"))
        self.assertEqual(request["proposed_commands"], [])
        self.assertIn("Files the plan would change: " + PATHS[0], request["impact"])
        self.assertIn("NOT approved", text)
        self.assertIn("adds its number to your reply", text)

    def test_the_request_has_every_field_the_authorizations_page_and_the_rest_of_the_system_read(self):
        _, _, out = self.submit()
        request = out.plan_request
        for key in ("action_summary", "executive_summary", "risk_level", "impact", "proposed_code_diff", "proposed_commands", "rollback_plan",
                    "execution_plan", "status", "requires_human_approval", "executed"):
            self.assertIn(key, request, key)
        self.assertTrue(request["action_summary"].startswith("Add a Clear all button"))
        self.assertLessEqual(len(request["action_summary"]), cto_tools.MAX_ACTION_CHARS)

    def test_the_summary_limit_is_500_words_and_a_long_one_is_refused_with_how_to_fix_it(self):
        self.assertTrue(self.submit(plan_summary=" ".join(["word"] * 500))[1])
        text, ok, out = self.submit(plan_summary=" ".join(["word"] * 501))
        self.assertFalse(ok)
        self.assertIn("501 words", text)
        self.assertIn("limit is 500", text)
        self.assertIsNone(out.plan_request)

    def test_an_empty_or_non_string_summary_is_refused(self):
        for bad in ("", "   ", None, 5, ["a plan"], {"x": 1}):
            _, ok, out = self.submit(plan_summary=bad)
            self.assertFalse(ok, repr(bad))
            self.assertIsNone(out.plan_request)

    def test_a_secret_in_the_summary_is_refused_not_stored(self):
        text, ok, out = self.submit(plan_summary=PLAN + " Use key sk-ant-" + "A" * 30)
        self.assertFalse(ok)
        self.assertIn("secret", text)
        self.assertIsNone(out.plan_request)

    def test_a_file_an_agent_may_never_write_is_refused_as_it_is_in_a_plan(self):
        for path in ("backend/app/governance/audit.py", ".github/workflows/ci.yml", "backend/.env", "backend/app/agents/tools.py", "../etc/passwd", "/abs/x.py", "a\\b.py"):
            text, ok, out = self.submit(files_to_change=[path])
            self.assertFalse(ok, path)
            self.assertIn("files_to_change was refused", text)
            self.assertIsNone(out.plan_request, path)

    def test_files_are_limited_deduplicated_and_may_be_path_objects_or_empty_but_not_a_bare_string(self):
        text, ok, _ = self.submit(files_to_change=[f"frontend/src/f{i}.ts" for i in range(cto_tools.MAX_FILES + 1)])
        self.assertFalse(ok)
        self.assertIn("the most a plan may change", text)
        _, ok, out = self.submit(files_to_change=[PATHS[0], PATHS[0].replace("frontend", "FRONTEND"), {"path": PATHS[1]}])
        self.assertTrue(ok)
        self.assertEqual(len(out.plan_request["files_to_change"]), 2)
        _, ok, out = self.submit(files_to_change=[])
        self.assertTrue(ok)
        self.assertIn("No files are named", out.plan_request["impact"])
        for bad in ("a.py", None, 7, {"path": "x"}):
            self.assertFalse(self.submit(files_to_change=bad)[1], repr(bad))

    def test_risk_is_raised_by_the_same_floors_and_never_lowered_and_a_bad_level_becomes_high(self):
        self.assertEqual(self.submit(risk_level="low")[2].plan_request["risk_level"], "low")
        self.assertEqual(self.submit(risk_level="critical")[2].plan_request["risk_level"], "critical")  # never lowered
        _, _, out = self.submit(files_to_change=["backend/app/api/new_route.py"], risk_level="low")
        self.assertEqual(out.plan_request["risk_level"], "high")  # a backend file is at least high
        _, _, out = self.submit(files_to_change=["frontend/prisma/schema.prisma"], risk_level="low")
        self.assertEqual(out.plan_request["risk_level"], "high")
        _, _, out = self.submit(plan_summary=PLAN + " Then run git push --force origin main.", risk_level="low")
        self.assertEqual(out.plan_request["risk_level"], "critical")  # the text floor
        for bad in ("urgent", None, "", 3):
            self.assertEqual(self.submit(risk_level=bad)[2].plan_request["risk_level"], "high", repr(bad))

    def test_one_plan_per_answer_and_a_limit_on_attempts_and_a_refused_call_can_be_corrected(self):
        out = cto_agent.ToolOutcome()
        args = {"plan_summary": PLAN, "files_to_change": PATHS, "risk_level": "low"}
        self.assertFalse(cto_tools.submit({**args, "plan_summary": ""}, out)[1])
        self.assertTrue(cto_tools.submit(args, out)[1])  # a corrected call is accepted
        text, ok = cto_tools.submit({**args, "plan_summary": "a different plan"}, out)
        self.assertFalse(ok)
        self.assertIn("already submitted", text)
        self.assertEqual(out.plan_request["plan_summary"], PLAN)  # the first accepted plan stands
        capped = cto_agent.ToolOutcome()
        for _ in range(cto_tools.MAX_ATTEMPTS):
            cto_tools.submit({**args, "plan_summary": ""}, capped)
        text, ok = cto_tools.submit(args, capped)
        self.assertFalse(ok)
        self.assertIn("limit reached", text)
        self.assertIsNone(capped.plan_request)

    def test_a_crash_inside_the_tool_never_escapes_it(self):
        with mock.patch.object(cto_tools, "build_request", side_effect=RuntimeError("boom")):
            text, ok = cto_tools.submit({}, cto_agent.ToolOutcome())
        self.assertFalse(ok)
        self.assertTrue(text.startswith("ERROR:"))


class OnlyTheCtoHoldsTheToolTests(unittest.TestCase):
    def test_it_is_on_the_ctos_list_after_its_read_tools_and_before_memory(self):
        names = [t["name"] for t in [cto_agent.tools.READ_TOOL_SPEC, cto_agent.diagnostics.TOOL_SPEC, cto_tools.SPEC, *cto_agent.memory_tools.SPECS]]
        self.assertEqual(names, ["github_read_file", "diagnose_boardroom_session", "submit_plan_for_approval", "save_memory", "search_memory"])

    def test_no_other_executive_is_offered_it_and_the_coos_approval_tools_are_unchanged(self):
        for role in ("cao", "coo", "cmo", "cto", "ceo", ""):
            self.assertNotIn("submit_plan_for_approval", exec_tools.tool_names(role), role)
        self.assertIn("review_cto_task", exec_tools.tool_names("coo"))

    def test_the_schema_requires_the_three_fields_and_limits_the_risk_values(self):
        schema = cto_tools.SPEC["input_schema"]
        self.assertEqual(schema["required"], ["plan_summary", "files_to_change", "risk_level"])
        self.assertEqual(schema["properties"]["risk_level"]["enum"], ["low", "medium", "high", "critical"])
        self.assertIn("500 words", cto_tools.SPEC["description"])


def plain_answer():
    """A valid CTO answer that carries NO authorization_request: the plan travels through the tool instead."""
    body = answer({})
    body["authorization_request"] = None
    return body


class ThroughTheRealCtoLoopTests(GuardedTestCase):
    def ask(self, replies, audit_id=None, **kwargs):
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        recorded = mock.AsyncMock(return_value=(audit_id, SimpleNamespace(score=90, needs_review=False, findings=[])))
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(cto_agent.audit_module, "record_response", recorded):
            result = run(cto_agent.get_cto_result("Add a Clear all button to AgentChat.", use_rag=False, audit=True, **kwargs))
        return result, client, recorded

    def test_a_plan_submitted_through_the_tool_is_stored_with_the_answer_and_its_number_is_in_the_reply(self):
        result, client, recorded = self.ask([plan_call(), text_reply(plain_answer())], audit_id=77)
        request = result.authorization_request
        self.assertEqual((request["submitted_via"], request["status"], request["executed"]), ("submit_plan_for_approval", "pending_human_approval", False))
        self.assertEqual(recorded.await_args.kwargs["authorization_request"], request)  # what is stored in AgentAuditLog.authorizationRequest
        self.assertIn("request #77", result.reply)
        self.assertIn("no code is written until you ask me for it", result.reply)
        tool_result = client.calls[1]["messages"][-1]["content"][0]
        self.assertEqual((tool_result["tool_use_id"], tool_result["is_error"]), ("tu_plan", False))
        self.assertIn("Plan staged", tool_result["content"])

    def test_the_plan_survives_a_final_answer_that_is_cut_off_at_the_token_limit(self):
        cut = '{"response": "Here is the plan for the button. I will add it beside Send and wire it to the reset hand'  # cut mid-JSON
        truncated = SimpleNamespace(content=[SimpleNamespace(type="text", text=cut)], stop_reason="max_tokens")
        result, _, recorded = self.ask([plan_call(), truncated], audit_id=88)
        self.assertIsNotNone(result.authorization_request, "the plan was lost with the cut-off answer")
        self.assertEqual(recorded.await_args.kwargs["authorization_request"]["files_to_change"], PATHS)
        self.assertIn("request #88", result.reply)
        self.assertIn("length limit", result.reply)
        self.assertIn("was not lost", result.reply)
        self.assertNotIn("was NOT recovered", result.reply)  # the warning that is only true without the tool is not shown

    def test_without_the_tool_a_cut_off_answer_still_loses_its_request_as_before(self):
        cut = '{"response": "Plan...", "authorization_request": {"action_summary": "x", "risk_level"'
        truncated = SimpleNamespace(content=[SimpleNamespace(type="text", text=cut)], stop_reason="max_tokens")
        result, _, _ = self.ask([truncated], audit_id=1)
        self.assertIsNone(result.authorization_request)
        self.assertIn("was NOT recovered", result.reply)

    def test_with_no_audit_row_the_reply_says_the_plan_was_not_recorded_and_invents_no_number(self):
        result, _, _ = self.ask([plan_call(), text_reply(plain_answer())], audit_id=None)
        self.assertIn("Plan not recorded", result.reply)
        self.assertNotIn("request #", result.reply)

    def test_a_request_in_the_answers_own_json_with_file_contents_wins_over_the_tools_plan(self):
        rich = answer({"frontend/src/components/mc/New.tsx": "export default function New() { return null; }\n"})
        result, _, _ = self.ask([plan_call(), text_reply(rich)], audit_id=5)
        self.assertIsNotNone(result.authorization_request["execution_plan"])
        self.assertNotIn("submitted_via", result.authorization_request)
        self.assertNotIn("Plan submitted for your approval", result.reply)
        self.assertTrue(any("also used" in f for f in result.format_findings))

    def test_a_refused_call_stages_nothing_and_the_model_is_told_why_so_it_can_fix_it(self):
        result, client, _ = self.ask([plan_call(files=["backend/app/governance/audit.py"]), text_reply(plain_answer())], audit_id=3)
        self.assertIsNone(result.authorization_request)
        tool_result = client.calls[1]["messages"][-1]["content"][0]
        self.assertTrue(tool_result["is_error"])
        self.assertIn("NOT SUBMITTED", tool_result["content"])

    def test_repeated_calls_keep_the_first_plan_the_loop_stays_bounded_and_no_file_is_read(self):
        calls = [plan_call(call_id=f"tu_{i}") for i in range(cto_agent.MAX_TOOL_ROUNDS + 1)]
        result, client, _ = self.ask([*calls, text_reply(plain_answer())], audit_id=9)
        self.assertEqual(client.calls[-1].get("tool_choice"), {"type": "none"})
        self.assertEqual(result.authorization_request["files_to_change"], PATHS)
        self.assertEqual(self.repo.calls, [])  # nothing was read from the repository


class ThePromptTellsTheCtoHowToUseTheToolTests(unittest.TestCase):
    def test_the_cto_prompt_carries_the_plan_approval_instructions(self):
        text = cto_agent.BASE_SYSTEM_PROMPT
        for needle in ("PLAN APPROVAL:", "concise plan", "no file contents", "call submit_plan_for_approval once", "under 500 words",
                       'leave "authorization_request" null', "never write authorization metadata in text", "request number"):
            self.assertIn(needle, text, needle)

    def test_only_the_ctos_prompt_has_it(self):
        from app.agents import cao_agent, cmo_agent, coo_agent

        for module in (cao_agent, coo_agent, cmo_agent):
            self.assertNotIn("submit_plan_for_approval", module.BASE_SYSTEM_PROMPT, module.__name__)

    def test_the_prompt_stays_inside_the_size_guard_and_never_asks_the_model_to_hide_its_reasoning(self):
        from app.agents.plan_checks import REFUSED_WORDING

        self.assertLess(len(cto_agent.BASE_SYSTEM_PROMPT) // 4, 16_500)
        for wording in REFUSED_WORDING:
            self.assertNotIn(wording, cto_agent.BASE_SYSTEM_PROMPT.lower(), wording)


CODE_TASKS = (
    "Add a Clear all button to AgentChat",
    "Read frontend/src/app/page.tsx and fix the typo in the heading",
    "Change the CTO_MAX_TOKENS setting in render.yaml to 4000",
    "Fix the failing pytest in backend/tests/test_rag.py",
    "Refactor the authorization card into smaller components",
    "Rename diseased_flag to is_sick in the audit table code",  # an identifier that merely CONTAINS a domain word
    "Update the feeder component styles",
    "Add an index to the AgentAuditLog table",
    "Why does /api/cto return a 502 after the deploy?",
)
DOMAIN_QUESTIONS = (
    "What does the literature say about biofloc systems?",
    "Our tilapia are showing disease signs and the water quality is poor",
    "Summarise the trial data on the SND probiotic",
    "Search the knowledge base for ammonia limits in recirculating systems",
    "Which vaccines are used against streptococcus in fish?",
    "What stocking density works for shrimp ponds?",
)


class RagIsConditionalTests(GuardedTestCase):
    """The CTO was handed irrelevant Biofloc papers for simple code tasks. Retrieval now runs only when the message asks for domain knowledge."""

    def ask(self, query, **kwargs):
        client = ScriptedClient([text_reply(plain_answer())])
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        result_object = SimpleNamespace(context="[1] biofloc.pdf | excerpt text", sources=[])
        fetch = mock.AsyncMock(return_value=result_object)
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("app.rag.retrieval.retrieve_context_with_sources", fetch):
            run(cto_agent.get_cto_result(query, use_rag=True, audit=False, **kwargs))
        return fetch, client

    def test_rag_is_not_called_for_simple_code_file_and_configuration_tasks(self):
        for query in CODE_TASKS:
            fetch, client = self.ask(query)
            fetch.assert_not_awaited()
            self.assertNotIn("KNOWLEDGE BASE EXCERPTS", client.calls[0]["system"], query)
            self.assertEqual(client.calls[0]["system"], cto_agent.BASE_SYSTEM_PROMPT, query)

    def test_rag_still_runs_when_the_message_asks_for_domain_knowledge_and_the_excerpts_reach_the_model(self):
        for query in DOMAIN_QUESTIONS:
            fetch, client = self.ask(query)
            fetch.assert_awaited_once()
            self.assertIn("KNOWLEDGE BASE EXCERPTS", client.calls[0]["system"], query)
            self.assertIn("biofloc.pdf", client.calls[0]["system"], query)

    def test_use_rag_false_still_means_never_even_for_a_domain_question(self):
        client = ScriptedClient([text_reply(plain_answer())])
        env = {k: v for k, v in os.environ.items() if k not in NO_DB}
        fetch = mock.AsyncMock()
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("app.rag.retrieval.retrieve_context_with_sources", fetch):
            run(cto_agent.get_cto_result("What does the literature say about biofloc?", use_rag=False, audit=False))
        fetch.assert_not_awaited()

    def test_the_classifier_is_whole_word_and_never_fails_on_odd_input(self):
        for query in CODE_TASKS:
            self.assertFalse(cto_agent.needs_domain_knowledge(query), query)
        for query in DOMAIN_QUESTIONS:
            self.assertTrue(cto_agent.needs_domain_knowledge(query), query)
        self.assertTrue(cto_agent.needs_domain_knowledge("BIOFLOC"))  # case does not matter
        self.assertTrue(cto_agent.needs_domain_knowledge("diseases and parasites"))  # plurals do
        for odd in ("", "   ", None, 5, ["biofloc"], {"biofloc": 1}):
            self.assertFalse(cto_agent.needs_domain_knowledge(odd), repr(odd))

    def test_a_cross_check_review_and_the_other_agents_are_unaffected(self):
        from app.agents import cao_agent

        self.assertFalse(hasattr(cao_agent, "needs_domain_knowledge"))  # the gate is the CTO's alone; the CAO keeps retrieving for its own questions
        fetch, client = self.ask("Add a button", reader=False)
        fetch.assert_not_awaited()
        self.assertEqual(client.calls[0]["system"], cto_agent.BASE_SYSTEM_PROMPT)
