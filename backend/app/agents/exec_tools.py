"""
The CAO's, COO's and CMO's READ-ONLY tools (executive capability parity with the CTO's github_read_file).

  CAO: read_farm_records (water_quality, feeding, mortality, sampling), read_knowledge_base (the RAG library), diagnose_recommendation
  COO: read_farm_records (inventory, cost), read_audit_log, read_operations_summary, diagnose_operational_routing
  CMO: read_brand_file (brand docs, past copy, public assets), diagnose_asset_request   (plus its existing generate_image)

THE RULES, ALL IN CODE:
  * READ-ONLY, with TWO exceptions. (1) The COO's manage_fetch_queue (knowledge_queue.py) writes the fetch queue and the knowledge base. It is COO-only,
    switched off by default (KNOWLEDGE_INGEST), bounded and logged. (2) EVERY executive's save_memory (memory_tools.py) writes one lesson to the shared
    episodic memory (the ExecutiveMemory table and its own Pinecone namespace), under the caller's own role, bounded and logged; search_memory reads it.
    Nothing here writes to a repository. The CAO and COO have no GitHub tool at all.
  * SCOPED ON THE SERVER. The role is passed by code, never taken from a tool argument. A record kind or file outside the role's scope is refused.
  * NO SECRETS, NO OTHER ROLE'S PRIVATE DATA. Secret files are refused by name and secret-looking text is masked. The COO's audit read returns
    decision metadata only (never a question or an answer's text). The CMO reads brand and public files only, not Mission Control or staff code.
  * BOUNDED. At most MAX_READS_PER_TURN reads, MAX_DIAGNOSTICS_PER_TURN diagnostic, MAX_TOOL_ROUNDS rounds, then a last call with tools off.
  * LOGGED. Every read is kept in ExecOutcome.reads, printed, and written best-effort to AdminAuditLog.
  * READ-BEFORE-WRITE. A proposal names the things it changes (``targets``); code drops it if any target was not read this turn (exec_checks).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents import execution_plan as plan_rules
from app.agents import knowledge_queue, memory_tools
from app.governance import admin_store, db, farm_records, operations_log

logger = logging.getLogger(__name__)

MAX_READS_PER_TURN = 6
MAX_DIAGNOSTICS_PER_TURN = 1
MAX_TOOL_ROUNDS = 6
MAX_TEXT_CHARS = 6000
MAX_AUDIT_ROWS = 25
KB_TARGET, AUDIT_TARGET, SUMMARY_TARGET = "knowledge_base", "audit_log", "operations_summary"
ROLES = ("cao", "coo", "cmo")

# What the CMO may read (a prefix match on the lower-case path): brand documents, the site's public copy and components, public assets.
BRAND_PREFIXES = (
    "frontend/src/components/", "frontend/src/app/page.tsx", "frontend/src/app/layout.tsx", "frontend/src/app/globals.css",
    "frontend/public/", "backend/app/prompts/cmo_prompt.py", "backend/app/prompts/persona.py", "project_context.md", "readme.md", "frontend/readme.md",
)
# ...and never these: Mission Control and the staff area belong to other roles.
BRAND_REFUSED = ("frontend/src/components/mc/", "frontend/src/components/staff/")
BRAND_RULE_FILES = ("backend/app/prompts/cmo_prompt.py", "project_context.md")  # reading one counts as "brand rules read"


@dataclass
class ExecOutcome:
    """What a role's tools did during ONE answer: facts for code to use afterwards. Nothing here is model-written."""

    role: str
    reads: list[dict[str, Any]] = field(default_factory=list)  # every read attempt: {tool, target, ok, rows}
    read_targets: set[str] = field(default_factory=set)  # targets read successfully (an empty result still counts: it was looked at)
    texts: dict[str, str] = field(default_factory=dict)  # text of each file read, by target
    kb_sources: list[str] = field(default_factory=list)  # knowledge-base documents actually retrieved this turn
    # The COO's knowledge queue (knowledge_queue.py): what it did this answer, so code can bound it and complete only what was really fetched.
    queue_actions: int = 0
    started: set[int] = field(default_factory=set)
    fetched: dict[int, dict[str, Any]] = field(default_factory=dict)
    completed: set[int] = field(default_factory=set)
    ingested_urls: list[str] = field(default_factory=list)
    # The executives' episodic memory (memory_tools.py): how many saves and searches this answer made, so code can bound them.
    memory_saves: int = 0
    memory_searches: int = 0
    inventory_rows: int = 0
    cost_rows: int = 0
    farm_rows: int = 0
    diagnostic: dict[str, Any] | None = None
    diagnosed: int = 0

    def attempted(self) -> int:
        return len(self.reads)


