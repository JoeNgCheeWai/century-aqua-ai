"""
The COO's knowledge queue (Phase 1): the FetchRequest table, tavily_search (web_search), ingest_to_rag (web_ingest) and manage_fetch_queue.

    cd backend; python -m unittest tests.test_knowledge_queue

No test reaches the network, OpenAI or Pinecone: the search backends are faked (and a tripwire fails any real request), embeddings and the vector store
are fakes that record what they were asked. The SQL tests run against REAL Postgres, the LOCAL `prisma dev` server only (skipped otherwise; never Neon),
and every row they create is tagged and removed afterwards.
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import requests  # noqa: E402

from app.agents import exec_tools, knowledge_queue  # noqa: E402
from app.governance import db, fetch_queue  # noqa: E402
from app.rag import embeddings, vector_db, web_ingest, web_search  # noqa: E402
from tests.test_mission_control_api import DB_READY, LOCAL_URL, _sql  # noqa: E402

NEEDS_DB = "needs the LOCAL prisma dev database with the add_fetch_queue migration (cd frontend; npx prisma dev start caip-local). Never runs against Neon."
TAG = "kqtest"  # every FetchRequest.query and IngestedSource.url this file creates starts with it, so cleanup is exact
PAGE = "Aquaculture water quality: nitrite and ammonia must be monitored daily in recirculating systems. " * 6


def run(coro):
    return asyncio.run(coro)


def block(name="manage_fetch_queue", **args):
    return SimpleNamespace(type="tool_use", id="tu_1", name=name, input=args)


def hit(n, title=None, text=PAGE):
    return web_search.Hit(title or f"Paper {n}", f"https://example.org/{TAG}/paper-{n}", text)


class FakeStore:
    """Stands in for OpenAI embeddings and Pinecone: records every call, so a test can say how much was embedded and stored."""

    def __init__(self):
        self.embedded, self.upserted, self.fail_embed = [], [], False

    async def embed(self, texts):
        if self.fail_embed:
            raise embeddings.EmbeddingError("OpenAI embeddings request failed: RateLimitError")
        self.embedded.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def upsert(self, chunks):
        self.upserted.extend(chunks)
        return len(chunks)


def patched_world(store, search):
    """Fake keys, fake embeddings/vector store, a given search function, and a tripwire on the real network."""
    return [
        mock.patch.dict(os.environ, {"KNOWLEDGE_INGEST": "on", "OPENAI_API_KEY": "k", "PINECONE_API_KEY": "k", "PINECONE_INDEX": "i", "GOVERNANCE_DATABASE_URL": LOCAL_URL or ""}),
        mock.patch.object(embeddings, "generate_embeddings_batch", store.embed),
        mock.patch.object(vector_db, "upsert_chunks", store.upsert),
        mock.patch.object(web_search, "web_search", search),
        mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network")),
    ]


class WithDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB_READY:
            raise unittest.SkipTest(NEEDS_DB)
        try:
            _sql('SELECT 1 FROM "FetchRequest" LIMIT 1')
        except Exception:
            raise unittest.SkipTest(NEEDS_DB)

    def setUp(self):
        self.store = FakeStore()
        self.search_hits = [hit(1), hit(2)]
        self.search_error = ""

        def search(query):
            return web_search.SearchResult(not self.search_error, "fake", [] if self.search_error else list(self.search_hits), self.search_error)

        for patcher in patched_world(self.store, search):
            patcher.start()
            self.addCleanup(patcher.stop)
        self._clean()
        self.addCleanup(self._clean)

    @staticmethod
    def _clean():
        _sql('DELETE FROM "IngestedSource" WHERE "url" LIKE %s', (f"%{TAG}%",))
        _sql('DELETE FROM "FetchRequest" WHERE "query" LIKE %s', (f"{TAG}%",))
        _sql('DELETE FROM "AdminAuditLog" WHERE "action" = %s AND "details"->>\'note\' LIKE %s', ("executive_queue", f"%{TAG}%"))

    def add(self, requester="CAO", topic="nitrite", priority="MED"):
        status, request_id = fetch_queue.create_request(requester, f"{TAG} {topic}", priority)
        self.assertEqual(status, "ok")
        return request_id

    def coo(self, **args):
        outcome = self.outcome if hasattr(self, "outcome") else exec_tools.ExecOutcome("coo")
        self.outcome = outcome
        return run(exec_tools.handle_block("coo", block(**args), outcome))

    def status_of(self, request_id):
        return fetch_queue.get_request(request_id)


# ================================================================================================ NO DATABASE NEEDED
class UrlKeyTests(unittest.TestCase):
    def test_the_same_page_spelled_differently_has_one_key(self):
        spellings = ["https://www.Example.com/a/b/?utm_source=x&b=2&a=1#frag", "http://example.com/a/b?a=1&b=2", "https://example.com:443/a//b?b=2&a=1&gclid=zz"]
        self.assertEqual({web_ingest.url_key(u) for u in spellings}, {"example.com/a/b?a=1&b=2"})

    def test_different_pages_have_different_keys(self):
        self.assertNotEqual(web_ingest.url_key("https://example.com/a"), web_ingest.url_key("https://example.com/b"))
        self.assertNotEqual(web_ingest.url_key("https://example.com/a?id=1"), web_ingest.url_key("https://example.com/a?id=2"))  # a real parameter counts

    def test_unusable_addresses_are_refused(self):
        for bad in ("ftp://x.com/a", "javascript:alert(1)", "file:///etc/passwd", "https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://[::1]/x",
                    "https://user:pw@x.com/", "https://intranet/x", "", "   ", None, 5, "https://x.com/" + "a" * 600):
            with self.assertRaises(ValueError, msg=repr(bad)[:40]):
                web_ingest.url_key(bad)


class CleanTextTests(unittest.TestCase):
    def test_control_characters_and_blank_lines_go_and_secrets_are_masked(self):
        secret = "sk-ant-" + "Z" * 30
        text = web_ingest.clean_text(f"line one\x00\x1b  has   gaps\n\n\n  key {secret}  \n")
        self.assertEqual(text.splitlines()[0], "line one has gaps")
        self.assertNotIn(secret, text)
        self.assertNotIn("\x00", text)

    def test_text_is_cut_to_the_limit_and_a_non_string_is_empty(self):
        self.assertEqual(len(web_ingest.clean_text("word " * 20_000)), web_ingest.MAX_TEXT_CHARS - 1)  # cut, then stripped of the trailing space
        for bad in (None, 5, [], {}):
            self.assertEqual(web_ingest.clean_text(bad), "")


class SearchWrapperTests(unittest.TestCase):
    """web_search never reaches the network here: requests.post / requests.get are faked, and a tripwire catches anything else."""

    def setUp(self):
        self.calls = []
        for patcher in (mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network")),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, status=200, body=None):
        def respond(*args, **kwargs):
            self.calls.append((args, kwargs))
            return SimpleNamespace(status_code=status, json=lambda: body or {})
        return respond

    def env(self, **kv):
        env = {k: v for k, v in os.environ.items() if k not in ("TAVILY_API_KEY", "SEARCH_BACKEND")}
        env.update(kv)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_with_a_tavily_key_it_calls_tavily_with_a_bearer_token_and_returns_the_top_five_clean_results(self):
        self.env(TAVILY_API_KEY="tvly-SECRET")
        body = {"results": [{"title": f"<b>T{i}</b>", "url": f"https://example.org/p{i}", "content": f"Some <i>clean</i> &amp; text number {i}", "score": 0.9} for i in range(8)]}
        with mock.patch.object(requests, "post", self.fake(200, body)):
            result = web_search.web_search("nitrite in recirculating aquaculture")
        self.assertEqual((result.ok, result.backend, len(result.hits)), (True, "tavily", 5))  # five of eight
        self.assertEqual((result.hits[0].title, result.hits[0].snippet), ("T0", "Some clean & text number 0"))
        args, kwargs = self.calls[0]
        self.assertEqual(args[0], web_search.TAVILY_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tvly-SECRET")
        self.assertEqual((kwargs["json"]["max_results"], kwargs["json"]["include_answer"]), (5, False))
        self.assertNotIn("tvly-SECRET", web_search.render_hits(result))  # the key is never in anything returned

    def test_without_a_key_it_uses_the_labelled_keyless_fallback(self):
        self.env()
        self.assertEqual(web_search.backend_name(), "wikipedia-fallback")
        body = {"query": {"pages": {"1": {"index": 2, "title": "B", "fullurl": "https://en.wikipedia.org/wiki/B", "extract": "Text about B."},
                                    "2": {"index": 1, "title": "A", "fullurl": "https://en.wikipedia.org/wiki/A", "extract": "Text about A."}}}}
        with mock.patch.object(requests, "get", self.fake(200, body)):
            result = web_search.web_search("biofloc technology")
        self.assertEqual((result.ok, result.backend, [h.title for h in result.hits]), (True, "wikipedia-fallback", ["A", "B"]))  # ordered by search rank
        self.assertIn("wikipedia-fallback", web_search.render_hits(result))

    def test_a_result_that_is_not_https_or_has_no_text_or_a_private_host_is_dropped(self):
        self.env(TAVILY_API_KEY="k")
        body = {"results": [{"title": "ok", "url": "https://example.org/ok", "content": "fine text"}, {"title": "plain", "url": "http://example.org/x", "content": "t"},
                            {"title": "local", "url": "https://localhost/x", "content": "t"}, {"title": "empty", "url": "https://example.org/e", "content": "  "},
                            {"title": "ip", "url": "https://10.0.0.1/x", "content": "t"}, "junk", {"title": "nourl", "content": "t"}]}
        with mock.patch.object(requests, "post", self.fake(200, body)):
            self.assertEqual([h.title for h in web_search.web_search("a query here").hits], ["ok"])

    def test_failures_are_a_short_safe_error_never_an_exception(self):
        self.env(TAVILY_API_KEY="tvly-SECRET")
        for status, needle in ((401, "check TAVILY_API_KEY"), (500, "500")):
            with mock.patch.object(requests, "post", self.fake(status)):
                result = web_search.web_search("a query here")
            self.assertFalse(result.ok)
            self.assertIn(needle, result.error)
            self.assertNotIn("tvly-SECRET", result.error)
        with mock.patch.object(requests, "post", side_effect=requests.Timeout()):
            self.assertIn("in time", web_search.web_search("a query here").error)
        with mock.patch.object(requests, "post", side_effect=requests.ConnectionError("boom tvly-SECRET")):
            self.assertNotIn("tvly-SECRET", web_search.web_search("a query here").error)
        with mock.patch.object(requests, "post", side_effect=ValueError("bad json")):
            self.assertFalse(web_search.web_search("a query here").ok)

    def test_a_blank_or_tiny_query_and_the_off_switch_make_no_request_at_all(self):
        self.env(TAVILY_API_KEY="k")
        with mock.patch.object(requests, "post", self.fake(200, {})) as post:
            for bad in ("", "  ", "ab", None):
                self.assertFalse(web_search.web_search(bad).ok)
        self.assertEqual(self.calls, [])
        self.env(TAVILY_API_KEY="k", SEARCH_BACKEND="off")
        self.assertEqual(web_search.web_search("a real query").error, "web search is switched off (SEARCH_BACKEND=off)")
        self.assertEqual(self.calls, [])


# ================================================================================================ 1. A FETCH REQUEST CAN BE CREATED AND SAVED (real Postgres)
class FetchRequestStorageTests(WithDb):
    def test_a_request_is_saved_with_the_documented_fields_and_defaults(self):
        request_id = self.add("CTO", "tls certificates")
        row = _sql('SELECT * FROM "FetchRequest" WHERE "id" = %s', (request_id,))[0]
        self.assertEqual((row["requester"], row["priority"], row["status"], list(row["fetchedUrls"]), row["summaryText"], row["processedAt"]),
                         ("CTO", "MED", "PENDING", [], None, None))
        self.assertTrue(row["query"].endswith("tls certificates"))
        self.assertLess(abs((datetime.utcnow() - row["createdAt"]).total_seconds()), 120)

    def test_each_requester_and_priority_is_accepted_and_stored_as_given(self):
        for who in ("CAO", "CTO", "CMO"):
            for level in ("HIGH", "MED", "LOW"):
                request_id = self.add(who, f"{who} {level}", level)
                row = self.status_of(request_id)
                self.assertEqual((row["requester"], row["priority"]), (who, level))

    def test_the_input_is_checked_before_anything_is_saved(self):
        before = _sql('SELECT COUNT(*) AS n FROM "FetchRequest"')[0]["n"]
        for who, query, level, expect in (("COO", "enough text", "MED", "bad_requester"), ("", "enough text", "MED", "bad_requester"), ("CAO", "hi", "MED", "bad_query"),
                                          ("CAO", "   ", "MED", "bad_query"), ("CAO", None, "MED", "bad_query"), ("CAO", "enough text", "URGENT", "bad_priority")):
            self.assertEqual(fetch_queue.create_request(who, query, level), (expect, None), (who, query, level))
        self.assertEqual(_sql('SELECT COUNT(*) AS n FROM "FetchRequest"')[0]["n"], before)

    def test_a_long_query_is_cut_and_control_characters_are_removed(self):
        request_id = self.add("CAO", "x" * 1000)
        self.assertLessEqual(len(self.status_of(request_id)["query"]), fetch_queue.MAX_QUERY_CHARS)
        status, request_id = fetch_queue.create_request("cao", f"{TAG} a\x00b\x1b c", "high")  # case-insensitive, cleaned
        self.assertEqual((status, self.status_of(request_id)["query"], self.status_of(request_id)["priority"]), ("ok", f"{TAG} a b c", "HIGH"))

    def test_a_sql_injection_in_the_query_is_just_text(self):
        request_id = self.add("CAO", "x'); DROP TABLE \"FetchRequest\"; --")
        self.assertIn("DROP TABLE", self.status_of(request_id)["query"])
        self.assertEqual(_sql('SELECT COUNT(*) AS n FROM "FetchRequest" WHERE "id" = %s', (request_id,))[0]["n"], 1)

    def test_the_queue_refuses_a_flood(self):
        with mock.patch.object(fetch_queue, "MAX_PENDING", 3):
            pending = _sql('SELECT COUNT(*) AS n FROM "FetchRequest" WHERE "status" = CAST(%s AS "FetchStatus")', ("PENDING",))[0]["n"]
        with mock.patch.object(fetch_queue, "MAX_PENDING", pending + 2):
            self.add("CAO", "one")
            self.add("CAO", "two")
            self.assertEqual(fetch_queue.create_request("CAO", f"{TAG} three", "MED"), ("queue_full", None))

    def test_the_coo_can_add_a_request_for_another_executive_and_it_is_saved(self):
        result = self.coo(action="add", requester="CMO", query=f"{TAG} brand guidelines for aquaculture", priority="HIGH")
        self.assertFalse(result["is_error"], result["content"])
        request_id = int(result["content"].split("#")[1].split()[0])
        row = self.status_of(request_id)
        self.assertEqual((row["requester"], row["priority"], row["status"]), ("CMO", "HIGH", "PENDING"))
        for args in ({"action": "add", "requester": "COO", "query": f"{TAG} xxxxxx"}, {"action": "add", "requester": "CAO", "query": "no"}, {"action": "add", "requester": "CAO", "query": f"{TAG} yyyyyy", "priority": "NOW"}):
            self.assertTrue(self.coo(**args)["is_error"], args)


# ================================================================================================ 2. THE COO LISTS THE QUEUE AND PROCESSES A DUMMY FETCH
class CooQueueTests(WithDb):
    def test_the_queue_is_listed_high_then_med_then_low_and_oldest_first(self):
        low = self.add("CAO", "low old", "LOW")
        med_old = self.add("CTO", "med old", "MED")
        high = self.add("CMO", "high", "HIGH")
        med_new = self.add("CAO", "med new", "MED")
        _sql('UPDATE "FetchRequest" SET "createdAt" = %s WHERE "id" = %s', (datetime.utcnow() - timedelta(hours=5), med_old))
        rows = [r["id"] for r in fetch_queue.list_requests("PENDING") if r["query"].startswith(TAG)]
        self.assertEqual(rows, [high, med_old, med_new, low])
        text = self.coo(action="list")["content"]
        self.assertLess(text.index(f"[{high}] HIGH"), text.index(f"[{med_old}] MED"))
        self.assertLess(text.index(f"[{med_new}] MED"), text.index(f"[{low}] LOW"))
        self.assertIn("from CMO", text)

    def test_only_pending_requests_are_listed(self):
        done = self.add("CAO", "finished")
        waiting = self.add("CAO", "waiting")
        self.assertTrue(fetch_queue.claim(done))
        ids = [r["id"] for r in fetch_queue.list_requests("PENDING")]
        self.assertIn(waiting, ids)
        self.assertNotIn(done, ids)

    def test_the_coo_processes_a_dummy_fetch_end_to_end(self):
        request_id = self.add("CAO", "nitrite in recirculating systems", "HIGH")
        result = self.coo(action="process", request_id=request_id)
        self.assertFalse(result["is_error"], result["content"])
        self.assertIn(f"Request #{request_id} is DONE", result["content"])
        row = self.status_of(request_id)
        self.assertEqual(row["status"], "DONE")
        self.assertEqual(sorted(row["fetchedUrls"]), sorted(h.url for h in self.search_hits))
        self.assertIn("Ingested 2 new page(s)", row["summaryText"])
        self.assertIsNotNone(row["processedAt"])
        self.assertEqual(len({c["document_id"] for c in self.store.upserted}), 2)  # two pages stored
        self.assertTrue(all(c["document_type"] == "auto_sourced_literature" and c["source_api"] == "web_search" for c in self.store.upserted))  # labelled public literature
        self.assertEqual(_sql('SELECT COUNT(*) AS n FROM "IngestedSource" WHERE "fetchRequestId" = %s', (request_id,))[0]["n"], 2)

    def test_the_same_run_step_by_step_start_fetch_complete(self):
        request_id = self.add("CTO", "kubernetes network policies")
        self.assertIn("now FETCHING", self.coo(action="start", request_id=request_id)["content"])
        self.assertEqual(self.status_of(request_id)["status"], "FETCHING")
        self.assertIn("Now complete the request", self.coo(action="fetch", request_id=request_id)["content"])
        self.assertEqual(self.status_of(request_id)["status"], "FETCHING")  # fetched, but not yet marked
        self.assertIn("is DONE", self.coo(action="complete", request_id=request_id)["content"])
        self.assertEqual(self.status_of(request_id)["status"], "DONE")

    def test_the_order_is_enforced(self):
        request_id = self.add("CAO", "order matters")
        for action in ("fetch", "complete"):
            self.assertTrue(self.coo(action=action, request_id=request_id)["is_error"], action)  # not started
        self.assertEqual(self.status_of(request_id)["status"], "PENDING")
        self.coo(action="start", request_id=request_id)
        self.assertTrue(self.coo(action="complete", request_id=request_id)["is_error"])  # started but never fetched: cannot be completed
        self.assertEqual(self.status_of(request_id)["status"], "FETCHING")
        self.assertTrue(self.coo(action="start", request_id=request_id)["is_error"])  # already FETCHING

    def test_a_request_is_claimed_by_only_one_caller(self):
        request_id = self.add("CAO", "race")
        self.assertEqual([fetch_queue.claim(request_id), fetch_queue.claim(request_id)], [True, False])
        self.assertFalse(fetch_queue.claim(10**9))
        for bad in (None, "1", True, 1.5):
            self.assertFalse(fetch_queue.claim(bad))

    def test_a_search_that_fails_marks_the_request_FAILED_with_the_honest_reason_and_stores_nothing(self):
        self.search_error = "could not reach the search service"
        request_id = self.add("CAO", "unreachable")
        result = self.coo(action="process", request_id=request_id)
        self.assertTrue(result["is_error"])
        row = self.status_of(request_id)
        self.assertEqual((row["status"], list(row["fetchedUrls"])), ("FAILED", []))
        self.assertIn("could not reach the search service", row["summaryText"])
        self.assertEqual((self.store.embedded, self.store.upserted), ([], []))

    def test_a_search_with_no_usable_results_is_FAILED_not_DONE(self):
        self.search_hits = []
        request_id = self.add("CAO", "nothing found")
        self.coo(action="process", request_id=request_id)
        row = self.status_of(request_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("found no usable pages", row["summaryText"])

    def test_when_every_store_attempt_fails_it_is_FAILED_and_the_claims_are_released_so_it_can_be_retried(self):
        self.store.fail_embed = True
        request_id = self.add("CAO", "embedding down")
        self.coo(action="process", request_id=request_id)
        row = self.status_of(request_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("EmbeddingError", row["summaryText"])  # only the error TYPE is recorded, never the provider's message
        self.assertEqual(_sql('SELECT COUNT(*) AS n FROM "IngestedSource" WHERE "url" LIKE %s', (f"%{TAG}%",))[0]["n"], 0)  # nothing is marked as ingested
        self.store.fail_embed = False
        retry = self.add("CAO", "embedding back")
        self.assertIn("is DONE", self.coo(action="process", request_id=retry)["content"])  # the same pages can now be stored

    def test_with_the_switch_off_nothing_is_fetched_or_changed_and_the_coo_is_told_to_say_so(self):
        request_id = self.add("CAO", "switch off")
        for value in ("", "off", "no", "0", "maybe"):
            with mock.patch.dict(os.environ, {"KNOWLEDGE_INGEST": value}):
                for action in ("start", "fetch", "complete", "process"):
                    self.outcome = exec_tools.ExecOutcome("coo")  # a fresh answer each time, so the per-answer action cap is not what is being tested
                    result = self.coo(action=action, request_id=request_id)
                    self.assertTrue(result["is_error"], (value, action))
                    self.assertIn("switched OFF", result["content"])
                    self.assertIn("do not say it was processed", result["content"])
        self.assertEqual(self.status_of(request_id)["status"], "PENDING")
        self.assertEqual((self.store.embedded, self.store.upserted), ([], []))
        self.assertIn("1", self.coo(action="list")["content"].split()[0])  # add and list still work with the switch off

    def test_a_stuck_FETCHING_request_goes_back_to_PENDING_after_the_timeout(self):
        request_id = self.add("CAO", "server died")
        fetch_queue.claim(request_id)
        _sql('UPDATE "FetchRequest" SET "createdAt" = %s WHERE "id" = %s', (datetime.utcnow() - timedelta(minutes=fetch_queue.STALE_MINUTES + 5), request_id))
        self.assertGreaterEqual(fetch_queue.reset_stale(), 1)
        self.assertEqual(self.status_of(request_id)["status"], "PENDING")

    def test_the_other_executives_do_not_have_the_tool_and_cannot_call_it(self):
        self.assertIn("manage_fetch_queue", exec_tools.tool_names("coo"))
        for role in ("cao", "cmo", "cto", "x"):
            self.assertNotIn("manage_fetch_queue", exec_tools.tool_names(role))
            text, ok, _, _ = run(knowledge_queue.handle(role, {"action": "list"}, exec_tools.ExecOutcome(role)))
            self.assertFalse(ok, role)
            self.assertIn("not one of your tools", text)
        self.assertIsNone(run(exec_tools.handle_block("cao", block(action="list"), exec_tools.ExecOutcome("cao"))))  # not even recognised by the other roles' handler

    def test_actions_per_turn_and_fetches_per_turn_are_bounded(self):
        out = exec_tools.ExecOutcome("coo")
        results = [run(exec_tools.handle_block("coo", block(action="list"), out)) for _ in range(knowledge_queue.MAX_QUEUE_ACTIONS_PER_TURN + 2)]
        self.assertTrue(results[-1]["is_error"])
        self.assertIn("Queue limit reached", results[-1]["content"])
        out = exec_tools.ExecOutcome("coo")
        ids = [self.add("CAO", f"bulk {i}") for i in range(knowledge_queue.MAX_FETCHES_PER_TURN + 1)]
        outcomes = [run(exec_tools.handle_block("coo", block(action="process", request_id=i), out)) for i in ids]
        self.assertTrue(outcomes[-1]["is_error"])
        self.assertIn("Fetch limit reached", outcomes[-1]["content"])
        self.assertEqual(self.status_of(ids[-1])["status"], "FETCHING")  # claimed but not fetched: the next turn's reset_stale puts it back

    def test_every_queue_action_is_logged_to_the_audit_table(self):
        before = _sql('SELECT COALESCE(MAX("id"),0) AS m FROM "AdminAuditLog"')[0]["m"]
        self.addCleanup(lambda: _sql('DELETE FROM "AdminAuditLog" WHERE "id" > %s AND "action" = %s', (before, "executive_queue")))
        self.coo(action="list")
        rows = _sql('SELECT "actorId","action","targetType" FROM "AdminAuditLog" WHERE "id" > %s AND "action" = %s', (before, "executive_queue"))
        self.assertEqual([(r["actorId"], r["action"], r["targetType"]) for r in rows], [("ai_coo", "executive_queue", "FetchRequest")])


# ================================================================================================ 3. THE SAME URL IS NEVER INGESTED TWICE (real Postgres)
class DeduplicationTests(WithDb):
    URL = f"https://example.org/{TAG}/dedup-page"

    def ingest(self, url=None, title="A paper", text=PAGE):
        return run(web_ingest.ingest_to_rag(title, text, self.URL if url is None else url))  # "" must stay "", not fall back to the default

    @staticmethod
    def rows():
        return _sql('SELECT COUNT(*) AS n FROM "IngestedSource" WHERE "url" LIKE %s', (f"%{TAG}%",))[0]["n"]

    def test_the_same_url_is_ingested_once_and_a_second_attempt_is_rejected_as_a_duplicate(self):
        first = self.ingest()
        self.assertEqual(first.status, "ingested")
        self.assertGreater(first.chunks, 0)
        embedded, stored = list(self.store.embedded), list(self.store.upserted)
        second = self.ingest()
        self.assertEqual((second.status, second.chunks), ("duplicate", 0))
        self.assertIn("already in the knowledge base", second.reason)
        self.assertEqual((self.store.embedded, self.store.upserted), (embedded, stored))  # nothing more was embedded (no money spent) or stored
        self.assertEqual(self.rows(), 1)
        self.assertTrue(run(web_ingest.already_ingested(self.URL)))

    def test_a_different_title_or_text_does_not_get_around_it(self):
        self.assertEqual(self.ingest().status, "ingested")
        again = self.ingest(title="Another title", text="Completely different text about feed conversion ratios in tilapia. " * 8)
        self.assertEqual(again.status, "duplicate")
        self.assertEqual(self.rows(), 1)

    def test_the_same_page_spelled_differently_is_still_a_duplicate_but_another_page_is_not(self):
        base = f"https://example.org/{TAG}/spell"
        self.assertEqual(self.ingest(base).status, "ingested")
        for spelling in (f"http://www.Example.org/{TAG}/spell/?utm_campaign=z&gclid=1#frag", f"https://example.org:443/{TAG}//spell", base + "/"):
            self.assertEqual(self.ingest(spelling).status, "duplicate", spelling)
        self.assertEqual(self.ingest(f"https://example.org/{TAG}/spell-two").status, "ingested")  # a genuinely different page still goes in
        self.assertEqual(self.rows(), 2)

    def test_two_simultaneous_attempts_cannot_both_win(self):
        async def both():
            return await asyncio.gather(*(web_ingest.ingest_to_rag("A paper", PAGE, self.URL) for _ in range(2)))

        results = run(both())
        self.assertEqual(sorted(r.status for r in results), ["duplicate", "ingested"])
        self.assertEqual(len({c["document_id"] for c in self.store.upserted}), 1)  # one page's vectors, not two
        self.assertEqual(self.rows(), 1)

    def test_a_failed_attempt_does_not_block_the_url_forever(self):
        self.store.fail_embed = True
        self.assertEqual(self.ingest().status, "failed")
        self.assertEqual(self.rows(), 0)  # the claim was released
        self.store.fail_embed = False
        self.assertEqual(self.ingest().status, "ingested")

    def test_a_bad_url_is_rejected_before_anything_is_claimed_or_embedded(self):
        for bad in ("", "ftp://example.org/x", "https://localhost/x", "https://10.0.0.1/x", "https://user:pw@example.org/x", "not a url"):
            self.assertEqual(self.ingest(bad).status, "rejected", bad)
        self.assertEqual((self.store.embedded, self.store.upserted, self.rows()), ([], [], 0))

    def test_the_coo_skips_pages_a_second_request_finds_again(self):
        first, second = self.add("CAO", "first ask"), self.add("CMO", "second ask")
        self.assertIn("is DONE", self.coo(action="process", request_id=first)["content"])
        embedded = list(self.store.embedded)
        self.outcome = exec_tools.ExecOutcome("coo")  # a new answer
        result = self.coo(action="process", request_id=second)
        self.assertFalse(result["is_error"], result["content"])  # every page was already there: that is DONE, not a failure
        row = self.status_of(second)
        self.assertEqual((row["status"], list(row["fetchedUrls"])), ("DONE", []))  # it ingested nothing, so it lists nothing
        self.assertIn("Ingested 0 new page(s)", row["summaryText"])
        self.assertIn("skipped 2 already in the knowledge base", row["summaryText"])
        self.assertEqual(self.store.embedded, embedded)  # not embedded a second time
        self.assertEqual(self.rows(), 2)

    def test_a_request_that_finds_one_old_and_one_new_page_ingests_only_the_new_one(self):
        first, second = self.add("CAO", "first ask"), self.add("CAO", "overlapping ask")
        self.coo(action="process", request_id=first)  # stores paper-1 and paper-2
        self.outcome = exec_tools.ExecOutcome("coo")
        self.search_hits = [hit(1), hit(3)]
        self.coo(action="process", request_id=second)
        row = self.status_of(second)
        self.assertEqual((row["status"], list(row["fetchedUrls"])), ("DONE", [hit(3).url]))
        self.assertIn("skipped 1 already", row["summaryText"])
        self.assertEqual(self.rows(), 3)
