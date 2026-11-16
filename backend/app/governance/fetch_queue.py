"""
The COO's FETCH QUEUE (the COO acts as Chief Information Officer): create a request, list the queue by priority, claim one, finish it.

Plain SQL on the same helpers as the rest of the governance layer. Nothing here fetches or ingests: the COO's ``manage_fetch_queue`` tool
(app/agents/exec_tools.py) does that, using app/rag/web_search.py and app/rag/web_ingest.py. Every function NEVER raises into a caller that must go on:
the ``create``/``claim`` family returns a plain result, and a database problem is a ``None`` / error string, not an exception.

RULES IN CODE:
  * Who may ask: only the CAO, CTO or CMO (FetchRequester). The COO does not queue for itself.
  * Priority is HIGH, MED or LOW (default MED); the queue is listed HIGH first, then MED, then LOW, then oldest first.
  * A request moves PENDING -> FETCHING -> DONE or FAILED, never backwards. ``claim`` is ONE atomic UPDATE that only succeeds for a request that is still
    PENDING, so two runs cannot both take the same request.
  * Bounded: the query is cut to MAX_QUERY_CHARS, at most MAX_PENDING requests may wait (a flood is refused), a list shows at most MAX_LIST.
  * A request that stays FETCHING (the server died mid-run) is put back to PENDING by ``reset_stale`` once it is older than STALE_MINUTES.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.governance import db

logger = logging.getLogger(__name__)

REQUESTERS = ("CAO", "CTO", "CMO")
PRIORITIES = ("HIGH", "MED", "LOW")
STATUSES = ("PENDING", "FETCHING", "DONE", "FAILED")
MAX_QUERY_CHARS = 300
MIN_QUERY_CHARS = 5
MAX_PENDING = 50
MAX_LIST = 25
MAX_SUMMARY_CHARS = 2000
MAX_URLS = 20
STALE_MINUTES = 30
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_COLUMNS = '"id","requester","query","priority","status","fetchedUrls","summaryText","createdAt","processedAt"'


def clean_query(query: Any) -> str:
    return " ".join(_CONTROL.sub(" ", query).split())[:MAX_QUERY_CHARS] if isinstance(query, str) else ""


def _create_sync(requester: str, query: str, priority: str) -> tuple[str, int | None]:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) AS n FROM "FetchRequest" WHERE "status" = CAST(%s AS "FetchStatus")', ("PENDING",))
        if db.fetch_all(cur)[0]["n"] >= MAX_PENDING:
            return "queue_full", None
        cur.execute('INSERT INTO "FetchRequest" ("requester","query","priority") VALUES (CAST(%s AS "FetchRequester"),%s,CAST(%s AS "FetchPriority")) RETURNING "id"',
                    (requester, query, priority))
        return "ok", db.fetch_all(cur)[0]["id"]


def create_request(requester: str, query: str, priority: str = "MED") -> tuple[str, int | None]:
    """(status, id). status: ok | bad_requester | bad_priority | bad_query | queue_full | unavailable."""
    who, level, text = str(requester or "").strip().upper(), str(priority or "MED").strip().upper(), clean_query(query)
    if who not in REQUESTERS:
        return "bad_requester", None
    if level not in PRIORITIES:
        return "bad_priority", None
    if len(text) < MIN_QUERY_CHARS:
        return "bad_query", None
    if not db.is_configured():
        return "unavailable", None
    try:
        return _create_sync(who, text, level)
    except Exception as exc:
        logger.error("fetch_queue: could not create a request (%s)", type(exc).__name__)
        return "unavailable", None


def _list_sync(status: str, limit: int) -> list[dict[str, Any]]:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT {_COLUMNS} FROM "FetchRequest" WHERE "status" = CAST(%s AS "FetchStatus") '
                    'ORDER BY CASE "priority" WHEN CAST(%s AS "FetchPriority") THEN 0 WHEN CAST(%s AS "FetchPriority") THEN 1 ELSE 2 END, "createdAt", "id" LIMIT %s',
                    (status, "HIGH", "MED", limit))
        return db.fetch_all(cur)


def list_requests(status: str = "PENDING", limit: int = MAX_LIST) -> list[dict[str, Any]] | None:
    """The requests with ``status``, HIGH first then MED then LOW, oldest first within a priority. None when the database is unavailable."""
    if status not in STATUSES or not db.is_configured():
        return None
    try:
        return _list_sync(status, max(1, min(MAX_LIST, int(limit))))
    except Exception as exc:
        logger.error("fetch_queue: could not list (%s)", type(exc).__name__)
        return None


def _get_sync(request_id: int) -> dict[str, Any] | None:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT {_COLUMNS} FROM "FetchRequest" WHERE "id" = %s', (request_id,))
        rows = db.fetch_all(cur)
        return rows[0] if rows else None


def get_request(request_id: int) -> dict[str, Any] | None:
    if isinstance(request_id, bool) or not isinstance(request_id, int) or not db.is_configured():
        return None
    try:
        return _get_sync(request_id)
    except Exception:
        return None


def _claim_sync(request_id: int) -> bool:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE "FetchRequest" SET "status" = CAST(%s AS "FetchStatus"), "processedAt" = NULL WHERE "id" = %s AND "status" = CAST(%s AS "FetchStatus") '
                    'RETURNING "id"', ("FETCHING", request_id, "PENDING"))
        return bool(db.fetch_all(cur))


def claim(request_id: int) -> bool:
    """Mark a PENDING request FETCHING. True only for the ONE caller that won it (atomic); False if it is not PENDING or not found. Never raises."""
    if isinstance(request_id, bool) or not isinstance(request_id, int) or not db.is_configured():
        return False
    try:
        return _claim_sync(request_id)
    except Exception as exc:
        logger.error("fetch_queue: could not claim #%s (%s)", request_id, type(exc).__name__)
        return False


def _finish_sync(request_id: int, status: str, urls: list[str], summary: str) -> bool:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE "FetchRequest" SET "status" = CAST(%s AS "FetchStatus"), "fetchedUrls" = CAST(%s AS text[]), "summaryText" = %s, "processedAt" = NOW() '
                    'WHERE "id" = %s AND "status" = CAST(%s AS "FetchStatus") RETURNING "id"',
                    (status, db.text_array_param(urls), summary, request_id, "FETCHING"))
        return bool(db.fetch_all(cur))


def finish(request_id: int, status: str, urls: list[str], summary: str) -> bool:
    """Mark a FETCHING request DONE or FAILED, with the URLs it ingested and a code-written summary. True when it was written. Never raises."""
    if status not in ("DONE", "FAILED") or isinstance(request_id, bool) or not isinstance(request_id, int) or not db.is_configured():
        return False
    try:
        return _finish_sync(request_id, status, [str(u)[:500] for u in urls[:MAX_URLS]], str(summary)[:MAX_SUMMARY_CHARS])
    except Exception as exc:
        logger.error("fetch_queue: could not finish #%s (%s)", request_id, type(exc).__name__)
        return False


def _reset_stale_sync() -> int:
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE "FetchRequest" SET "status" = CAST(%s AS "FetchStatus") WHERE "status" = CAST(%s AS "FetchStatus") '
                    "AND \"createdAt\" < NOW() - (%s * INTERVAL '1 minute') AND \"processedAt\" IS NULL RETURNING \"id\"", ("PENDING", "FETCHING", STALE_MINUTES))
        return len(db.fetch_all(cur))


def reset_stale() -> int:
    """Put requests stuck in FETCHING (the server died mid-run) back to PENDING. Returns how many. Never raises."""
    if not db.is_configured():
        return 0
    try:
        return _reset_stale_sync()
    except Exception:
        return 0


def render(rows: list[dict[str, Any]] | None, status: str = "PENDING") -> str:
    if rows is None:
        return "ERROR: the fetch queue is not available"
    if not rows:
        return f"The queue has no {status} requests."
    return f"{len(rows)} {status} request(s), highest priority first:\n" + "\n".join(
        f"[{r['id']}] {r['priority']} | from {r['requester']} | {r['query']} | asked {r['createdAt']:%Y-%m-%d %H:%M}" for r in rows)