def norm(target: Any) -> str:
    return str(target).strip().lower()


def _spec(name: str, description: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, **({"required": required} if required else {})}}


def _farm_spec(role: str) -> dict[str, Any]:
    kinds = list(farm_records.kinds_for(role))
    return _spec(
        "read_farm_records",
        f"Read recent farm records you are allowed to see ({', '.join(kinds)}). Read-only. An empty result means no records are loaded: "
        "say so, never estimate. ALWAYS read before you propose something that depends on them.",
        {"kind": {"type": "string", "enum": kinds}, "parameter": {"type": "string", "description": "e.g. dissolved_oxygen"},
         "site": {"type": "string"}, "days": {"type": "integer", "description": "how far back, 1-365 (default 30)"}})


def _audit_id_prop() -> dict[str, Any]:
    return {"audit_id": {"type": "integer", "description": "The audit entry number. Leave out for the most recent relevant one."}}


SPECS: dict[str, list[dict[str, Any]]] = {
    "cao": [
        _farm_spec("cao"),
        _spec("read_knowledge_base", "Search Century Aqua's document library (trial data, patents, regulations, market reports). Read-only.",
              {"query": {"type": "string"}}, ["query"]),
        _spec("diagnose_recommendation",
              "Find out WHY a recommendation or protocol was rejected, left pending or judged unusable, and what data was missing. Read-only. "
              "Use it immediately when asked why; never offer to investigate.", _audit_id_prop()),
    ],
    "coo": [
        _farm_spec("coo"),
        _spec("read_audit_log",
              "Read recent decisions in the audit log: who proposed what, risk, the human or COO decision, the pull request. Metadata only, "
              "never the text of a question or answer. Read-only.",
              {"agent_id": {"type": "string", "enum": ["cao", "coo", "cto", "cmo"]}, "days": {"type": "integer"}, "limit": {"type": "integer"}}),
        _spec("read_operations_summary", "Read the code-written Operations Summary (exact counts of tasks the COO reviewed and approved). Read-only.",
              {"days": {"type": "integer"}}),
        _spec("diagnose_operational_routing",
              "Find out WHY a task did not reach COO review, or why a COO verdict failed. Read-only. "
              "Use it immediately when asked why; never offer to investigate.", _audit_id_prop()),
        knowledge_queue.TOOL_SPEC,  # the one tool here that WRITES (the queue, the knowledge base): COO only, switched off by default, bounded
    ],
    "cmo": [
        _spec("read_brand_file",
              "Read ONE brand document, past copy, site component or public asset record from the project repository (read-only). "
              "ALWAYS read an existing file before you propose to change it, and read the brand rules before you propose copy. "
              "Secret and private files cannot be read.",
              {"file_path": {"type": "string", "description": "Path from the repository root, e.g. frontend/src/app/page.tsx"}}, ["file_path"]),
        _spec("diagnose_asset_request",
              "Find out WHY an asset request was rejected or dropped, and which brand rule or parser check failed. Read-only. "
              "Use it immediately when asked why; never offer to investigate.", _audit_id_prop()),
    ],
}
for _role in SPECS:  # every executive gets the shared episodic memory (save_memory, search_memory): one record, found by meaning, kept for later chats
    SPECS[_role].extend(memory_tools.SPECS)
READ_TOOLS = {"read_farm_records", "read_knowledge_base", "read_audit_log", "read_operations_summary", "read_brand_file"}
QUEUE_TOOL = knowledge_queue.TOOL_NAME  # manage_fetch_queue: handled by knowledge_queue.handle, not by run_read
DIAGNOSTIC_TOOLS = {"diagnose_recommendation": "cao", "diagnose_operational_routing": "coo", "diagnose_asset_request": "cmo"}


