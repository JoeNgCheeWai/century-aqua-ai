"""
The CTO must READ an existing file before it proposes to change it, and this is enforced in CODE as well as asked for in the prompt.

What is proven here, with a scripted model and a fake GitHub (no network):
  * the CTO is offered exactly two tools, both read-only: github_read_file (a call to it really reads the file) and
    diagnose_boardroom_session (tested in tests/test_cto_autonomous_diagnosis.py);
  * the order of events when asked to modify a file: the read happens BEFORE the final answer, and the file text reaches the model;
  * a plan that overwrites an existing file that was NOT read is dropped (the rule is not just a request), a plan for a brand-new
    file is kept, a plan for a file it did read is kept, and an unverifiable file fails CLOSED;
  * it is enforced through the real endpoint (POST /api/cto), the loop is bounded, and a cross-check review still gets no tools.

    python -m unittest tests.test_cto_reads_before_writing   (from backend/)
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

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import cto_agent, tools  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase, TOKEN  # noqa: E402

CHAT_PATH = "frontend/src/components/mc/AgentChat.tsx"
CTO_KEY = "test-internal-cto-key"
QUERY = "Please add a small 'Clear all' button to AgentChat."


def text_reply(payload):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], stop_reason="end_turn")


def tool_reply(*paths, name="github_read_file", branch=None):
    blocks = []
    for i, path in enumerate(paths):
        args = {"file_path": path, **({"branch": branch} if branch else {})}
        blocks.append(SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=args))
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


def answer(files, summary="Add a Clear all button", with_diff=False):
    """A CTO answer in the real JSON shape, with an execution_plan that writes ``files`` {path: content}."""
    request = {
        "action_summary": summary, "risk_level": "low", "impact": "One small visible control.",
        "proposed_code_diff": "adds a button" if with_diff else None, "proposed_commands": [], "rollback_plan": "Close the pull request.",
        "execution_plan": {
            "branch_name": "agent/clear-all-button",
            "files_to_update": [{"path": p, "content": c, "commit_message": "Add clear all"} for p, c in files.items()],
            "pr_title": "Add a Clear all button", "pr_body": "Adds the button. I read the file first.",
        },
    }
    return {"response": "Great idea: a one-click reset is easy to use.\n\nRecommendation for human review: review the pull request. Confidence: 80%.",
            "reasoning_chain": ["First I read the component.", "Data and sources: the file.", "Assumptions: none.", "Limitations: not run."],
            "scenarios": None, "pre_consultation_requests": {"coo_query": None, "cto_query": None},
            "chat_reply": "Good idea. I read the component and drafted the change.", "authorization_request": request}


class ScriptedClient:
    """Plays back replies in order and records every call, including the messages (so a test can see what the model was shown)."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.replies.pop(0)


def run(coro):
    return asyncio.run(coro)


