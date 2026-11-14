"""
Tests for the CMO's image tool (app/agents/image_tool.py): OpenAI Images API -> Vercel Blob -> public https URL, with a
labelled-placeholder fall-back, and for how the CMO agent offers and bounds the tool.

NO TEST HERE REACHES THE NETWORK. ``requests.post`` (OpenAI) and the Vercel Blob SDK's ``put_blob`` are replaced by fakes that
record the call and answer like the real thing. Any other route to the network fails the test (see GuardedTestCase). The keys
used are fakes, and the tests prove neither ever leaks into a result or a log line.

    python -m unittest tests.test_image_tool   (from backend/)
"""

import asyncio
import base64
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
from vercel.blob import sync as vercel_blob_sync  # noqa: E402

from app.agents import cmo_agent, cmo_authorization, image_tool, tools  # noqa: E402

FAKE_KEY = "sk-" + "Z" * 40
FAKE_BLOB_TOKEN = "vercel_blob_rw_abc123XYZ_" + "q" * 30
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 16).decode()
WEBP_B64 = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8).decode()
BLOB_URL = "https://abc123xyz.public.blob.vercel-storage.com/cmo-mockups/hero-Ab12Cd.png"


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

    def __init__(self):
        self.outcome = ok()
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None, **kw):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeBlob:
    """Replaces vercel.blob.sync.put_blob. ``outcome`` is the URL to return, or an exception to raise."""

    def __init__(self):
        self.outcome = BLOB_URL
        self.calls = []

    def __call__(self, pathname, body, *, access, content_type=None, add_random_suffix=False, **kw):
        self.calls.append({"pathname": pathname, "body": body, "access": access, "content_type": content_type,
                           "add_random_suffix": add_random_suffix, "token": os.environ.get("BLOB_READ_WRITE_TOKEN")})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(url=self.outcome, download_url=f"{self.outcome}?download=1", pathname=pathname,
                               content_type=content_type, content_disposition="", etag="e")


