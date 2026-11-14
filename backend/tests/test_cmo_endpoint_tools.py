"""
The CMO's image tool, tested THROUGH THE REAL ENDPOINT the console calls (POST /api/cmo), not just cmo_agent.py.

The console path is: browser -> Next.js /api/mc/agents/cmo -> this backend's POST /api/cmo -> get_cmo_result -> the Anthropic
call. These tests drive the FastAPI app with a fake Anthropic client that records the exact keyword arguments the model call
receives, so "is generate_image in the ``tools`` parameter sent to Claude?" is answered by the request itself.

NO TEST HERE REACHES THE NETWORK: OpenAI (``requests.post``) and Vercel Blob (``put_blob``) are the fakes from test_image_tool.

    python -m unittest tests.test_cmo_endpoint_tools   (from backend/)
"""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import cmo_agent, image_tool  # noqa: E402
from tests.test_image_tool import (  # noqa: E402
    BLOB_URL, FAKE_BLOB_TOKEN, FAKE_KEY, GuardedTestCase, ScriptedClient, _cmo_answer, _text_reply, _tool_reply,
)

CMO_KEY = "test-internal-cmo-key"
QUERY = "Show me a mockup of a new hero banner for the home page."


class CmoEndpointToolTests(GuardedTestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        super().setUp()
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        env["INTERNAL_CMO_KEY"] = CMO_KEY
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.headers = {"X-Internal-CMO-Key": CMO_KEY, "X-User-Id": "user_staff_1"}

    def post(self, replies, message=QUERY):
        model = ScriptedClient(replies)
        out = io.StringIO()
        with mock.patch.object(cmo_agent, "_client", model), contextlib.redirect_stdout(out):
            response = self.client.post("/api/cmo", headers=self.headers, json={"message": message})
        return response, model, out.getvalue()

    def test_the_endpoint_offers_generate_image_to_the_model_in_the_tools_parameter(self):
        response, model, _ = self.post([_text_reply(_cmo_answer([]))])
        self.assertEqual(response.status_code, 200, response.text)
        tools = model.calls[0]["tools"]  # the keyword argument the Anthropic call actually received
        self.assertEqual([t["name"] for t in tools], ["generate_image"])
        self.assertEqual(tools[0]["input_schema"]["required"], ["prompt"])
        self.assertIn("generate_image", model.calls[0]["system"])  # and the system prompt says it exists

    def test_the_system_prompt_no_longer_tells_the_model_it_has_no_image_tool(self):
        _, model, _ = self.post([_text_reply(_cmo_answer([]))])
        system = model.calls[0]["system"]
        self.assertIn("single exception to the", system)  # the one-tool exception to "You have NO tools"
        self.assertNotIn("mock_generate_image", system)

    def test_a_tool_call_through_the_endpoint_makes_a_real_upload_and_returns_the_blob_url(self):
        response, model, _ = self.post([_tool_reply("a calm blue hero banner"), _text_reply(_cmo_answer([BLOB_URL]))])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["authorization_request"]["mockup_urls"], [BLOB_URL])
        self.assertEqual(self.api.calls[0]["json"]["prompt"], "a calm blue hero banner")
        self.assertEqual(self.blob.calls[0]["access"], "public")
        self.assertEqual(len(model.calls), 2)  # the tool result went back to the model, which then wrote its answer

    def test_the_first_line_of_the_response_logs_the_tools_available_without_any_secret(self):
        _, _, printed = self.post([_text_reply(_cmo_answer([]))])
        first = printed.splitlines()[0]
        self.assertEqual(first, "CMO tools available: ['generate_image'] (openai_key set: True, blob_token set: True)")
        self.assertNotIn(FAKE_KEY, printed)
        self.assertNotIn(FAKE_BLOB_TOKEN, printed)

    def test_the_log_shows_when_a_key_is_missing_so_render_logs_explain_a_placeholder(self):
        env = {k: v for k, v in os.environ.items() if k != "BLOB_READ_WRITE_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            _, _, printed = self.post([_text_reply(_cmo_answer([]))])
        self.assertIn("['generate_image'] (openai_key set: True, blob_token set: False)", printed.splitlines()[0])
        self.assertEqual(image_tool.readiness(), {"openai_key": True, "blob_token": True})  # restored after the block

    def test_the_endpoint_still_refuses_a_wrong_key_before_any_model_call(self):
        model = ScriptedClient([_text_reply(_cmo_answer([]))])
        with mock.patch.object(cmo_agent, "_client", model):
            response = self.client.post("/api/cmo", headers={"X-Internal-CMO-Key": "wrong"}, json={"message": QUERY})
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(model.calls, [])

    def test_a_cross_check_review_logs_no_tools_and_sends_none(self):
        import asyncio

        model, out = ScriptedClient([_text_reply("verdict")]), io.StringIO()
        with mock.patch.object(cmo_agent, "_client", model), contextlib.redirect_stdout(out):
            asyncio.run(cmo_agent.get_cmo_answer("review this", use_rag=False, audit=False))
        self.assertNotIn("tools", model.calls[0])
        self.assertTrue(out.getvalue().startswith("CMO tools available: []"))


if __name__ == "__main__":
    unittest.main()
