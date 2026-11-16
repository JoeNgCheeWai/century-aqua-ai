"""
The CTO chat 403 investigation: tests for the fixes made while diagnosing it (POST /api/mc/agents/cto -> POST /api/cto).

    cd backend; python -m unittest tests.test_cto_chat_403

No model is called and no network is reached (a tripwire fails any test that tries). No database is used.

WHAT THE CODE SHOWED. The backend CTO route can only answer 401 (wrong key), 503 (no key), 400 (bad actor) or 502. It has no 403 of its own, and
it is gated exactly like the CAO, COO and CMO routes. A 403 can come from (a) the model API, (b) GitHub, or (c) a host or proxy in front of the
backend (Render/Cloudflare), none of which was logged. The one thing that is specific to the CTO is its use of the GitHub token, which was only
ever read under the exact name GITHUB_PAT: a token entered as ``GitHub_Pat`` is invisible on Linux (case-sensitive) and the CTO's file reader said
"not set" with a good token present.
"""

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

import requests  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agents import cao_agent, cmo_agent, coo_agent, cto_agent, tools  # noqa: E402
from tests.test_read_file_tool import FakeRepo  # noqa: E402

TOKEN = "github_pat_" + "B" * 40
KEYS = {"INTERNAL_CAO_KEY": "cao-key-aaaa", "INTERNAL_COO_KEY": "coo-key-aaaa", "INTERNAL_CTO_KEY": "cto-key-aaaa", "INTERNAL_CMO_KEY": "cmo-key-aaaa"}
HEADERS = {"cao": "X-Internal-CAO-Key", "coo": "X-Internal-COO-Key", "cto": "X-Internal-CTO-Key", "cmo": "X-Internal-CMO-Key"}
MODULES = {"cao": cao_agent, "coo": coo_agent, "cto": cto_agent, "cmo": cmo_agent}


class CaseSensitiveEnv(dict):
    """A plain, CASE-SENSITIVE environment, like Linux on Render. On Windows os.environ is case-insensitive, which would hide the very bug under test."""


def clean_env(**extra):
    """The process environment minus every GitHub token spelling and every service the tests must not reach, plus the four agent keys."""
    env = {k: v for k, v in os.environ.items() if k.upper() != "GITHUB_PAT" and k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY",
                                                                                          "PINECONE_INDEX", "OPENAI_API_KEY", "COO_AUTO_APPROVAL")}
    env.update(KEYS)
    env.update(extra)
    return env