def tool_names(role: str) -> list[str]:
    return [s["name"] for s in SPECS.get(role, [])]


def log_tools_available(role: str, extra: list[str] | None = None, suffix: str = "") -> None:
    """One line at the start of an answer, like the CTO's: a print, so Render shows it. Never a secret."""
    print(f"{role.upper()} tools available: {[*(extra or []), *tool_names(role)]}{suffix}", flush=True)


def _log_read_sync(role: str, entry: dict[str, Any]) -> None:
    with db.transaction() as conn:
        conn.cursor().execute(
            'INSERT INTO "AdminAuditLog" ("actorId","action","targetType","targetId","details") VALUES (%s,%s,%s,%s,CAST(%s AS jsonb))',
            (f"ai_{role}", "executive_read", "Tool", str(entry.get("tool"))[:60], db.json_param(entry)))


async def _log(role: str, entry: dict[str, Any]) -> None:
    print(f"{role.upper()} read: {entry['tool']} target={entry['target']} ok={entry['ok']} rows={entry.get('rows')}", flush=True)
    if not db.is_configured():
        return
    try:
        await asyncio.wait_for(db.run(_log_read_sync, role, entry), timeout=4.0)
    except Exception as exc:  # the log is best-effort: a read is never blocked by it
        logger.error("exec_tools: could not log a read (%s)", type(exc).__name__)


def _result(block: Any, text: str, error: bool = False) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": block.id, "is_error": error, "content": plan_rules.redact_secrets(text)[:MAX_TEXT_CHARS * 2]}


def brand_path(path: Any) -> str:
    """A clean brand path the CMO may read, or PlanError. Secret files are refused by the shared read rule first."""
    clean = plan_rules.check_path_for_read(path)
    low = clean.lower()
    if any(low.startswith(p) for p in BRAND_REFUSED) or not any(low.startswith(p) for p in BRAND_PREFIXES):
        raise plan_rules.PlanError(f"{clean} is outside the brand and public files you may read")
    return clean


def _read_brand(path: Any) -> tuple[str, bool, str]:
    """(text, ok, normalised path). A binary public asset is reported as existing (it cannot be shown as text)."""
    from app.agents import tools

    try:
        clean = brand_path(path)
    except plan_rules.PlanError as exc:
        return f"ERROR: {exc}", False, norm(path) if isinstance(path, str) else ""
    text = tools.github_read_file(clean, "main")
    if text.startswith("ERROR:") and "binary file" in text and tools.github_file_status(clean) == "exists":
        return f"ASSET {clean} exists on main. It is a binary file (an image or video), so only its existence and name can be read.", True, norm(clean)
    ok = not text.startswith("ERROR:") and tools.CUT_OFF_MARK not in text  # a cut-off file has NOT been read in full
    return text, ok, norm(clean)


def _audit_text(rows: list[dict[str, Any]]) -> str:
    lines = []
    for r in rows:
        req = admin_store.parse_json(r.get("authorizationRequest"))
        req = req if isinstance(req, dict) else {}
        review = req.get("operational_review") if isinstance(req.get("operational_review"), dict) else {}
        execution = req.get("execution") if isinstance(req.get("execution"), dict) else {}
        lines.append(f"[{r['id']}] {admin_store.iso_utc(r.get('timestamp'))} | {r['agentId']} | decision: {r['humanAction']} by "
                     f"{r.get('humanActionBy') or 'nobody'} | risk: {req.get('risk_level') or 'n/a'} | route: {review.get('route') or 'none'} | "
                     f"pull request: {execution.get('status') or 'none'} | {str(req.get('action_summary') or 'no request')[:160]}")
    if not lines:
        return "The audit log has no matching entries."
    return f"{len(lines)} audit entr(ies), newest first (metadata only):\n" + "\n".join(lines)


