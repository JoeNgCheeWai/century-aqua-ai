"""
manage_fetch_queue: the COO's tool for its KNOWLEDGE QUEUE (the COO acts as Chief Information Officer). Phase 1.

THE COO'S ACTIONS (one tool, one ``action`` argument):
  add       put a request on the queue (requester CAO/CTO/CMO, a query, a priority). The CEO relays "COO, please add a fetch request to the queue for X".
  list      the PENDING requests, HIGH first, then MED, then LOW, oldest first.
  start     mark ONE pending request FETCHING (an atomic claim: only one caller can win it).
  fetch     run the search and ingest tools for a FETCHING request: web_search, then ingest_to_rag for each result (max 5).
  complete  finish a request that was fetched THIS turn. CODE chooses DONE or FAILED from what really happened, and writes the summary itself.
  process   start + fetch + complete in one step.

THIS TOOL WRITES (the database queue, the vector database, an audit line) and it reaches the public web. Unlike the other executive tools it is not read-only,
so it is held to these rules, all in code:
  * COO ONLY. The role is code's. No other executive has this tool, and none can ask for it.
  * SWITCHED OFF BY DEFAULT for the part that spends money and reads the web. ``add`` and ``list`` always work. ``start``, ``fetch``, ``complete`` and
    ``process`` need KNOWLEDGE_INGEST=on (same on/true/1/yes rule as COO_AUTO_APPROVAL), and they say so plainly when it is off.
  * BOUNDED: at most MAX_QUEUE_ACTIONS_PER_TURN actions and MAX_FETCHES_PER_TURN fetches in one answer; at most 5 pages per request.
  * NO FREE TEXT. The text that is ingested is the search tool's own result, never a string the model wrote. The model chooses WHICH request to run.
  * NEVER TWICE. A page already in the knowledge base is skipped (ingest_to_rag's atomic URL claim), before any embedding is paid for.
  * HONEST OUTCOME. DONE means the search worked and nothing failed: new pages were stored, or every result was already there. FAILED means the search
    failed, found nothing usable, or every attempt to store failed. The summary_text is written here from the counts; no model text is stored.
  * LOGGED. Every action is printed and written to AdminAuditLog (action ``executive_queue``).
What this cannot do: judge whether a page is true or relevant. Everything it stores is labelled public web literature, not Century Aqua data.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.governance import db, fetch_queue
from app.rag import web_ingest, web_search

logger = logging.getLogger(__name__)

TOOL_NAME = "manage_fetch_queue"
ENV_SWITCH = "KNOWLEDGE_INGEST"
ACTIONS = ("add", "list", "start", "fetch", "complete", "process")
NEEDS_SWITCH = ("start", "fetch", "complete", "process")
MAX_QUEUE_ACTIONS_PER_TURN = 8
MAX_FETCHES_PER_TURN = 2

TOOL_SPEC: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Manage the knowledge-fetch queue (you are the Chief Information Officer). Actions: add (requester CAO/CTO/CMO, query, priority HIGH/MED/LOW), "
        "list (pending requests, highest priority first), start (mark one request FETCHING), fetch (search the web and ingest the results for a FETCHING "
        "request), complete (finish it: the system decides DONE or FAILED from what really happened), process (start, fetch and complete in one step). "
        "Fetching and ingesting need the owner's switch to be on; the tool says so if it is off. Pages already in the knowledge base are skipped, never "
        "ingested twice. You never write the text that is ingested: it comes from the search results. Say only what the tool result says happened."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "request_id": {"type": "integer", "description": "The request number, for start, fetch, complete and process."},
            "requester": {"type": "string", "enum": list(fetch_queue.REQUESTERS), "description": "For add: which executive asked."},
            "query": {"type": "string", "description": "For add: the topic to find sources on."},
            "priority": {"type": "string", "enum": list(fetch_queue.PRIORITIES), "description": "For add. Default MED."},
        },
        "required": ["action"],
    },
}


def enabled() -> bool:
    """Is fetching and ingesting switched on? Only the exact values on/true/1/yes; unset or anything else is OFF. Read at call time."""
    return os.getenv(ENV_SWITCH, "").strip().lower() in ("on", "true", "1", "yes")


def _log_sync(entry: dict[str, Any]) -> None:
    with db.transaction() as conn:
        conn.cursor().execute(
            'INSERT INTO "AdminAuditLog" ("actorId","action","targetType","targetId","details") VALUES (%s,%s,%s,%s,CAST(%s AS jsonb))',
            ("ai_coo", "executive_queue", "FetchRequest", str(entry.get("request_id") or "")[:60], db.json_param(entry)))


async def _log(entry: dict[str, Any]) -> None:
    print(f"COO queue: action={entry['action']} request={entry.get('request_id')} ok={entry['ok']} {entry.get('note', '')}", flush=True)
    if not db.is_configured():
        return
    try:
        await asyncio.wait_for(db.run(_log_sync, entry), timeout=4.0)
    except Exception as exc:  # best-effort: the queue is never blocked by its log
        logger.error("knowledge_queue: could not log (%s)", type(exc).__name__)


def summarise(query: str, search: web_search.SearchResult, results: list[tuple[str, web_ingest.IngestOutcome]]) -> tuple[str, str]:
    """(DONE | FAILED, the summary), from the counts alone. Written by code so a model can never claim more than happened."""
    if not search.ok:
        return "FAILED", f"The search failed ({search.backend}): {search.error}. Nothing was ingested."
    if not search.hits:
        return "FAILED", f"The search ({search.backend}) found no usable pages for '{query[:120]}'. Nothing was ingested."
    count = {k: sum(1 for _, o in results if o.status == k) for k in ("ingested", "duplicate", "rejected", "failed")}
    chunks = sum(o.chunks for _, o in results if o.status == "ingested")
    line = (f"Searched with {search.backend}: {len(search.hits)} result(s). Ingested {count['ingested']} new page(s) ({chunks} chunks), skipped "
            f"{count['duplicate']} already in the knowledge base, rejected {count['rejected']}, failed {count['failed']}.")
    problems = "; ".join(f"{u[:80]}: {o.reason}" for u, o in results if o.status in ("rejected", "failed") and o.reason)[:600]
    if count["ingested"] == 0 and count["duplicate"] == 0:
        return "FAILED", line + (f" Why: {problems}." if problems else "")
    return "DONE", line + (f" Problems: {problems}." if problems else "")


async def _run_fetch(request: dict[str, Any], outcome: Any) -> tuple[str, bool]:
    query = request["query"]
    search = await asyncio.to_thread(web_search.web_search, query)
    results: list[tuple[str, web_ingest.IngestOutcome]] = []
    for hit in search.hits if search.ok else []:
        results.append((hit.url, await web_ingest.ingest_to_rag(hit.title, hit.snippet, hit.url, fetch_request_id=request["id"])))
    status, summary = summarise(query, search, results)
    urls = [u for u, o in results if o.status == "ingested"]
    outcome.fetched[request["id"]] = {"status": status, "summary": summary, "urls": urls}
    outcome.ingested_urls.extend(urls)
    detail = "\n".join(f"- {o.status}: {u}" + (f" ({o.reason})" if o.reason else "") for u, o in results)
    return f"{summary}\n{detail}".strip(), status == "DONE"


async def handle(role: str, args: dict[str, Any], outcome: Any) -> tuple[str, bool, str, int | None]:
    """Run one manage_fetch_queue call. Returns (text for the model, ok, target, rows). Never raises."""
    action = args.get("action")
    if role != "coo":
        return f"ERROR: {TOOL_NAME} is not one of your tools", False, "", None
    if action not in ACTIONS:
        return f"ERROR: action must be one of {', '.join(ACTIONS)}", False, "", None
    if outcome.queue_actions >= MAX_QUEUE_ACTIONS_PER_TURN:
        return f"Queue limit reached ({MAX_QUEUE_ACTIONS_PER_TURN} actions per answer). Report what you have done.", False, "", None
    outcome.queue_actions += 1
    request_id = args.get("request_id") if isinstance(args.get("request_id"), int) and not isinstance(args.get("request_id"), bool) else None
    target = f"fetch_queue:{action}"
    try:
        if action == "add":
            status, new_id = await db.run(fetch_queue.create_request, args.get("requester"), args.get("query"), args.get("priority") or "MED")
            messages = {"bad_requester": "requester must be CAO, CTO or CMO", "bad_priority": "priority must be HIGH, MED or LOW",
                        "bad_query": f"the query needs at least {fetch_queue.MIN_QUERY_CHARS} characters", "queue_full": f"the queue is full ({fetch_queue.MAX_PENDING} pending)",
                        "unavailable": "the queue database is not available"}
            if status != "ok":
                return f"ERROR: could not add the request: {messages.get(status, status)}", False, target, None
            return f"Added request #{new_id} to the queue (PENDING).", True, target, new_id
        if action == "list":
            rows = await db.run(fetch_queue.list_requests, "PENDING")
            return fetch_queue.render(rows), rows is not None, target, None if rows is None else len(rows)
        if not enabled():
            return (f"Knowledge fetching is switched OFF ({ENV_SWITCH} is not on), so nothing was fetched or ingested and the request stays as it is. "
                    "Tell the CEO plainly; do not say it was processed."), False, target, None
        if request_id is None:
            return "ERROR: this action needs a request_id (list the queue first)", False, target, None
        await db.run(fetch_queue.reset_stale)
        if action in ("start", "process"):
            if not await db.run(fetch_queue.claim, request_id):
                row = await db.run(fetch_queue.get_request, request_id)
                why = "there is no such request" if row is None else f"it is {row['status']}, not PENDING, so it cannot be started"
                return f"ERROR: could not start request #{request_id}: {why}.", False, target, None
            outcome.started.add(request_id)
            if action == "start":
                return f"Request #{request_id} is now FETCHING.", True, target, request_id
        if action in ("fetch", "process"):
            row = await db.run(fetch_queue.get_request, request_id)
            if row is None or row["status"] != "FETCHING":
                return f"ERROR: request #{request_id} must be FETCHING first (start it).", False, target, None
            if request_id in outcome.fetched:
                return f"Request #{request_id} was already fetched in this answer. Complete it.", False, target, None
            if len(outcome.fetched) >= MAX_FETCHES_PER_TURN:
                return f"Fetch limit reached ({MAX_FETCHES_PER_TURN} per answer). Complete what you have fetched.", False, target, None
            text, ok = await _run_fetch(row, outcome)
            if action == "fetch":
                return text + "\nNow complete the request.", True, target, request_id
        row = outcome.fetched.get(request_id)
        if row is None:
            return f"ERROR: request #{request_id} was not fetched in this answer, so it cannot be completed. Fetch it first.", False, target, None
        if request_id in outcome.completed:
            return f"Request #{request_id} is already completed.", False, target, request_id
        if not await db.run(fetch_queue.finish, request_id, row["status"], row["urls"], row["summary"]):
            return f"ERROR: request #{request_id} could not be marked {row['status']} (it was not FETCHING).", False, target, None
        outcome.completed.add(request_id)
        return f"Request #{request_id} is {row['status']}. {row['summary']}", row["status"] == "DONE", target, request_id
    except Exception as exc:  # the queue must never take the app down
        logger.error("knowledge_queue: %s failed (%s)", action, type(exc).__name__)
        return "ERROR: the queue action could not be completed", False, target, None
