"""
The CTO's read-only file tool (tools.github_read_file) and the rule that it must read a file before it changes it.

NO TEST HERE REACHES THE NETWORK. GitHub is replaced by FakeRepo, a small in-memory stand-in that answers the way the real
contents API does (raw text for a file, JSON for a folder, 404 for a missing path or ref), records every call, and can be told to
fail. The Anthropic client is a scripted stand-in. A test that reached the real network would fail (see GuardedTestCase).

    python -m unittest tests.test_read_file_tool   (from backend/)
"""

import asyncio
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

import requests  # noqa: E402

from app.agents import cto_agent, execution_plan as ep, tools  # noqa: E402
from app.prompts import persona  # noqa: E402

OWNER, REPO = "JoeNgCheeWai", "century-aqua-ai"
TOKEN = "github_pat_" + "A" * 40  # a fake token: the tests prove it never leaks into a result


class FakeResponse:
    def __init__(self, status, body=b"", content_type="application/vnd.github.v3.raw; charset=utf-8", json_body=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = {"Content-Type": content_type}
        self._json = json_body

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeRepo:
    """An in-memory repository on GitHub's contents API. ``files`` maps path -> bytes on ``main``; ``folders`` are directories."""

    def __init__(self):
        self.files = {
            "frontend/src/components/mc/AgentChat.tsx": b"export default function AgentChat() {\n  return null;\n}\n",
            "backend/app/agents/tools.py": b"# the tool router\n",
            "README.md": b"# Century Aqua\n",
        }
        self.folders = {"frontend/src/components/mc", "backend/app/agents"}
        self.branches = {"main", "agent/other"}
        self.calls = []
        self.fail = None  # an exception to raise, or a status code to answer with, for every call

    def __call__(self, method, url, headers=None, timeout=None, params=None, json=None, **kw):
        path = url.replace(f"{tools.API}/repos/{OWNER}/{REPO}/contents/", "")
        self.calls.append({"method": method, "path": path, "url": url, "headers": headers, "timeout": timeout, "params": params})
        if isinstance(self.fail, Exception):
            raise self.fail
        if isinstance(self.fail, int):
            return FakeResponse(self.fail, json_body={"message": "forced failure"}, content_type="application/json")
        ref = (params or {}).get("ref", "main")
        if ref not in self.branches:
            return FakeResponse(404, json_body={"message": f"No commit found for the ref {ref}"}, content_type="application/json")
        if path in self.folders:
            return FakeResponse(200, json_body=[{"name": "x"}], content_type="application/json; charset=utf-8")
        if path in self.files:
            return FakeResponse(200, self.files[path])
        return FakeResponse(404, json_body={"message": "Not Found"}, content_type="application/json")


class GuardedTestCase(unittest.TestCase):
    """A fake token, a fake repository, and a tripwire on any other route to the network."""

    def setUp(self):
        self.repo = FakeRepo()
        for patcher in (
            mock.patch.dict(os.environ, {"GITHUB_PAT": TOKEN, "GITHUB_OWNER": "", "GITHUB_REPO": ""}),
            mock.patch.object(requests, "request", self.repo),
            mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


class ReadFileToolTests(GuardedTestCase):
    def test_it_returns_the_files_plain_text_for_a_file_on_main(self):
        text = tools.github_read_file("frontend/src/components/mc/AgentChat.tsx")
        self.assertEqual(text, "export default function AgentChat() {\n  return null;\n}\n")

    def test_it_calls_the_contents_api_with_the_raw_accept_header_the_token_and_the_branch(self):
        tools.github_read_file("README.md", "main")
        call = self.repo.calls[0]
        self.assertEqual(call["method"], "GET")  # read-only: nothing but a GET
        self.assertEqual(call["path"], "README.md")
        self.assertEqual(call["params"], {"ref": "main"})
        self.assertEqual(call["headers"]["Accept"], "application/vnd.github.v3.raw")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(call["timeout"], tools.TIMEOUT_SECONDS)

    def test_the_branch_defaults_to_main_and_another_existing_branch_can_be_read(self):
        tools.github_read_file("README.md")
        self.assertEqual(self.repo.calls[0]["params"], {"ref": "main"})
        self.assertEqual(tools.github_read_file("README.md", "agent/other"), "# Century Aqua\n")

    def test_a_missing_file_returns_a_clear_error_string_and_does_not_raise(self):
        result = tools.github_read_file("nope/missing.txt")
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("File not found", result)
        self.assertIn("nope/missing.txt", result)

    def test_a_missing_branch_is_reported_as_not_found(self):
        self.assertIn("File not found", tools.github_read_file("README.md", "no-such-branch"))

    def test_a_folder_is_reported_not_dumped(self):
        result = tools.github_read_file("backend/app/agents")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("folder", result)

    def test_secret_files_and_unclean_paths_are_refused_before_any_request_is_made(self):
        for path in ("backend/.env", ".env", "frontend/.env.local", "key.pem", "x/id_rsa", "src/credentials.ts", ".git/config",
                     "../etc/passwd", "/etc/passwd", "a//b", "a\\b", "", "   ", None, 5):
            self.assertTrue(tools.github_read_file(path).startswith("ERROR:"), repr(path))
        self.assertEqual(self.repo.calls, [])  # not one request left this machine

    def test_a_branch_name_that_could_misbehave_is_refused_before_any_request(self):
        for branch in ("../main", "-rf", "/etc", "a b", "x" * 200, "", None, 5, "main?x=1"):
            self.assertTrue(tools.github_read_file("README.md", branch).startswith("ERROR:"), repr(branch))
        self.assertEqual(self.repo.calls, [])


class ReadFileToolSafetyTests(GuardedTestCase):
    def test_reading_the_guardrail_files_is_allowed_because_only_writing_them_is_dangerous(self):
        self.assertIn("tool router", tools.github_read_file("backend/app/agents/tools.py"))
        with self.assertRaises(ep.PlanError):
            ep.check_path("backend/app/agents/tools.py")  # the WRITE rule is unchanged: still protected

    def test_the_tool_takes_no_owner_or_repo_so_a_caller_cannot_point_it_elsewhere(self):
        import inspect

        self.assertEqual(list(inspect.signature(tools.github_read_file).parameters), ["file_path", "branch"])
        tools.github_read_file("README.md")
        self.assertIn(f"/repos/{OWNER}/{REPO}/contents/README.md", self.repo.calls[0]["url"])  # the default: this project's own repo
        with mock.patch.dict(os.environ, {"GITHUB_OWNER": "acme", "GITHUB_REPO": "site"}):
            tools.github_read_file("README.md")
        self.assertIn("/repos/acme/site/contents/README.md", self.repo.calls[1]["url"])  # only the SERVER's config chooses the repo

    def test_a_missing_token_returns_an_error_and_makes_no_request(self):
        with mock.patch.dict(os.environ, {"GITHUB_PAT": ""}):
            result = tools.github_read_file("README.md")
        self.assertIn("GITHUB_PAT", result)
        self.assertEqual(self.repo.calls, [])

    def test_github_errors_and_network_failures_become_short_error_strings_that_never_leak_the_token(self):
        for failure in (401, 403, 500, requests.Timeout("slow"), requests.ConnectionError(f"reset {TOKEN}"), RuntimeError(f"boom {TOKEN}")):
            self.repo.fail = failure
            result = tools.github_read_file("README.md")
            self.assertTrue(result.startswith("ERROR:"), repr(failure))
            self.assertNotIn(TOKEN, result)

    def test_a_binary_file_is_reported_not_returned(self):
        self.repo.files["logo.png"] = b"\x89PNG\r\n\x1a\n\x00\x00\x00binary"
        self.assertIn("binary", tools.github_read_file("logo.png"))

    def test_a_very_large_file_is_cut_off_and_marked_so_it_is_not_mistaken_for_the_whole_file(self):
        self.repo.files["big.txt"] = ("line\n" * 40_000).encode()  # 200,000 bytes
        text = tools.github_read_file("big.txt")
        self.assertIn(tools.CUT_OFF_MARK, text)
        self.assertLess(len(text), tools.MAX_READ_BYTES + 400)

    def test_a_key_hard_coded_in_an_ordinary_file_is_redacted_before_the_model_sees_it(self):
        secret = "sk-ant-" + "Z" * 30
        self.repo.files["app/settings.py"] = f"API_KEY = '{secret}'\nDEBUG = True\n".encode()
        text = tools.github_read_file("app/settings.py")
        self.assertNotIn(secret, text)
        self.assertIn(ep.REDACTED, text)
        self.assertIn("DEBUG = True", text)  # the rest of the file is intact

    def test_the_tool_is_registered_and_described_to_the_model_with_one_required_argument(self):
        self.assertIs(tools.TOOLS["github_read_file"], tools.github_read_file)
        self.assertEqual(tools.READ_TOOL_SPEC["name"], "github_read_file")
        self.assertEqual(tools.READ_TOOL_SPEC["input_schema"]["required"], ["file_path"])
        self.assertIn("needs no approval", tools.READ_TOOL_SPEC["description"])


class FileStatusTests(GuardedTestCase):
    def test_exists_missing_and_unknown(self):
        self.assertEqual(tools.github_file_status("README.md"), "exists")
        self.assertEqual(tools.github_file_status("nope.txt"), "missing")
        self.repo.fail = 500
        self.assertEqual(tools.github_file_status("README.md"), "unknown")  # a failed check is never "missing"
        self.repo.fail = requests.ConnectionError("down")
        self.assertEqual(tools.github_file_status("README.md"), "unknown")
        self.assertEqual(tools.github_file_status("../x"), "unknown")
        with mock.patch.dict(os.environ, {"GITHUB_PAT": ""}):
            self.assertEqual(tools.github_file_status("README.md"), "unknown")


# __CONTINUE__