class CtoReadsBeforeItWritesTests(GuardedTestCase):
    def ask(self, replies, **kwargs):
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            result = run(cto_agent.get_cto_result(QUERY, use_rag=False, audit=False, **kwargs))
        return result, client

    def test_the_cto_is_offered_exactly_two_tools_both_read_only_the_file_reader_and_the_diagnostic(self):
        _, client = self.ask([text_reply(answer({}))])
        self.assertEqual([t["name"] for t in client.calls[0]["tools"]], ["github_read_file", "diagnose_boardroom_session"])

    def test_when_asked_to_modify_a_file_it_reads_the_file_BEFORE_it_produces_the_execution_plan(self):
        order = []
        real_read = tools.github_read_file

        def spy(path, branch="main"):
            order.append(("read", path, branch))
            return real_read(path, branch)

        client = ScriptedClient([tool_reply(CHAT_PATH), text_reply(answer({CHAT_PATH: "export default function AgentChat() { return <button>Clear all</button>; }\n"}))])
        original_create = client._create

        async def logging_create(**kwargs):
            reply = await original_create(**kwargs)
            order.append(("model_call", "plan" if reply.stop_reason == "end_turn" else "asks_to_read"))
            return reply

        client.messages = SimpleNamespace(create=logging_create)
        with mock.patch.object(cto_agent, "_client", client), mock.patch.object(tools, "github_read_file", spy), contextlib.redirect_stdout(io.StringIO()):
            result = run(cto_agent.get_cto_result(QUERY, use_rag=False, audit=False))
        self.assertEqual(order, [("model_call", "asks_to_read"), ("read", CHAT_PATH, "main"), ("model_call", "plan")])
        self.assertIsNotNone(result.authorization_request["execution_plan"])  # and the plan it wrote after reading was accepted
        self.assertEqual(result.authorization_request["execution_plan"]["files_to_update"][0]["path"], CHAT_PATH)

    def test_the_text_of_the_file_it_read_is_what_the_model_is_shown_before_it_writes(self):
        _, client = self.ask([tool_reply(CHAT_PATH), text_reply(answer({CHAT_PATH: "export default function AgentChat() { return null; }\n"}))])
        tool_result = client.calls[1]["messages"][-1]["content"][0]
        self.assertEqual((tool_result["type"], tool_result["tool_use_id"], tool_result["is_error"]), ("tool_result", "tu_0", False))
        self.assertIn("export default function AgentChat()", tool_result["content"])  # the real file text from the fake repo
        self.assertEqual(client.calls[1]["messages"][-2]["role"], "assistant")

    def test_a_brand_new_file_needs_no_read_and_its_plan_is_kept(self):
        new = "frontend/src/components/mc/ClearAllButton.tsx"
        result, client = self.ask([text_reply(answer({new: "export default function ClearAllButton() { return null; }\n"}))])
        self.assertEqual(result.authorization_request["execution_plan"]["files_to_update"][0]["path"], new)
        self.assertEqual(len(client.calls), 1)  # it never needed to read anything
        self.assertEqual(self.repo.calls[-1]["path"], new)  # the code only asked GitHub whether the file exists: it did not

    def test_path_spelling_in_capitals_does_not_trip_the_check_for_a_file_it_did_read(self):
        result, _ = self.ask([tool_reply("README.md"), text_reply(answer({"readme.md": "x\n"}, with_diff=True))])
        self.assertIsNotNone(result.authorization_request["execution_plan"])  # read as README.md, written as readme.md: the same file


