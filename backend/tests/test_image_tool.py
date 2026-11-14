"""
Tests for the real image tool (app/agents/image_tool.py): OpenAI Images API with a placeholder fall-back.

NO TEST HERE REACHES THE NETWORK. ``requests.post`` inside the module is replaced by a fake that records the call and answers
like the API. Any test that reached the real network would fail (see GuardedTestCase). The OPENAI_API_KEY used is a fake, and
the tests prove it never leaks into a result or a log line.

    python -m unittest tests.test_image_tool   (from backend/)
"""

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import requests  # noqa: E402

from app.agents import image_tool, tools  # noqa: E402

FAKE_KEY = "sk-" + "Z" * 40
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 16).decode()
WEBP_B64 = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8).decode()


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def ok(b64=PNG_B64):
    return FakeResponse(200, {"data": [{"b64_json": b64}]})


class FakeOpenAI:
    """Replaces requests.post. ``outcome`` is a FakeResponse, or an exception to raise."""

    def __init__(self, outcome=None):
        self.outcome = outcome if outcome is not None else ok()
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None, **kw):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class GuardedTestCase(unittest.TestCase):
    """A fake key, a fake API, and a tripwire on any other route to the network."""

    def setUp(self):
        self.api = FakeOpenAI()
        env = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_IMAGE_MODEL": ""}
        for patcher in (
            mock.patch.dict(os.environ, env),
            mock.patch.object(image_tool.requests, "post", self.api),
            mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_placeholder(self, result):
        self.assertFalse(result.generated)
        self.assertTrue(result.url.startswith(tools.MOCKUP_BASE), result.url[:80])
        self.assertTrue(result.reason)


class GeneratesARealImageTests(GuardedTestCase):
    def test_with_a_key_it_calls_the_images_api_and_returns_a_png_data_uri(self):
        url = image_tool.generate_image("A calm blue hero banner for a shrimp farm")
        self.assertEqual(url, f"data:image/png;base64,{PNG_B64}")
        self.assertEqual(len(self.api.calls), 1)

    def test_the_request_is_the_documented_one_for_a_gpt_image_model(self):
        image_tool.generate_image("  A hero banner  ")
        call = self.api.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/images/generations")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertEqual(call["json"], {"model": "gpt-image-1-mini", "prompt": "A hero banner", "n": 1, "size": "1024x1024", "quality": "low"})
        self.assertNotIn("response_format", call["json"])  # GPT Image models reject it
        self.assertEqual(call["timeout"], image_tool.TIMEOUT_SECONDS)

    def test_the_model_comes_from_openai_image_model_when_set(self):
        with mock.patch.dict(os.environ, {"OPENAI_IMAGE_MODEL": "gpt-image-1"}):
            image_tool.generate_image("x")
        self.assertEqual(self.api.calls[0]["json"]["model"], "gpt-image-1")

    def test_the_detailed_result_says_it_is_real_and_which_model_made_it(self):
        result = image_tool.generate_image_detailed("x")
        self.assertTrue(result.generated)
        self.assertEqual((result.reason, result.model), ("", "gpt-image-1-mini"))

    def test_jpeg_and_webp_are_labelled_with_their_own_type(self):
        for b64, mime in ((JPEG_B64, "image/jpeg"), (WEBP_B64, "image/webp")):
            self.api.outcome = ok(b64)
            self.assertTrue(image_tool.generate_image("x").startswith(f"data:{mime};base64,"))

    def test_a_long_prompt_is_cut_to_the_limit(self):
        image_tool.generate_image("w" * 5000)
        self.assertEqual(len(self.api.calls[0]["json"]["prompt"]), image_tool.MAX_PROMPT_CHARS)

    def test_an_https_link_from_an_older_model_is_passed_on_but_a_plain_http_one_is_not(self):
        self.api.outcome = FakeResponse(200, {"data": [{"url": "https://cdn.example/pic.png"}]})
        self.assertEqual(image_tool.generate_image("x"), "https://cdn.example/pic.png")
        self.api.outcome = FakeResponse(200, {"data": [{"url": "http://cdn.example/pic.png"}]})
        self.assert_placeholder(image_tool.generate_image_detailed("x"))


class FallsBackToThePlaceholderTests(GuardedTestCase):
    def test_a_missing_key_logs_a_warning_and_uses_the_placeholder_without_any_request(self):
        for value in (None, "", "   "):
            env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
            if value is not None:
                env["OPENAI_API_KEY"] = value
            with mock.patch.dict(os.environ, env, clear=True), self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
                result = image_tool.generate_image_detailed("Hero banner")
            self.assert_placeholder(result)
            self.assertIn("OPENAI_API_KEY is not set", logs.output[0])
        self.assertEqual(self.api.calls, [])

    def test_the_placeholder_is_exactly_what_the_mock_tool_returns(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(image_tool.generate_image("CMO Mockup"), tools.mock_generate_image("CMO Mockup"))

    def test_an_empty_or_non_text_prompt_falls_back_without_a_request(self):
        for bad in ("", "   ", None, 5, ["x"]):
            self.assert_placeholder(image_tool.generate_image_detailed(bad))
        self.assertEqual(self.api.calls, [])

    def test_api_errors_fall_back_and_the_log_names_only_the_status(self):
        for status in (400, 401, 403, 429, 500, 503):
            self.api.outcome = FakeResponse(status, {"error": {"message": f"bad key {FAKE_KEY} for prompt: secret brief"}})
            with self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
                result = image_tool.generate_image_detailed("secret brief")
            self.assert_placeholder(result)
            self.assertIn(f"HTTP {status}", logs.output[0])
            self.assertNotIn(FAKE_KEY, logs.output[0] + result.url + result.reason)
            self.assertNotIn("secret brief", logs.output[0] + result.reason)

    def test_network_failures_and_timeouts_fall_back_and_never_leak_the_key(self):
        for error in (requests.Timeout("slow"), requests.ConnectionError(f"failed with {FAKE_KEY}"), requests.RequestException("x")):
            self.api.outcome = error
            with self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
                result = image_tool.generate_image_detailed("x")
            self.assert_placeholder(result)
            self.assertNotIn(FAKE_KEY, logs.output[0] + result.reason)

    def test_a_reply_that_is_not_a_usable_image_falls_back(self):
        not_png = base64.b64encode(b"<html>not an image</html>").decode()
        for outcome in (
            FakeResponse(200, None),  # not JSON
            FakeResponse(200, []),
            FakeResponse(200, {}),
            FakeResponse(200, {"data": []}),
            FakeResponse(200, {"data": ["x"]}),
            FakeResponse(200, {"data": [{}]}),
            FakeResponse(200, {"data": [{"b64_json": "###not base64###"}]}),
            FakeResponse(200, {"data": [{"b64_json": not_png}]}),
            FakeResponse(200, {"data": [{"b64_json": ""}]}),
        ):
            self.api.outcome = outcome
            self.assert_placeholder(image_tool.generate_image_detailed("x"))

    def test_it_never_raises_whatever_goes_wrong(self):
        self.api.outcome = RuntimeError("boom")
        self.assert_placeholder(image_tool.generate_image_detailed("x"))
        self.assertTrue(image_tool.generate_image("x").startswith(tools.MOCKUP_BASE))

    def test_a_failed_call_is_not_retried(self):
        self.api.outcome = FakeResponse(500, {})
        image_tool.generate_image("x")
        self.assertEqual(len(self.api.calls), 1)


class WiringBoundaryTests(unittest.TestCase):
    def test_the_audited_tool_router_is_unchanged_and_nothing_uses_the_image_tool_yet(self):
        self.assertEqual(sorted(tools.TOOLS), ["github_create_branch", "github_open_pr", "github_write_file", "mock_generate_image"])
        for path in (BACKEND_DIR / "app").rglob("*.py"):
            if path.name != "image_tool.py":
                self.assertNotIn("image_tool", path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    unittest.main()
