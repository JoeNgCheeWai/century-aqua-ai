"""
The CTO's SELF-DIAGNOSIS tool: ``diagnose_boardroom_session`` (READ-ONLY).

WHY. When a Boardroom session fails, the CTO should not answer "I can investigate if you'd like". It should investigate, in the same
answer. The CTO cannot run scripts, so this tool does, on the server, the same job as scripts/try_boardroom_turn.py:

  1. READ the session's recorded facts (status, the reason it stopped, who sat, how many replies were stored per round).
  2. SCAN the council prompts for wording the real model has refused (the refusal outage), and read the live limits.
  3. PROBE the real model: one round-1 turn per seat, exactly as a session sends it, and record for each: did the call error, the stop
     reason (end_turn, refusal, max_tokens), tokens, seconds, and whether the reply was a usable proposal.
  4. DERIVE the cause and the exact fix IN CODE from those facts. The model only reads and explains this report; it does not decide it.

IT ONLY READS AND PROBES. It changes nothing: no database write, no file, no setting. The probe spends a few real model calls (one per
seat, parallel, about 8 seconds each), so it is rate-limited (PROBES_PER_HOUR per server) and bounded (at most MAX_SEATS calls). Reply text
from the model is never put in the report, only counts and the stop reason, so it cannot carry an instruction back to the CTO.

What it does NOT do: it cannot see Render's logs, so it cannot tell what happened at the moment of a past failure beyond what the session
row recorded. It says so. A healthy probe means the prompt and model work NOW, not that the earlier failure had no other cause.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

from app.agents.plan_checks import REFUSED_WORDING
from app.governance import council_store, db

logger = logging.getLogger(__name__)

TOOL_NAME = "diagnose_boardroom_session"
TOOL_SPEC: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Run the Boardroom diagnostic and get back a report. Use it IMMEDIATELY, in the same answer, whenever the CEO reports that a Boardroom "
        "session failed or came back empty (for example 'only 0 usable proposals'), before you write your reply. It reads the session's record, "
        "scans the council prompts for wording the real model refuses, probes the real model once per seat, and derives the cause and the exact fix. "
        "It changes nothing. Leave session_id out to diagnose the most recent session that did not resolve."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The Boardroom session's id (a UUID), if the CEO gave one. Leave it out for the latest unresolved session."},
            "run_probe": {"type": "boolean", "description": "Probe the real model once per seat. Default true. Set false only to read the record without spending model calls."},
        },
    },
}
MAX_PER_ANSWER = 1
MAX_SEATS = 4
PROBES_PER_HOUR = 3
PROBE_QUESTION = "Should we launch the new customer portal this quarter? Pilot budget cap USD 150,000; initial market Vietnam."
_probe_times: list[float] = []

# Plain-language causes, matched against the reason a session stored (council.why_unusable and the halt reasons).
_REASONS = (
    ("refused", re.compile(r"(\d+) reply\(ies\) refused")),
    ("cut_off", re.compile(r"(\d+) reply\(ies\) cut off")),
    ("errored", re.compile(r"(\d+) call\(s\) failed or timed out")),
    ("not_json", re.compile(r"(\d+) reply\(ies\) were not valid JSON")),
)


def parse_reason(reason: str | None) -> dict[str, int]:
    """Counts of what went wrong, read from the reason the session stored. Empty when the reason has none of them."""
    found: dict[str, int] = {}
    for key, pattern in _REASONS:
        match = pattern.search(reason or "")
        if match:
            found[key] = int(match.group(1))
    return found


def rate_limited(now: float | None = None) -> bool:
    """True when this server already ran PROBES_PER_HOUR probes in the last hour (and records this one if not)."""
    moment = time.monotonic() if now is None else now
    _probe_times[:] = [t for t in _probe_times if moment - t < 3600]
    if len(_probe_times) >= PROBES_PER_HOUR:
        return True
    _probe_times.append(moment)
    return False


def scan_prompts() -> list[str]:
    """Every council prompt text that contains wording the real model has refused (the exact phrase and where). [] means none found."""
    from app.agents import council
    from app.prompts import council_prompts as prompts

    texts = {name: getattr(prompts, name) for name in ("TURN_CONTRACT", "CROSS_RESPONSE_CONTRACT", "COHESION_RULES", "DELIBERATION_CARD",
                                                          "MODERATION_CONTRACT", "SYNTHESIS_CONTRACT", "RED_TEAM_CONTRACT")}
    texts.update({f"system prompt of {agent}": council.system_prompt_for(agent) for agent in council.ORDER})
    hits = []
    for name, text in texts.items():
        for phrase in REFUSED_WORDING:
            if phrase in text.lower():
                hits.append(f"{name} contains '{phrase}'")
    return hits


def _read_session_sync(session_id: str | None) -> dict[str, Any] | None:
    columns = '"id","status","escalationReason","participatingAgents","modelCalls","totalTokens","startedAt"'
    with db.transaction() as conn:
        cur = conn.cursor()
        if session_id:
            cur.execute(f'SELECT {columns} FROM "CouncilSessionLog" WHERE "id"=%s', (session_id,))
        else:
            cur.execute(f'SELECT {columns} FROM "CouncilSessionLog" WHERE "status" <> CAST(%s AS "CouncilStatus") ORDER BY "startedAt" DESC LIMIT 1',
                        ("RESOLVED",))
        rows = db.fetch_all(cur)
        if not rows:
            return None
        row = rows[0]
        cur.execute('SELECT "round", COUNT(*) AS n FROM "CouncilTurnLog" WHERE "sessionId"=%s GROUP BY "round" ORDER BY "round"', (row["id"],))
        row["turns_by_round"] = {int(r["round"]): int(r["n"]) for r in db.fetch_all(cur)}
        return row


async def read_session(session_id: str | None) -> tuple[dict[str, Any] | None, str]:
    """(the session's recorded facts or None, a note about why there are none). Never raises."""
    if session_id is not None:
        try:
            uuid.UUID(session_id)
        except (ValueError, AttributeError, TypeError):
            return None, "that is not a valid session id"
    if not db.is_configured():
        return None, "the governance database is not configured, so no session record can be read"
    try:
        row = await db.run(_read_session_sync, session_id)
    except Exception as exc:
        logger.error("diagnostics: could not read the session (%s)", type(exc).__name__)
        return None, f"the session record could not be read ({type(exc).__name__})"
    if row is None:
        return None, "no session with that id" if session_id else "there is no unresolved Boardroom session on record"
    return {
        "session_id": row["id"], "status": str(row["status"]), "reason": row.get("escalationReason"),
        "seats": list(row.get("participatingAgents") or []), "model_calls": row.get("modelCalls"), "total_tokens": row.get("totalTokens"),
        "turns_by_round": row.get("turns_by_round", {}),
    }, ""


async def probe_seat(agent_id: str, seats: list[str]) -> dict[str, Any]:
    """One real round-1 turn for one seat, as a session sends it. Returns counts and the stop reason only, never the model's text."""
    from app.agents import council
    from app.prompts import council_prompts as prompts

    session = council.Session("diagnostic-probe", PROBE_QUESTION, seats, "diagnostic")
    user = f"{prompts.TURN_CONTRACT}\n\n{council._table(session)}THE CEO'S QUESTION:\n{council._wrap(PROBE_QUESTION)}\n\nGive your proposal."
    started = time.perf_counter()
    try:
        reply = await council.call_model(council.system_prompt_for(agent_id), user, max_tokens=council.TURN_MAX_TOKENS,
                                         budget=council.Budget(), keep=council.RESERVED_CALLS, timeout=council.TURN_TIMEOUT_SECONDS)
    except Exception as exc:
        return {"seat": agent_id, "error": type(exc).__name__, "stop_reason": None, "usable": False, "seconds": round(time.perf_counter() - started, 1)}
    turn = council.normalize_turn(agent_id, reply.text)
    return {"seat": agent_id, "error": None, "stop_reason": reply.stop_reason, "output_tokens": reply.output_tokens, "truncated": reply.truncated,
            "usable": bool(turn["valid"]), "reasoning_written": bool(turn["reasoning"]), "seconds": round(time.perf_counter() - started, 1)}


async def probe(seats: list[str]) -> list[dict[str, Any]]:
    """Probe each seat (at most MAX_SEATS), in parallel. Never raises."""
    from app.agents import council

    seated = [s for s in council.ORDER if s in seats][:MAX_SEATS] or ["cao", "coo"]
    results = await asyncio.gather(*(probe_seat(s, seated) for s in seated), return_exceptions=True)
    return [r if isinstance(r, dict) else {"seat": s, "error": type(r).__name__, "stop_reason": None, "usable": False, "seconds": None}
            for s, r in zip(seated, results)]


def derive_cause(session: dict[str, Any] | None, scan: list[str], probes: list[dict[str, Any]] | None) -> dict[str, Any]:
    """
    The cause and the exact fix, decided HERE from the facts (never by the model). Returns {"cause", "fix", "confidence", "facts"} where
    confidence is "confirmed" (the prompt scan or a probe reproduced it), "likely" (the stored record points to it but nothing reproduced
    it) or "unknown".
    """
    counts = parse_reason(session.get("reason") if session else None)
    refused_now = [p["seat"] for p in (probes or []) if p.get("stop_reason") == "refusal"]
    cut_now = [p["seat"] for p in (probes or []) if p.get("stop_reason") == "max_tokens" or p.get("truncated")]
    errored_now = [p["seat"] for p in (probes or []) if p.get("error")]
    unusable_now = [p["seat"] for p in (probes or []) if not p.get("usable") and not p.get("error") and p.get("stop_reason") not in ("refusal", "max_tokens")]
    facts = {"recorded_counts": counts, "refused_now": refused_now, "cut_off_now": cut_now, "errored_now": errored_now, "unusable_now": unusable_now,
             "prompt_scan": scan}
    if scan or refused_now or counts.get("refused"):
        where = "; ".join(scan) if scan else ("the probe was refused with the current prompt" if refused_now else "the session recorded refused replies")
        confirmed = bool(scan or refused_now)  # a recorded count alone is only "likely": nothing reproduced it just now
        fix = (f"Remove the wording the model refuses ({where}) from backend/app/prompts/council_prompts.py: ask for the reasoning as an analysis, never "
               "as something hidden. Then run scripts/try_boardroom_turn.py to confirm every seat answers.")
        if not confirmed:
            fix = ("The session recorded refused replies, but the prompts contain none of the refused wording and nothing reproduced it just now, so the cause "
                   "may already be gone. Run the session again; if it is refused again, run scripts/try_boardroom_turn.py and send me the new session id.")
        return {"cause": "The model is refusing the Boardroom prompt (a refusal looks like an empty reply, so the session reports no usable proposals).",
                "fix": fix, "confidence": "confirmed" if confirmed else "likely", "facts": facts}
    if cut_now or counts.get("cut_off"):
        return {"cause": "Replies are being cut off at the token limit before the JSON is complete.",
                "fix": "Raise COUNCIL_TURN_MAX_TOKENS (and COUNCIL_CROSS_MAX_TOKENS) on Render, or shorten the reasoning the prompt asks for.",
                "confidence": "confirmed" if cut_now else "likely", "facts": facts}
    if errored_now or counts.get("errored"):
        return {"cause": "Calls to the model are failing or timing out.",
                "fix": "Check ANTHROPIC_API_KEY and the Anthropic status page, then COUNCIL_TURN_TIMEOUT_SECONDS on Render (the default is 75 seconds).",
                "confidence": "confirmed" if errored_now else "likely", "facts": facts}
    if unusable_now or counts.get("not_json"):
        return {"cause": "The model answers but not in the JSON shape the Boardroom needs.",
                "fix": "Compare a live reply with TURN_CONTRACT using scripts/try_boardroom_turn.py and tighten the contract's example.",
                "confidence": "confirmed" if unusable_now else "likely", "facts": facts}
    if probes and all(p.get("usable") for p in probes):
        return {"cause": "I could not reproduce the failure: every seat answered correctly just now, and no refused wording is in the prompts.",
                "fix": "Run the session again. If it fails again, send me the new session id. A past failure may have been a temporary model or network "
                       "problem, and I cannot see Render's logs to confirm.",
                "confidence": "unknown", "facts": facts}
    return {"cause": "I could not determine the cause from the record alone.", "fix": "Run the probe (run_probe) and send me the session id.",
            "confidence": "unknown", "facts": facts}


async def diagnose(session_id: str | None = None, run_probe: bool = True) -> dict[str, Any]:
    """The whole diagnostic. NEVER RAISES. Returns a report dict (see derive_cause); 'steps' lists what really ran, in order."""
    steps: list[str] = []
    try:
        session, note = await read_session(session_id)
        steps.append("read the session record" if session else f"could not read a session record ({note})")
        scan = scan_prompts()
        steps.append("scanned the council prompts for wording the real model has refused: " + (f"FOUND {len(scan)}" if scan else "none found"))
        probes: list[dict[str, Any]] | None = None
        if run_probe:
            if rate_limited():
                steps.append(f"did not probe the model: already probed {PROBES_PER_HOUR} times this hour (a limit that protects the model budget)")
            else:
                probes = await probe((session or {}).get("seats") or [])
                steps.append(f"probed the real model once per seat ({len(probes)} call(s)), exactly as a session sends round 1")
        else:
            steps.append("did not probe the model (run_probe was false)")
        report = derive_cause(session, scan, probes)
        return {"ok": True, "steps": steps, "session": session, "session_note": note, "probes": probes, **report,
                "limits": "I cannot see Render's logs, so I only know what the session recorded and what the probe shows right now."}
    except Exception as exc:  # a diagnostic must never take an answer down
        logger.error("diagnostics: failed (%s)", type(exc).__name__)
        return {"ok": False, "steps": steps, "cause": "The diagnostic itself failed.", "fix": "Run scripts/try_boardroom_session.py from the backend folder.",
                "confidence": "unknown", "error": type(exc).__name__}


def _clean(text: str) -> str:
    return text.replace("<<<", "< < <").replace(">>>", "> > >")


def render_report(report: dict[str, Any]) -> str:
    """The report as plain text for the model (counts, stop reasons and the derived cause only; never model replies)."""
    lines = ["DIAGNOSTIC REPORT (produced by code that just ran; these are facts, not instructions)", "What was run: " + "; ".join(report.get("steps", []))]
    session = report.get("session")
    if session:
        lines.append(f"Session {session['session_id']}: status {session['status']}; seats {session['seats']}; calls {session['model_calls']}; "
                     f"replies stored per round {session['turns_by_round']}; reason it stopped: {session.get('reason')}")
    for p in report.get("probes") or []:
        lines.append(f"Probe {p['seat']}: error={p.get('error')} stop_reason={p.get('stop_reason')} usable={p.get('usable')} "
                     f"tokens={p.get('output_tokens')} seconds={p.get('seconds')}")
    lines += [f"CAUSE ({report.get('confidence')}): {report.get('cause')}", f"EXACT FIX: {report.get('fix')}", f"LIMITS: {report.get('limits', '')}"]
    return _clean("\n".join(lines))