class UnreadOverwritesAreRefusedInCodeTests(GuardedTestCase):
    ask = CtoReadsBeforeItWritesTests.ask  # the same helper: a scripted model, this fake repository, stdout captured

    def test_a_plan_that_overwrites_an_existing_file_it_did_not_read_is_dropped_in_code(self):
        result, _ = self.ask([text_reply(answer({CHAT_PATH: "export default function AgentChat() { return <i/>; }\n"}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])  # the plan is gone, so no pull request could come from it
        self.assertTrue(any("did not read" in f and CHAT_PATH in f for f in result.format_findings), result.format_findings)
        self.assertEqual(result.authorization_request["status"], "pending_human_approval")  # the rest of the request still stands

    def test_with_no_diff_or_commands_left_the_whole_request_is_dropped_not_left_empty(self):
        result, _ = self.ask([text_reply(answer({CHAT_PATH: "x\n"}))])
        self.assertIsNone(result.authorization_request)
        self.assertTrue(any("did not read" in f for f in result.format_findings))

    def test_reading_a_different_file_does_not_license_overwriting_this_one(self):
        result, _ = self.ask([tool_reply("README.md"), text_reply(answer({CHAT_PATH: "x\n"}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])

    def test_a_file_that_could_not_be_checked_fails_closed_never_open(self):
        self.repo.fail = 500
        result, _ = self.ask([text_reply(answer({"frontend/src/components/mc/Whatever.tsx": "x\n"}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])
        self.assertTrue(any("could not check" in f for f in result.format_findings), result.format_findings)

    def test_a_file_cut_off_for_being_too_long_does_not_count_as_read(self):
        self.repo.files[CHAT_PATH] = ("line\n" * 40_000).encode()
        result, _ = self.ask([tool_reply(CHAT_PATH), text_reply(answer({CHAT_PATH: "x\n"}, with_diff=True))])
        self.assertIsNone(result.authorization_request["execution_plan"])  # it saw only the start, so it cannot rewrite the whole file

    def test_a_failed_read_does_not_count_as_read_and_the_model_is_told_it_failed(self):
        result, client = self.ask([tool_reply("nope/missing.tsx"), text_reply(answer({CHAT_PATH: "x\n"}, with_diff=True))])
        tool_result = client.calls[1]["messages"][-1]["content"][0]
        self.assertTrue(tool_result["is_error"])
        self.assertIn("File not found", tool_result["content"])
        self.assertIsNone(result.authorization_request["execution_plan"])


class TheLoopIsBoundedAndTheEndpointEnforcesItTests(GuardedTestCase):
    def test_a_model_that_never_stops_asking_is_cut_off_and_reads_at_most_the_cap(self):
        replies = [tool_reply(f"f{i}.md") for i in range(cto_agent.MAX_TOOL_ROUNDS + 1)] + [text_reply(answer({}))]
        client = ScriptedClient(replies)
        with mock.patch.object(cto_agent, "_client", client), contextlib.redirect_stdout(io.StringIO()):
            run(cto_agent.get_cto_result(QUERY, use_rag=False, audit=False))
        self.assertEqual(len(client.calls), cto_agent.MAX_TOOL_ROUNDS + 2)
        self.assertEqual(client.calls[-1]["tool_choice"], {"type": "none"})  # the last call forbids another tool request
        self.assertLessEqual(len(self.repo.calls), cto_agent.MAX_FILE_READS)

    def test_reads_past_the_per_answer_cap_get_an_error_and_make_no_request(self):
        paths = [f"f{i}.md" for i in range(cto_agent.MAX_FILE_READS + 2)]
        client = ScriptedClient([tool_reply(*paths), text_reply(answer({}))])
        with mock.patch.object(cto_agent, "_client", client), contextlib.redirect_stdout(io.StringIO()):
            run(cto_agent.get_cto_result(QUERY, use_rag=False, audit=False))
        results = client.calls[1]["messages"][-1]["content"]
        self.assertEqual(len(results), len(paths))  # every tool_use is answered, or the API would reject the next call
        self.assertIn("limit reached", results[-1]["content"])
        self.assertEqual(len(self.repo.calls), cto_agent.MAX_FILE_READS)

    def test_an_unknown_tool_is_refused_and_a_branch_the_model_chooses_is_ignored_in_favour_of_main(self):
        client = ScriptedClient([tool_reply("README.md", name="run_shell"), tool_reply("README.md", branch="agent/other"), text_reply(answer({}))])
        with mock.patch.object(cto_agent, "_client", client), contextlib.redirect_stdout(io.StringIO()):
            run(cto_agent.get_cto_result(QUERY, use_rag=False, audit=False))
        self.assertTrue(client.calls[1]["messages"][-1]["content"][0]["is_error"])
        self.assertEqual(self.repo.calls[0]["params"], {"ref": "main"})  # only the second, real read reached GitHub, and from main

    def test_the_real_endpoint_drops_an_unread_overwrite_and_logs_the_tools_offered(self):
        from app.main import app

        client = ScriptedClient([text_reply(answer({CHAT_PATH: "x\n"}, with_diff=True))])
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        env["INTERNAL_CTO_KEY"] = CTO_KEY
        out = io.StringIO()
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            response = TestClient(app).post("/api/cto", headers={"X-Internal-CTO-Key": CTO_KEY}, json={"message": QUERY})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["authorization_request"]["execution_plan"])
        self.assertEqual([t["name"] for t in client.calls[0]["tools"]], ["github_read_file", "diagnose_boardroom_session"])
        self.assertTrue(out.getvalue().startswith("CTO tools available: ['github_read_file', 'diagnose_boardroom_session']"))
        self.assertNotIn(TOKEN, out.getvalue())

    def test_a_cross_check_review_gets_no_tools_at_all(self):
        client, out = ScriptedClient([text_reply("verdict")]), io.StringIO()
        with mock.patch.object(cto_agent, "_client", client), contextlib.redirect_stdout(out):
            run(cto_agent.get_cto_answer("review this", use_rag=False, audit=False))
        self.assertNotIn("tools", client.calls[0])
        self.assertTrue(out.getvalue().startswith("CTO tools available: []"))


if __name__ == "__main__":
    unittest.main()