def _audit_sync(agent_id: str | None, days: int, limit: int) -> list[dict[str, Any]]:
    sql = 'SELECT "id","agentId","timestamp","humanAction","humanActionBy","authorizationRequest" FROM "AgentAuditLog" WHERE "timestamp" >= %s'
    params: list[Any] = [db.naive_utc(datetime.now(timezone.utc) - timedelta(days=days))]
    if agent_id:
        sql += ' AND "agentId" = %s'
        params.append(agent_id)
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(sql + ' ORDER BY "timestamp" DESC, "id" DESC LIMIT %s', (*params, limit))
        return db.fetch_all(cur)


def _clamp(value: Any, default: int, high: int) -> int:
    return default if not isinstance(value, int) or isinstance(value, bool) else max(1, min(high, value))


async def run_read(role: str, name: str, args: dict[str, Any], outcome: ExecOutcome) -> tuple[str, bool, str, int | None]:
    """Run ONE read tool for ``role``. Returns (text, ok, target, rows). The role is code's, so scope cannot be argued with."""
    if name not in tool_names(role) or name not in READ_TOOLS:
        return f"ERROR: {name} is not one of your tools", False, "", None
    if name == "read_farm_records":
        kind = args.get("kind") if isinstance(args.get("kind"), str) else None
        text, rows = await asyncio.to_thread(farm_records.read_records, role, kind, args.get("parameter"), args.get("site"), args.get("days"))
        if rows is not None:
            for k in ([kind] if kind else list(farm_records.kinds_for(role))):
                outcome.read_targets.add(k)
            outcome.farm_rows += rows
            if role == "coo" and kind in (None, "inventory"):
                outcome.inventory_rows += rows
            if role == "coo" and kind in (None, "cost"):
                outcome.cost_rows += rows
        return text, rows is not None, kind or f"all_{role}_records", rows
    if name == "read_knowledge_base":
        from app.rag.retrieval import retrieve_context_with_sources

        query = args.get("query") if isinstance(args.get("query"), str) else ""
        result = await retrieve_context_with_sources(query[:500])
        outcome.kb_sources.extend(s.source_file for s in result.sources)
        empty = "The knowledge base returned nothing for that query (or is not configured). Say so; do not stretch it."
        return (result.context[:MAX_TEXT_CHARS] or empty), True, KB_TARGET, len(result.sources)
    if name == "read_audit_log":
        agent = args.get("agent_id") if args.get("agent_id") in ("cao", "coo", "cto", "cmo") else None
        if not db.is_configured():
            return "ERROR: the audit log is not available", False, AUDIT_TARGET, None
        rows = await db.run(_audit_sync, agent, _clamp(args.get("days"), 14, 90), _clamp(args.get("limit"), 15, MAX_AUDIT_ROWS))
        return _audit_text(rows), True, AUDIT_TARGET, len(rows)
    if name == "read_operations_summary":
        facts = await operations_log.collect(_clamp(args.get("days"), 7, operations_log.MAX_DAYS))
        if not facts:
            return "ERROR: the operations log could not be read", False, SUMMARY_TARGET, None
        return operations_log.summary_text(facts), True, SUMMARY_TARGET, facts["reviewed"]
    text, ok, target = await asyncio.to_thread(_read_brand, args.get("file_path"))
    if ok:
        outcome.texts[target] = text
    return text, ok, target, None