class GuardedTestCase(unittest.TestCase):
    """Fake keys, a fake OpenAI, a fake Blob store, and a tripwire on any other route to the network."""

    def setUp(self):
        self.api, self.blob = FakeOpenAI(), FakeBlob()
        env = {"OPENAI_API_KEY": FAKE_KEY, "BLOB_READ_WRITE_TOKEN": FAKE_BLOB_TOKEN, "OPENAI_IMAGE_MODEL": ""}
        for patcher in (
            mock.patch.dict(os.environ, env),
            mock.patch.object(image_tool.requests, "post", self.api),
            mock.patch.object(vercel_blob_sync, "put_blob", self.blob),
            mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_placeholder(self, result):
        self.assertFalse(result.generated)
        self.assertTrue(result.url.startswith(tools.MOCKUP_BASE), result.url[:80])
        self.assertTrue(result.reason)


class UploadsToVercelBlobTests(GuardedTestCase):
    def test_with_both_keys_it_generates_uploads_and_returns_the_blob_url(self):
        url = image_tool.generate_image("A calm blue hero banner for a shrimp farm")
        self.assertEqual(url, BLOB_URL)
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(len(self.blob.calls), 1)

    def test_the_uploaded_bytes_are_exactly_the_generated_picture_and_the_store_is_public(self):
        image_tool.generate_image("x")
        call = self.blob.calls[0]
        self.assertEqual(call["body"], PNG_BYTES)
        self.assertEqual(call["access"], "public")
        self.assertEqual(call["content_type"], "image/png")
        self.assertTrue(call["add_random_suffix"])  # an address nobody can guess or overwrite
        self.assertRegex(call["pathname"], r"^cmo-mockups/[0-9a-f]{32}\.png$")
        self.assertEqual(call["token"], FAKE_BLOB_TOKEN)  # the SDK reads BLOB_READ_WRITE_TOKEN from the environment

    def test_the_result_is_a_real_https_blob_address_and_not_a_data_uri(self):
        result = image_tool.generate_image_detailed("x")
        self.assertTrue(result.generated)
        self.assertEqual((result.reason, result.model), ("", "gpt-image-1-mini"))
        self.assertTrue(result.url.startswith("https://"))
        self.assertFalse(result.url.startswith("data:"))
        self.assertTrue(image_tool.is_blob_url(result.url))

    def test_jpeg_and_webp_are_uploaded_with_their_own_type_and_extension(self):
        for b64, mime, ext in ((JPEG_B64, "image/jpeg", "jpg"), (WEBP_B64, "image/webp", "webp")):
            self.api.outcome = ok(b64)
            image_tool.generate_image("x")
            self.assertEqual(self.blob.calls[-1]["content_type"], mime)
            self.assertTrue(self.blob.calls[-1]["pathname"].endswith("." + ext))

    def test_the_openai_request_is_the_documented_one_for_a_gpt_image_model(self):
        image_tool.generate_image("  A hero banner  ")
        call = self.api.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/images/generations")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertEqual(call["json"], {"model": "gpt-image-1-mini", "prompt": "A hero banner", "n": 1, "size": "1024x1024", "quality": "low"})
        self.assertNotIn("response_format", call["json"])  # GPT Image models reject it
        self.assertEqual(call["timeout"], image_tool.TIMEOUT_SECONDS)

    def test_the_model_comes_from_openai_image_model_when_set_and_a_long_prompt_is_cut(self):
        with mock.patch.dict(os.environ, {"OPENAI_IMAGE_MODEL": "gpt-image-1"}):
            image_tool.generate_image("w" * 5000)
        self.assertEqual(self.api.calls[0]["json"]["model"], "gpt-image-1")
        self.assertEqual(len(self.api.calls[0]["json"]["prompt"]), image_tool.MAX_PROMPT_CHARS)

    def test_each_image_gets_its_own_address(self):
        image_tool.generate_image("a")
        image_tool.generate_image("b")
        self.assertNotEqual(self.blob.calls[0]["pathname"], self.blob.calls[1]["pathname"])


class BlobUrlCheckTests(unittest.TestCase):
    def test_only_a_plain_https_blob_address_is_accepted(self):
        good = ("https://abc.public.blob.vercel-storage.com/a.png", "https://x1.blob.vercel-storage.com/cmo-mockups/a-1.png?v=2")
        bad = ("http://abc.public.blob.vercel-storage.com/a.png", "https://blob.vercel-storage.com/a.png", "https://evil.example/a.png",
               "https://abc.blob.vercel-storage.com.evil.example/a.png", "https://user:pw@abc.blob.vercel-storage.com/a.png",
               "https://abc.blob.vercel-storage.com:8443/a.png", "https://abc.blob.vercel-storage.com/a b.png",
               "data:image/png;base64,AAAA", "", None, 5, "https://abc.blob.vercel-storage.com/" + "x" * 300)
        for url in good:
            self.assertTrue(image_tool.is_blob_url(url), url)
        for url in bad:
            self.assertFalse(image_tool.is_blob_url(url), url)


class FallsBackToThePlaceholderTests(GuardedTestCase):
    def test_a_missing_openai_key_logs_a_warning_and_uses_the_placeholder_without_any_request(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True), self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
            result = image_tool.generate_image_detailed("Hero banner")
        self.assert_placeholder(result)
        self.assertIn("OPENAI_API_KEY is not set", logs.output[0])
        self.assertEqual((self.api.calls, self.blob.calls), ([], []))

    def test_a_missing_blob_token_falls_back_BEFORE_paying_for_an_image(self):
        for value in (None, "", "   "):
            env = {k: v for k, v in os.environ.items() if k != "BLOB_READ_WRITE_TOKEN"}
            if value is not None:
                env["BLOB_READ_WRITE_TOKEN"] = value
            with mock.patch.dict(os.environ, env, clear=True), self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
                result = image_tool.generate_image_detailed("Hero banner")
            self.assert_placeholder(result)
            self.assertIn("BLOB_READ_WRITE_TOKEN is not set", logs.output[0])
        self.assertEqual((self.api.calls, self.blob.calls), ([], []))  # no OpenAI call, so nothing was charged

    def test_a_failed_upload_logs_a_warning_and_uses_the_placeholder(self):
        from vercel.blob.errors import BlobAccessError, BlobCredentialsError

        for error in (BlobAccessError("store is private"), BlobCredentialsError("bad token"), ConnectionError(f"reset {FAKE_BLOB_TOKEN}"), RuntimeError("x")):
            self.blob.outcome = error
            with self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
                result = image_tool.generate_image_detailed("Hero banner")
            self.assert_placeholder(result)
            self.assertIn("upload to Vercel Blob failed", logs.output[0])
            self.assertNotIn(FAKE_BLOB_TOKEN, logs.output[0] + result.reason + result.url)
            self.assertNotIn(FAKE_KEY, logs.output[0] + result.reason + result.url)

    def test_an_upload_that_returns_no_usable_address_falls_back(self):
        for bad in ("https://evil.example/x.png", "http://a.blob.vercel-storage.com/x.png", "", None, "data:image/png;base64,AAAA"):
            self.blob.outcome = bad
            self.assert_placeholder(image_tool.generate_image_detailed("x"))

    def test_the_placeholder_is_exactly_what_the_mock_tool_returns(self):
        self.blob.outcome = RuntimeError("down")
        self.assertEqual(image_tool.generate_image("CMO Mockup"), tools.mock_generate_image("CMO Mockup"))

    def test_an_empty_or_non_text_prompt_falls_back_without_any_request(self):
        for bad in ("", "   ", None, 5, ["x"]):
            self.assert_placeholder(image_tool.generate_image_detailed(bad))
        self.assertEqual((self.api.calls, self.blob.calls), ([], []))

    def test_openai_errors_fall_back_without_uploading_and_the_log_names_only_the_status(self):
        for status in (400, 401, 403, 429, 500, 503):
            self.api.outcome = FakeResponse(status, {"error": {"message": f"bad key {FAKE_KEY} for prompt: secret brief"}})
            with self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
                result = image_tool.generate_image_detailed("secret brief")
            self.assert_placeholder(result)
            self.assertIn(f"HTTP {status}", logs.output[0])
            self.assertNotIn(FAKE_KEY, logs.output[0] + result.url + result.reason)
            self.assertNotIn("secret brief", logs.output[0] + result.reason)
        self.assertEqual(self.blob.calls, [])

    def test_network_failures_and_timeouts_fall_back_and_never_leak_the_key(self):
        for error in (requests.Timeout("slow"), requests.ConnectionError(f"failed with {FAKE_KEY}"), requests.RequestException("x")):
            self.api.outcome = error
            with self.assertLogs("app.agents.image_tool", level="WARNING") as logs:
                result = image_tool.generate_image_detailed("x")
            self.assert_placeholder(result)
            self.assertNotIn(FAKE_KEY, logs.output[0] + result.reason)
        self.assertEqual(self.blob.calls, [])

    def test_a_reply_that_is_not_a_usable_image_falls_back_without_uploading(self):
        not_png = base64.b64encode(b"<html>not an image</html>").decode()
        for outcome in (
            FakeResponse(200, None), FakeResponse(200, []), FakeResponse(200, {}), FakeResponse(200, {"data": []}),
            FakeResponse(200, {"data": ["x"]}), FakeResponse(200, {"data": [{}]}),
            FakeResponse(200, {"data": [{"url": "https://cdn.example/pic.png"}]}),  # a link is not the bytes we need to store
            FakeResponse(200, {"data": [{"b64_json": "###not base64###"}]}), FakeResponse(200, {"data": [{"b64_json": not_png}]}),
            FakeResponse(200, {"data": [{"b64_json": ""}]}),
        ):
            self.api.outcome = outcome
            self.assert_placeholder(image_tool.generate_image_detailed("x"))
        self.assertEqual(self.blob.calls, [])

    def test_the_package_missing_falls_back_instead_of_crashing(self):
        with mock.patch.dict(sys.modules, {"vercel.blob": None, "vercel.blob.sync": None}):
            result = image_tool.generate_image_detailed("x")
        self.assert_placeholder(result)
        self.assertIn("vercel-blob package is not installed", result.reason)

    def test_it_never_raises_and_never_retries(self):
        self.api.outcome = RuntimeError("boom")
        self.assert_placeholder(image_tool.generate_image_detailed("x"))
        self.api.outcome = FakeResponse(500, {})
        before = len(self.api.calls)
        image_tool.generate_image("x")
        self.assertEqual(len(self.api.calls), before + 1)


class MockupValidatorTests(unittest.TestCase):
    """cmo_authorization.clean_mockup_urls: no longer placeholder-only, but never an address the tool did not return."""

    OTHER_BLOB = "https://someoneelse.public.blob.vercel-storage.com/x.png"
    PLACEHOLDER = "https://placehold.co/800x600/1a1a2e/00ffff?text=CMO+Hero"

    def test_a_blob_address_the_tool_returned_is_kept_alongside_a_placeholder(self):
        urls, findings = cmo_authorization.clean_mockup_urls([BLOB_URL, self.PLACEHOLDER], {BLOB_URL})
        self.assertEqual((urls, findings), ([BLOB_URL, self.PLACEHOLDER], []))

    def test_a_blob_address_the_tool_did_not_return_is_dropped_even_on_blobs_own_host(self):
        urls, findings = cmo_authorization.clean_mockup_urls([self.OTHER_BLOB, BLOB_URL], {BLOB_URL})
        self.assertEqual(urls, [BLOB_URL])
        self.assertTrue(any("1 mockup URL(s) dropped" in f for f in findings))
        self.assertEqual(cmo_authorization.clean_mockup_urls([BLOB_URL])[0], [])  # no tool ran: no Blob address survives
        self.assertEqual(cmo_authorization.clean_mockup_urls([BLOB_URL], set())[0], [])

    def test_other_hosts_plain_http_data_uris_and_junk_are_still_dropped(self):
        junk = ["https://evil.example/x.png", "http://placehold.co/x", "data:image/png;base64,AAAA", 5, None, "https://placehold.co/a b"]
        urls, findings = cmo_authorization.clean_mockup_urls(junk + [self.PLACEHOLDER], {BLOB_URL})
        self.assertEqual(urls, [self.PLACEHOLDER])
        self.assertTrue(any("6 mockup URL(s) dropped" in f for f in findings))

    def test_the_cap_and_deduplication_still_apply(self):
        many = {f"https://s.public.blob.vercel-storage.com/{i}.png" for i in range(9)}
        urls, _ = cmo_authorization.clean_mockup_urls(sorted(many) + [sorted(many)[0]], many)
        self.assertEqual(len(urls), cmo_authorization.MAX_MOCKUPS)
        self.assertEqual(len(set(urls)), len(urls))

    def test_a_full_request_keeps_the_generated_mockup_and_the_audit_summary_says_ai_generated_not_placeholder(self):
        raw = {"action_summary": "Refresh the hero", "risk_level": "low", "impact": "i", "proposed_content": "c", "mockup_urls": [BLOB_URL]}
        request, _ = cmo_authorization.normalize_authorization_request(raw, {BLOB_URL})
        self.assertEqual(request["mockup_urls"], [BLOB_URL])
        summary = cmo_authorization.audit_summary(request)
        self.assertIn("AI-generated mockups", summary)
        self.assertNotIn("placeholders, not real designs", summary)
        with_placeholder = dict(request, mockup_urls=[BLOB_URL, self.PLACEHOLDER])
        self.assertIn("Placeholder images (an image could not be generated): 1", cmo_authorization.audit_summary(with_placeholder))


def _text_reply(payload="{}"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)], stop_reason="end_turn")


