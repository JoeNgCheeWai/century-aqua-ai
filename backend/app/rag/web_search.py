"""
tavily_search: the fetcher for the COO's knowledge queue (Phase 1). ``web_search(query)`` returns up to 5 results, each with a title, a source URL and a
CLEAN text snippet. It is a plain GET/POST for public search results: it changes nothing anywhere and is NOT offered to a model on its own (only the
COO's ``manage_fetch_queue`` runs it, for a queued request).

TWO BACKENDS, chosen by what is configured (never silently):
  * Tavily (https://api.tavily.com/search, ``Authorization: Bearer <TAVILY_API_KEY>``) when TAVILY_API_KEY is set. Best results.
  * A keyless FALLBACK when it is not: the Wikipedia search API (public, no key, https). It is narrower (an encyclopaedia, not the open web) and the
    result says ``backend: "wikipedia-fallback"`` so a person can see which one answered. This is the "generic wrapper while Tavily is not available".
Neither backend is ever called when ``SEARCH_BACKEND=off``.

SAFETY. The key is read at call time and never logged or returned. Only https result URLs are kept, with a real public-looking host (the same check as
ingestion); a result that fails it is dropped. Snippets are stripped of HTML and control characters, secret-looking text is masked, and each is cut to
MAX_SNIPPET_CHARS. Result text is untrusted DATA: the COO's tool wraps it in delimiters and the ingestion label says it is public web text.
It NEVER raises: a failure is ``SearchResult.error`` with a short, safe message.
"""

from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from app.rag.web_ingest import clean_text, url_key

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
WIKI_URL = "https://en.wikipedia.org/w/api.php"
MAX_RESULTS = 5
MAX_SNIPPET_CHARS = 4000
MAX_QUERY_CHARS = 300
TIMEOUT_SECONDS = 20
USER_AGENT = "century-aqua-ai-knowledge-fetcher"
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class Hit:
    title: str
    url: str
    snippet: str


@dataclass
class SearchResult:
    ok: bool
    backend: str = ""
    hits: list[Hit] = field(default_factory=list)
    error: str = ""


def backend_name() -> str:
    """Which backend a search would use right now: 'tavily', 'wikipedia-fallback' or 'off'."""
    if os.getenv("SEARCH_BACKEND", "").strip().lower() == "off":
        return "off"
    return "tavily" if os.getenv("TAVILY_API_KEY", "").strip() else "wikipedia-fallback"


def _plain(text: Any, limit: int) -> str:
    return clean_text(html.unescape(_TAGS.sub(" ", str(text or ""))))[:limit]


def _hit(title: Any, url: Any, snippet: Any) -> Hit | None:
    """A cleaned result, or None when its URL is not an https public page or it has no text."""
    try:
        link = str(url or "").strip()
        if not link.lower().startswith("https://"):
            return None
        url_key(link)
    except ValueError:
        return None
    text, name = _plain(snippet, MAX_SNIPPET_CHARS), " ".join(_plain(title, 200).split())
    return Hit(name or link, link, text) if text else None


def _tavily(query: str, key: str) -> SearchResult:
    response = requests.post(TAVILY_URL, json={"query": query, "max_results": MAX_RESULTS, "search_depth": "basic", "include_answer": False,
                                               "include_raw_content": False}, headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
                             timeout=TIMEOUT_SECONDS)
    if response.status_code != 200:
        return SearchResult(False, "tavily", error=f"Tavily answered {response.status_code}" + (" (check TAVILY_API_KEY)" if response.status_code in (401, 403) else ""))
    hits = [h for r in (response.json().get("results") or []) if isinstance(r, dict) for h in [_hit(r.get("title"), r.get("url"), r.get("content"))] if h]
    return SearchResult(True, "tavily", hits[:MAX_RESULTS])


def _wikipedia(query: str) -> SearchResult:
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": query, "gsrlimit": MAX_RESULTS, "prop": "extracts|info",
              "exintro": 1, "explaintext": 1, "exlimit": MAX_RESULTS, "inprop": "url", "redirects": 1}
    response = requests.get(WIKI_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    if response.status_code != 200:
        return SearchResult(False, "wikipedia-fallback", error=f"the fallback search answered {response.status_code}")
    pages = sorted(((response.json().get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))
    hits = [h for p in pages if isinstance(p, dict) for h in [_hit(p.get("title"), p.get("fullurl"), p.get("extract"))] if h]
    return SearchResult(True, "wikipedia-fallback", hits[:MAX_RESULTS])


def web_search(query: str) -> SearchResult:
    """Top results for ``query`` (at most 5), each with a clean snippet and a source URL. Blocking (HTTP): call it from a worker thread."""
    try:
        text = " ".join(str(query or "").split())[:MAX_QUERY_CHARS]
        if len(text) < 3:
            return SearchResult(False, backend_name(), error="the query is too short")
        backend = backend_name()
        if backend == "off":
            return SearchResult(False, "off", error="web search is switched off (SEARCH_BACKEND=off)")
        return _tavily(text, os.getenv("TAVILY_API_KEY", "").strip()) if backend == "tavily" else _wikipedia(text)
    except requests.Timeout:
        return SearchResult(False, backend_name(), error="the search did not answer in time")
    except requests.RequestException as exc:
        logger.error("web_search: request failed (%s)", type(exc).__name__)
        return SearchResult(False, backend_name(), error="could not reach the search service")
    except Exception as exc:  # a search must never take the app down
        logger.error("web_search: failed (%s)", type(exc).__name__)
        return SearchResult(False, backend_name(), error="the search could not be completed")


def render_hits(result: SearchResult) -> str:
    """The results as text for a person or a log: numbered, with the source URL. (Snippets are cut short here.)"""
    if not result.ok:
        return f"SEARCH FAILED ({result.backend}): {result.error}"
    if not result.hits:
        return f"No results from {result.backend}."
    return f"{len(result.hits)} result(s) from {result.backend}:\n" + "\n".join(f"[{i}] {h.title} | {h.url} | {h.snippet[:160]}" for i, h in enumerate(result.hits, 1))