async def handle_block(role: str, block: Any, outcome: ExecOutcome) -> dict[str, Any] | None:
    """The tool_result for one tool_use block if it is one of this module's tools, else None (the caller handles its own). Never raises."""
    name = getattr(block, "name", None)
    args = getattr(block, "input", None)
    args = args if isinstance(args, dict) else {}
    try:
        if name in DIAGNOSTIC_TOOLS:
            from app.agents import exec_diagnostics

            if DIAGNOSTIC_TOOLS[name] != role:
                return _result(block, f"{name} is not one of your tools", True)
            if outcome.diagnosed >= MAX_DIAGNOSTICS_PER_TURN:
                return _result(block, "The diagnostic already ran in this answer. Use its report.", True)
            outcome.diagnosed += 1
            audit_id = args.get("audit_id") if isinstance(args.get("audit_id"), int) and not isinstance(args.get("audit_id"), bool) else None
            report = await exec_diagnostics.diagnose(role, audit_id)
            outcome.diagnostic = report
            return _result(block, exec_diagnostics.render_report(report), not report.get("ok"))
        if name in memory_tools.TOOL_NAMES and name in tool_names(role):
            text, ok, target, rows = await memory_tools.handle(role, name, args, outcome)  # logged by memory_tools; NOT a "read": it must not use the read budget
            return _result(block, text, not ok)
        if name == QUEUE_TOOL and name in tool_names(role):
            text, ok, target, rows = await knowledge_queue.handle(role, args, outcome)
            entry = {"tool": name, "target": target, "ok": ok, "rows": rows}
            await knowledge_queue._log({"action": target.split(":")[-1], "request_id": None if args.get("action") == "list" else (rows if isinstance(rows, int) else args.get("request_id")), "ok": ok,
                                        "note": f"requester={args.get('requester')}" if args.get("action") == "add" else ""})
            outcome.reads.append(entry)
            return _result(block, text, not ok)
        if name not in READ_TOOLS:
            return None
        if outcome.attempted() >= MAX_READS_PER_TURN:
            return _result(block, f"Read limit reached ({MAX_READS_PER_TURN} per answer). Work with what you have already read.", True)
        text, ok, target, rows = await run_read(role, name, args, outcome)
        entry = {"tool": name, "target": target, "ok": ok, "rows": rows}
        outcome.reads.append(entry)
        if ok and target:
            outcome.read_targets.add(target)
        await _log(role, entry)
        return _result(block, text, not ok)
    except Exception as exc:  # a read tool must never take the app down
        logger.error("exec_tools: %s failed (%s)", name, type(exc).__name__)
        if name in memory_tools.TOOL_NAMES:  # never the generic line for memory: say which tool, and the real error (scrubbed), so production can be diagnosed
            action = "save" if name == memory_tools.SAVE_TOOL else "search"
            return _result(block, memory_tools._failure(action, "internal", "Unexpected error while running the memory tool.", exc)[0], True)
        return _result(block, "ERROR: the read could not be completed", True)


async def tool_loop(client: Any, role: str, *, model: str, max_tokens: int, system: str, messages: list[dict],
                    extra_tools: list[dict] | None = None, extra_handler: Any = None, log_suffix: str = "") -> tuple[Any, ExecOutcome]:
    """
    ONE answer with the role's read-only tools on offer. ``extra_tools``/``extra_handler`` let the CMO keep its generate_image tool in the same
    loop: the handler gets a tool_use block and returns a tool_result or None. Bounded by MAX_TOOL_ROUNDS, then one last call with tools off.
    """
    outcome = ExecOutcome(role)
    if role not in SPECS:  # not one of the three roles: no tools at all, a plain call (same as a cross-check review)
        return await client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=list(messages)), outcome

    common = {"model": model, "max_tokens": max_tokens, "system": system, "tools": [*(extra_tools or []), *SPECS[role]]}
    log_tools_available(role, [t["name"] for t in extra_tools or []], log_suffix)
    convo = list(messages)
    response = await client.messages.create(messages=convo, **common)
    for _ in range(MAX_TOOL_ROUNDS):
        if response.stop_reason != "tool_use":
            return response, outcome
        results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            done = await handle_block(role, block, outcome)
            if done is None and extra_handler is not None:
                done = await extra_handler(block)
            results.append(done or _result(block, f"Unknown tool. Only {', '.join([*(t['name'] for t in extra_tools or []), *tool_names(role)])} exist.", True))
        if not results:
            return response, outcome
        convo = [*convo, {"role": "assistant", "content": response.content}, {"role": "user", "content": results}]
        response = await client.messages.create(messages=convo, **common)
    if response.stop_reason == "tool_use":
        pending = [{"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": "Tool limit reached. Write your final answer now."}
                   for b in response.content if getattr(b, "type", None) == "tool_use"]
        convo = [*convo, {"role": "assistant", "content": response.content}, {"role": "user", "content": pending}]
        response = await client.messages.create(messages=convo, tool_choice={"type": "none"}, **common)
    return response, outcome