def _tool_reply(*prompts, name="generate_image"):
    blocks = [SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input={"prompt": p}) for i, p in enumerate(prompts)]
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


class ScriptedClient:
    """An Anthropic client stand-in that plays back a list of replies and records every call."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.replies.pop(0)


def run(coro):
    return asyncio.run(coro)


class CmoToolLoopTests(GuardedTestCase):
    def go(self, replies):
        client = ScriptedClient(replies)
        response, produced = run(cmo_agent._create_with_image_tool(client, model="m", system="s", messages=[{"role": "user", "content": "q"}]))
        return client, response, produced

    def test_the_cmo_is_offered_exactly_one_tool_generate_image(self):
        client, _, _ = self.go([_text_reply()])
        self.assertEqual([t["name"] for t in client.calls[0]["tools"]], ["generate_image"])
        self.assertEqual(client.calls[0]["tools"][0]["input_schema"]["required"], ["prompt"])

    def test_no_tool_use_means_one_call_no_image_and_nothing_uploaded(self):
        client, response, produced = self.go([_text_reply("hello")])
        self.assertEqual((len(client.calls), produced, self.api.calls, self.blob.calls), (1, set(), [], []))
        self.assertEqual(response.content[0].text, "hello")

    def test_when_the_cmo_calls_the_tool_it_generates_uploads_and_gets_the_blob_url_back(self):
        client, response, produced = self.go([_tool_reply("a calm blue hero banner"), _text_reply("done")])
        self.assertEqual(produced, {BLOB_URL})
        self.assertEqual(self.api.calls[0]["json"]["prompt"], "a calm blue hero banner")
        self.assertEqual(len(self.blob.calls), 1)
        follow_up = client.calls[1]["messages"]
        self.assertEqual(follow_up[-2]["role"], "assistant")
        result = follow_up[-1]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"]), ("tool_result", "tu_0"))
        self.assertIn(BLOB_URL, result["content"])
        self.assertEqual(response.content[0].text, "done")

    def test_a_failed_image_hands_the_model_an_honest_placeholder_message_and_no_blob_address_is_allowed(self):
        self.blob.outcome = RuntimeError("down")
        client, _, produced = self.go([_tool_reply("hero"), _text_reply()])
        self.assertEqual(produced, set())
        content = client.calls[1]["messages"][-1]["content"][0]["content"]
        self.assertIn("No image could be generated", content)
        self.assertIn("NOT the requested image", content)
        self.assertIn("placehold.co", content)

    def test_at_most_two_images_are_made_per_answer_and_the_rest_get_an_error_result(self):
        client, _, produced = self.go([_tool_reply("a", "b", "c"), _text_reply()])
        self.assertEqual(len(self.api.calls), cmo_agent.MAX_IMAGES_PER_ANSWER)
        results = client.calls[1]["messages"][-1]["content"]
        self.assertEqual(len(results), 3)  # every tool_use is answered, or the API would reject the next call
        self.assertTrue(results[2].get("is_error"))
        self.assertIn("Image limit reached", results[2]["content"])

    def test_an_unknown_tool_name_is_refused_and_runs_nothing(self):
        client, _, produced = self.go([_tool_reply("x", name="run_shell"), _text_reply()])
        self.assertEqual((self.api.calls, self.blob.calls, produced), ([], [], set()))
        self.assertTrue(client.calls[1]["messages"][-1]["content"][0]["is_error"])

    def test_a_model_that_never_stops_asking_is_cut_off_with_tools_switched_off(self):
        replies = [_tool_reply(f"p{i}") for i in range(cmo_agent.MAX_TOOL_ROUNDS + 1)] + [_text_reply("final")]
        client, response, _ = self.go(replies)
        self.assertEqual(len(client.calls), cmo_agent.MAX_TOOL_ROUNDS + 2)
        self.assertEqual(client.calls[-1]["tool_choice"], {"type": "none"})
        self.assertEqual(response.content[0].text, "final")
        self.assertLessEqual(len(self.api.calls), cmo_agent.MAX_IMAGES_PER_ANSWER)


def _cmo_answer(urls):
    import json

    return json.dumps({
        "response": "Bottom line: a hero mockup is attached. Nothing has been published. Confidence: 70%.\n\nRecommendation for human review: judge the mockup.",
        "reasoning_chain": ["Step 1: the mockup shows the idea.", "Data and sources: the message.", "Assumptions: none.", "Limitations: I cannot see the image."],
        "scenarios": None,
        "pre_consultation_requests": {"coo_query": None, "cto_query": None},
        "authorization_request": {"action_summary": "Use the new hero mockup on the home page", "risk_level": "low", "impact": "Visual only",
                                  "proposed_content": "Hero banner brief", "mockup_urls": urls},
    })


class CmoEndToEndTests(GuardedTestCase):
    def ask(self, replies, **kwargs):
        client = ScriptedClient(replies)
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        with mock.patch.object(cmo_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True):
            return run(cmo_agent.get_cmo_result("Show me a hero mockup", use_rag=False, audit=False, **kwargs)), client

    def test_the_whole_path_prompt_to_tool_to_blob_to_the_stored_request(self):
        result, client = self.ask([_tool_reply("a calm blue hero banner"), _text_reply(_cmo_answer([BLOB_URL]))])
        self.assertEqual(result.authorization_request["mockup_urls"], [BLOB_URL])
        self.assertEqual(self.blob.calls[0]["access"], "public")
        self.assertIn("generate_image", client.calls[0]["system"])
        self.assertEqual(result.authorization_request["status"], "pending_human_approval")
        self.assertFalse(result.authorization_request["executed"])

    def test_an_address_the_model_made_up_on_blobs_host_is_dropped_because_the_tool_never_returned_it(self):
        forged = "https://attacker.public.blob.vercel-storage.com/evil.png"
        result, _ = self.ask([_text_reply(_cmo_answer([forged]))])  # the model never called the tool
        self.assertEqual(result.authorization_request["mockup_urls"], [])
        self.assertTrue(any("mockup URL(s) dropped" in f for f in result.format_findings))

    def test_only_the_address_from_this_answer_survives_when_the_model_also_forges_one(self):
        forged = "https://attacker.public.blob.vercel-storage.com/evil.png"
        result, _ = self.ask([_tool_reply("hero"), _text_reply(_cmo_answer([forged, BLOB_URL]))])
        self.assertEqual(result.authorization_request["mockup_urls"], [BLOB_URL])

    def test_when_the_image_fails_the_request_still_stands_with_a_labelled_placeholder(self):
        self.blob.outcome = RuntimeError("down")
        placeholder = tools.mock_generate_image("hero")
        result, _ = self.ask([_tool_reply("hero"), _text_reply(_cmo_answer([placeholder]))])
        self.assertEqual(result.authorization_request["mockup_urls"], [placeholder])

    def test_a_cross_check_review_gets_no_tools_at_all(self):
        client = ScriptedClient([_text_reply("verdict")])
        with mock.patch.object(cmo_agent, "_client", client):
            run(cmo_agent.get_cmo_answer("review this", use_rag=False, audit=False))
        self.assertNotIn("tools", client.calls[0])
        self.assertEqual(self.api.calls, [])


class BoundaryTests(unittest.TestCase):
    def test_the_audited_tool_router_is_unchanged_and_only_the_cmo_imports_the_image_tool(self):
        self.assertEqual(sorted(tools.TOOLS), ["github_create_branch", "github_open_pr", "github_write_file", "mock_generate_image"])
        importers = {p.name for p in (BACKEND_DIR / "app").rglob("*.py") if p.name != "image_tool.py" and "image_tool" in p.read_text(encoding="utf-8")}
        self.assertEqual(importers, {"cmo_agent.py", "cmo_authorization.py"})

    def test_the_tool_never_logs_or_formats_a_secret_into_a_message(self):
        source = (BACKEND_DIR / "app" / "agents" / "image_tool.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if "logger." in line or "_fallback(" in line:
                self.assertNotIn("key}", line.replace("OPENAI_API_KEY", ""), line)
                self.assertNotIn("token}", line.replace("BLOB_TOKEN_ENV", ""), line)


if __name__ == "__main__":
    unittest.main()