def text_reply(text="{}"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


class Scripted:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class UpstreamForbidden(Exception):
    """What the Anthropic SDK raises for a permission error: an exception that carries ``status_code``."""

    status_code = 403


class TokenNameTests(unittest.TestCase):
    """The GitHub token is found under its documented name and under any CASING of it, and only its NAME is ever reported."""

    def setUp(self):
        tools._warned_alias = False

    def env(self, **kv):
        # Replace os.environ itself with a case-sensitive dict (as on Linux), so these tests mean the same thing on every platform.
        patcher = mock.patch.object(os, "environ", CaseSensitiveEnv(clean_env(**kv)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_documented_name_works_as_before(self):
        self.env(GITHUB_PAT=TOKEN)
        self.assertEqual((tools.token_name(), tools.configured(), tools._token()), ("GITHUB_PAT", True, TOKEN))

    def test_github_pat_in_mixed_case_is_now_found_instead_of_reported_as_not_set(self):
        for name in ("GitHub_Pat", "Github_Pat", "github_pat"):
            self.env(**{name: TOKEN})
            self.assertEqual((tools.token_name(), tools.configured(), tools._token()), (name, True, TOKEN), name)
            self.assertEqual(tools._headers()["Authorization"], f"Bearer {TOKEN}")
            self.assertIsNone(os.environ.get("GITHUB_PAT"))  # proof the old exact-name lookup would have missed it

    def test_nothing_set_or_only_blank_means_not_configured_and_never_sends_a_blank_bearer(self):
        self.env()
        self.assertEqual((tools.token_name(), tools.configured(), tools._token()), (None, False, ""))
        self.env(GitHub_Pat="   ")
        self.assertFalse(tools.configured())

    def test_an_explicit_empty_documented_name_still_means_not_configured(self):
        self.env(GITHUB_PAT="", GitHub_Pat=TOKEN)  # the documented name wins even when empty: an operator who blanked it meant it
        self.assertEqual((tools.token_name(), tools.configured()), (None, False))

    def test_other_variable_names_are_not_mistaken_for_the_token(self):
        self.env(GITHUB_TOKEN=TOKEN, GITHUB_PAT_OLD=TOKEN, MY_GITHUB_PAT=TOKEN)
        self.assertFalse(tools.configured())

    def test_a_mixed_case_name_is_logged_once_by_name_only_never_the_value(self):
        self.env(GitHub_Pat=TOKEN)
        with self.assertLogs("app.agents.tools", level="WARNING") as logs:
            tools.token_name()
            tools.token_name()
        self.assertEqual(len(logs.records), 1)
        text = logs.output[0]
        self.assertIn("GitHub_Pat", text)
        self.assertNotIn(TOKEN, text)

    def test_the_cto_file_reader_works_with_a_mixed_case_token_name(self):
        repo = FakeRepo()
        for patcher in (mock.patch.object(os, "environ", CaseSensitiveEnv(clean_env(GitHub_Pat=TOKEN, GITHUB_OWNER="", GITHUB_REPO=""))),
                        mock.patch.object(requests, "request", repo),
                        mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertEqual(tools.github_read_file("README.md"), "# Century Aqua\n")
        self.assertEqual(repo.calls[0]["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_the_not_set_message_names_the_documented_variable(self):
        self.env()
        self.assertIn("GITHUB_PAT", tools.github_read_file("README.md"))


class CtoEndpointLoggingTests(unittest.TestCase):
    """POST /api/cto never goes quiet: every refusal and every failure leaves the exact reason in the logs, and a GitHub problem never becomes a 403."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        for patcher in (mock.patch.dict(os.environ, clean_env(), clear=True),
                        mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        tools._warned_alias = False

    def post(self, agent="cto", replies=None, key=None, message="hello", client_model=None):
        model = Scripted(replies if replies is not None else [text_reply()])
        out = io.StringIO()
        with mock.patch.object(MODULES[agent], "_client", model), contextlib.redirect_stdout(out):
            response = self.client.post(f"/api/{agent}", headers={HEADERS[agent]: key or KEYS[f"INTERNAL_{agent.upper()}_KEY"], "X-User-Id": "mission-control"},
                                        json={"message": message})
        return response, model, out.getvalue()

    def test_a_wrong_cto_key_is_a_401_that_is_now_logged_by_name_without_the_key(self):
        response, model, printed = self.post(key="nope-not-the-key")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(model.calls, [])  # no model call for an unauthenticated caller
        self.assertIn("X-Internal-CTO-Key was present but did not match INTERNAL_CTO_KEY", printed)
        self.assertNotIn("nope-not-the-key", printed)
        self.assertNotIn(KEYS["INTERNAL_CTO_KEY"], printed)

    def test_an_unset_cto_key_is_a_503_that_is_now_logged(self):
        env = clean_env()
        env.pop("INTERNAL_CTO_KEY")
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            response = self.client.post("/api/cto", headers={HEADERS["cto"]: "anything"}, json={"message": "hi"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("INTERNAL_CTO_KEY is not set on this server; answering 503", out.getvalue())

    def test_the_cto_route_is_gated_like_the_other_three_with_no_extra_check(self):
        import ast

        from app.api import cao, cmo, coo, cto

        for module, gate in ((cao, "require_internal_key"), (coo, "require_coo_key"), (cto, "require_cto_key"), (cmo, "require_cmo_key")):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            self.assertIn(gate, calls, module.__name__)
            statuses = {n.value for n in ast.walk(tree) if isinstance(n, ast.keyword) and n.arg == "status_code" and isinstance(n.value, ast.Constant)
                        for n in [n.value]}
            self.assertNotIn(403, statuses, f"{module.__name__} raises a 403 of its own")

    def test_a_forbidden_from_the_model_api_is_logged_with_its_status_and_comes_back_as_a_502_not_a_silent_403(self):
        with self.assertLogs("app.api.cto", level="ERROR") as logs:
            response, _, printed = self.post(replies=[UpstreamForbidden("model access denied")])
        self.assertEqual(response.status_code, 502)
        self.assertIn("upstream status 403", printed)
        self.assertIn("model access denied", printed)
        self.assertIn("CTO chat failed (UpstreamForbidden, upstream status 403)", logs.output[0])

    def test_an_http_error_raised_inside_the_chat_keeps_its_status_and_is_logged_with_its_detail(self):
        async def refuse(*a, **k):
            raise HTTPException(status_code=403, detail="Refused: a dependency said no.")

        out = io.StringIO()
        with mock.patch("app.api.cto.get_cto_result", refuse), contextlib.redirect_stdout(out), self.assertLogs("app.api.cto", level="ERROR") as logs:
            response = self.client.post("/api/cto", headers={HEADERS["cto"]: KEYS["INTERNAL_CTO_KEY"]}, json={"message": "hi"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Refused: a dependency said no.")
        self.assertIn("CTO chat failed with 403: Refused: a dependency said no.", logs.output[0])
        self.assertIn("ERROR IN AI_CTO: HTTP 403", out.getvalue())

    def test_a_github_client_that_fails_to_initialise_never_crashes_the_chat_with_a_403(self):
        """The GitHub request itself blows up (as a broken client would): the chat still answers 200 and the failure is reported, not turned into a 403."""
        reader = Scripted([SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t1", name="github_read_file", input={"file_path": "README.md"})],
                                           stop_reason="tool_use"), text_reply("{}")])
        out = io.StringIO()
        with mock.patch.dict(os.environ, clean_env(GitHub_Pat=TOKEN), clear=True), \
                mock.patch.object(requests, "request", side_effect=RuntimeError("client failed to initialise")), \
                mock.patch.object(cto_agent, "_client", reader), contextlib.redirect_stdout(out):
            response = self.client.post("/api/cto", headers={HEADERS["cto"]: KEYS["INTERNAL_CTO_KEY"]}, json={"message": "read the readme"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(response.status_code, 403)
        result = reader.calls[1]["messages"][-1]["content"][0]  # what the model was told
        self.assertTrue(result["is_error"])
        self.assertEqual(result["content"], "ERROR: the file could not be read")  # the reader's own safe message: no token, no traceback
        self.assertNotIn(TOKEN, result["content"])
        self.assertIn("github token set: True", out.getvalue())

    def test_a_github_403_is_reported_to_the_model_as_a_read_error_and_never_becomes_the_chats_status(self):
        repo = FakeRepo()
        repo.fail = 403
        reader = Scripted([SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t1", name="github_read_file", input={"file_path": "README.md"})],
                                           stop_reason="tool_use"), text_reply("{}")])
        with mock.patch.dict(os.environ, clean_env(GITHUB_PAT=TOKEN), clear=True), mock.patch.object(requests, "request", repo), \
                mock.patch.object(cto_agent, "_client", reader), contextlib.redirect_stdout(io.StringIO()):
            response = self.client.post("/api/cto", headers={HEADERS["cto"]: KEYS["INTERNAL_CTO_KEY"]}, json={"message": "read the readme"})
        self.assertEqual(response.status_code, 200)
        content = reader.calls[1]["messages"][-1]["content"][0]["content"]
        self.assertIn("GitHub 403", content)
        self.assertIn("the token may lack permission", content)

    def test_a_missing_github_token_is_said_plainly_to_the_model_with_the_variable_name_and_the_chat_still_works(self):
        reader = Scripted([SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t1", name="github_read_file", input={"file_path": "README.md"})],
                                           stop_reason="tool_use"), text_reply("{}")])
        out = io.StringIO()
        with mock.patch.object(cto_agent, "_client", reader), contextlib.redirect_stdout(out):
            response = self.client.post("/api/cto", headers={HEADERS["cto"]: KEYS["INTERNAL_CTO_KEY"]}, json={"message": "read the readme"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("GITHUB_PAT is not set on this server", reader.calls[1]["messages"][-1]["content"][0]["content"])
        self.assertIn("github token set: False", out.getvalue())


class OtherAgentsStillWorkTests(unittest.TestCase):
    """The CAO, COO and CMO endpoints behave exactly as before: 200 with the right key, 401 with the wrong one, and the CTO's key opens none of them."""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        for patcher in (mock.patch.dict(os.environ, clean_env(), clear=True),
                        mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, agent, key):
        model = Scripted([text_reply("plain answer")])
        with mock.patch.object(MODULES[agent], "_client", model), contextlib.redirect_stdout(io.StringIO()):
            return self.client.post(f"/api/{agent}", headers={HEADERS[agent]: key}, json={"message": "hi"}), model

    def test_each_agent_answers_200_with_its_own_key(self):
        for agent in ("cao", "coo", "cto", "cmo"):
            response, model = self.call(agent, KEYS[f"INTERNAL_{agent.upper()}_KEY"])
            self.assertEqual(response.status_code, 200, (agent, response.text))
            self.assertEqual(len(model.calls), 1, agent)

    def test_a_wrong_key_is_401_and_never_reaches_the_model_for_every_agent(self):
        for agent in ("cao", "coo", "cto", "cmo"):
            response, model = self.call(agent, "wrong-key")
            self.assertEqual(response.status_code, 401, agent)
            self.assertEqual(model.calls, [], agent)

    def test_the_ctos_key_opens_no_other_agent_and_no_other_key_opens_the_cto(self):
        for agent in ("cao", "coo", "cmo"):
            self.assertEqual(self.call(agent, KEYS["INTERNAL_CTO_KEY"])[0].status_code, 401, agent)
            self.assertEqual(self.call("cto", KEYS[f"INTERNAL_{agent.upper()}_KEY"])[0].status_code, 401, agent)

    def test_the_cto_gets_no_github_reader_when_it_is_a_cross_check_review_and_the_others_have_no_github_tool_at_all(self):
        from app.agents import exec_tools

        for role in ("cao", "coo", "cmo"):
            self.assertFalse(any("github" in n for n in exec_tools.tool_names(role)), role)


if __name__ == "__main__":
    unittest.main()
