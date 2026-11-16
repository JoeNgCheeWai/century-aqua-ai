"""
The CAO's, COO's and CMO's CODE-WRITTEN SELF-CHECK, READ-BEFORE-WRITE rule and DIAGNOSTIC BLOCK (parity with the CTO's plan_checks / _self_check).

THE PRINCIPLE (the CTO's): code writes the line, and only after REAL checks pass. The model never claims verification itself. Each check below is a
pattern or fact check on the request text and on what the tools really did this turn; the declaration lists what passed and says plainly what did NOT
run. A check that fails drops the request (all or nothing, like the CTO's plan), with a finding and a code-written note at the top of the reply.

  CAO  dosages and units present; no invented statutory claim; species and life stage stated
  COO  costs and quantities present; no invented availability (only inventory it actually read counts)
  CMO  brand rules read; no unverifiable performance claim

What these are NOT: pattern checks on wording. They cannot tell that a dosage is RIGHT, that a statute exists, that stock is really in the store or
that a claim is true. The declaration says so. A person still decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agents import cmo_authorization, exec_requests
from app.agents.exec_tools import BRAND_RULE_FILES, KB_TARGET, ExecOutcome, norm


@dataclass
class Check:
    passed: bool = True
    ran: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    not_run: list[str] = field(default_factory=list)


_NUMBER_UNIT = re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s?(?:mg|g|kg|ml|l|litres?|liters?|ppm|ppt|mg/l|g/l|ml/l|g/kg|mg/kg|ml/kg|iu|%|cfu|m3|m\u00b3|ha|tonnes?|t|\u00b0c|c)(?:/[a-z0-9\u00b3]+)?\b")
_DOSE_WORDS = re.compile(r"(?i)\b(dos(?:e|es|age|ing)|apply|add|administer|treat(?:ment)?|feed(?:ing)? rate|stocking density|rate of)\b")
_STATUTE = re.compile(r"(?i)\b(?:the\s+)?(?:[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,4}\s+(?:Act|Regulations?|Ordinance|Directive|Code|Standard)\b(?:\s+\d{4})?|section\s+\d+[a-z]?|article\s+\d+|"
                      r"(?:law|statute|regulation)\s+(?:no\.?|number)\s*\d+|(?:it is|is)\s+(?:illegal|a legal requirement|legally required|mandatory by law))")
_SPECIES = re.compile(r"(?i)\b(tilapia|sea tilapia|shrimp|prawns?|vannamei|barramundi|seabass|sea bass|salmon|trout|catfish|carp|grouper|snapper|oyster|mussel|seaweed|species)\b")
_STAGE = re.compile(r"(?i)\b(fry|fingerlings?|juveniles?|larvae|larval|post-?larvae|pl\d*|nursery|grow-?out|broodstock|adults?|market[- ]size|hatchlings?|life stage|stage)\b")
_COST = re.compile(r"(?i)(?:[$\u20ac\u00a3]\s?\d|\b(?:usd|sgd|eur|gbp|myr)\s?\d|\b\d[\d,.]*\s?(?:usd|sgd|eur|gbp|myr|dollars)\b)")
_QUANTITY = re.compile(r"(?i)\b\d[\d,.]*\s?(?:units?|pcs|pieces|kg|g|tonnes?|t|litres?|liters?|l|ml|bags?|boxes|cartons?|pallets?|drums?|hours?|hrs|days?|weeks?|staff|people|workers|shifts?|trips?|orders?)\b")
_AVAILABILITY = re.compile(r"(?i)\b(in stock|available now|available immediately|on hand|we have \d|currently holds?|currently have|the store holds|stock level is|inventory (?:is|stands at)|"
                           r"ready to ship|lead time (?:is|of)|delivery (?:in|within) \d|supplier (?:has|can supply|confirmed))\b")
_PERFORMANCE = re.compile(r"(?i)(\b\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?\s?(?:x|times|fold)\b|\b(?:proven|clinically|scientifically|independently)\b|\b(?:guarantee[sd]?|outperforms?|best[- ]in[- ]class|market[- ]leading|"
                          r"fastest|highest yield|higher yield|better survival|zero mortality|cures?|prevents?)\b|\b(?:increase[sd]?|reduce[sd]?|improve[sd]?|boosts?|cuts?|saves?)\b[^.\n]{0,40}\d)")
_HEDGE = re.compile(r"(?i)\b(illustrative|assumption|assumed|example|placeholder|to be substantiated|substantiate|verify|unverified|\[detected by the system|per the (?:nus|trial)|according to)\b")


def _hits(pattern: re.Pattern[str], text: str) -> list[str]:
    return [m.group(0).strip() for m in pattern.finditer(text or "")]


def check_cao(request: dict[str, Any], outcome: ExecOutcome) -> Check:
    """Dosages and units present, no invented statutory claim, species and life stage stated."""
    text = exec_requests.request_text(request)
    c = Check(not_run=["a check that any dosage is correct for these animals", "a legal review of any regulation named"])
    c.ran.append("dosages and units present")
    if _DOSE_WORDS.search(text) and not _NUMBER_UNIT.search(text):
        c.failures.append("it talks about a dose, rate or treatment but gives no number with a unit")
    c.ran.append("no invented statutory claim")
    sourced = " ".join(outcome.kb_sources).lower()
    for claim in dict.fromkeys(_hits(_STATUTE, text)):
        if KB_TARGET not in outcome.read_targets or not any(w in sourced for w in re.findall(r"[a-z]{4,}", claim.lower())[:2]):
            c.failures.append(f"it names a legal requirement ('{claim[:60]}') that was not found in a knowledge-base document read this turn")
            break
    c.ran.append("species and life stage stated")
    if not _SPECIES.search(text):
        c.failures.append("no species is stated")
    if not _STAGE.search(text):
        c.failures.append("no life stage is stated")
    c.passed = not c.failures
    return c


def check_coo(request: dict[str, Any], outcome: ExecOutcome) -> Check:
    """Costs and quantities present, no invented availability."""
    text = exec_requests.request_text(request)
    c = Check(not_run=["a quote from any supplier", "a physical stock count"])
    c.ran.append("costs present")
    if not _COST.search(text):
        c.failures.append("no cost figure is stated")
    c.ran.append("quantities present")
    if not _QUANTITY.search(text):
        c.failures.append("no quantity is stated")
    c.ran.append("no invented availability")
    if _AVAILABILITY.search(text) and not (outcome.inventory_rows > 0):
        c.failures.append("it states that something is available or in stock, but no inventory record was read this turn to show it")
    c.passed = not c.failures
    return c


def check_cmo(request: dict[str, Any], outcome: ExecOutcome) -> Check:
    """Brand rules read, no unverifiable performance claim."""
    text = exec_requests.request_text(request)
    c = Check(not_run=["a legal or compliance review", "a check that any claim is true"])
    c.ran.append("brand rules read")
    if not any(norm(f) in outcome.texts for f in BRAND_RULE_FILES):
        c.failures.append("the brand rules were not read this turn (read_brand_file on the CMO prompt or PROJECT_CONTEXT.md)")
    c.ran.append("no unverifiable performance claim")
    claims = cmo_authorization.detect_claims(request.get("proposed_content"))
    unbacked = [x for x in claims if _PERFORMANCE.search(x) and not _HEDGE.search(x)]
    if unbacked:
        c.failures.append(f"{len(unbacked)} performance claim(s) with no source or label: '{unbacked[0][:80]}'")
    c.passed = not c.failures
    return c


CHECKS = {"cao": check_cao, "coo": check_coo, "cmo": check_cmo}


# ------------------------------------------------------------------------------------------------ read before write
def unread_targets(role: str, request: dict[str, Any], outcome: ExecOutcome, exists: Any = None) -> list[str]:
    """
    THE READ-BEFORE-WRITE RULE (the CTO's golden rule, for every role), decided in code. Returns what the request changes that was NOT read this turn.
      CAO / COO: the request must NAME what it rests on (``targets``: a record kind, knowledge_base, audit_log, operations_summary) and every one must
                 have been read successfully this turn. A request naming nothing is refused too: a recommendation about nothing it looked at is a guess.
      CMO:       every EXISTING file in its execution_plan (and every file path in ``targets``) must have been read in full. A brand-new file needs no
                 read. ``exists(path)`` says "exists" / "missing" / "unknown"; unknown fails closed (counts as unread).
    """
    targets = [norm(t) for t in request.get("targets") or []]
    if role in ("cao", "coo"):
        if not targets:
            return ["(it names nothing it read: list the records or documents the recommendation rests on in 'targets')"]
        return [t for t in targets if t not in outcome.read_targets]
    blocked = [t for t in targets if "/" in t and t not in outcome.texts]
    for item in (request.get("execution_plan") or {}).get("files_to_update", []):
        path = norm(item.get("path", ""))
        if path in outcome.texts or path in blocked:
            continue
        if (exists(item.get("path", "")) if exists else "unknown") != "missing":
            blocked.append(path)
    return blocked


def enforce_read_before_write(role: str, request: dict[str, Any] | None, outcome: ExecOutcome, exists: Any = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Drop the request (all or nothing) if it changes something unread. For the CMO a plan alone is dropped when the content still stands."""
    if request is None:
        return None, []
    blocked = unread_targets(role, request, outcome, exists)
    if not blocked:
        return request, []
    finding = f"authorization_request dropped: it changes or rests on something the {role.upper()} did not read first: " + "; ".join(blocked) + "."
    if role == "cmo" and request.get("execution_plan") and request.get("proposed_content"):
        revised = dict(request)
        revised["execution_plan"] = None
        return revised, [finding.replace("authorization_request dropped", "execution_plan dropped")]
    return None, [finding]


# ------------------------------------------------------------------------------------------------ the self-check
def declaration(role: str, check: Check, outcome: ExecOutcome) -> str:
    """The sentence the CEO sees, written by CODE after the checks passed. It states what was checked, what was read, and what was NOT run."""
    read = ", ".join(sorted({r["tool"] for r in outcome.reads if r["ok"]})) or "nothing"
    return (f"**Self-check ({role.upper()}):** I ran these checks on the request: " + " | ".join(check.ran) + f". Tools that read for it: {read}. "
            "I have not run " + " or ".join(check.not_run) + ". A person still decides.")


def self_check(role: str, request: dict[str, Any] | None, outcome: ExecOutcome) -> tuple[dict[str, Any] | None, list[str], str]:
    """Run the role's checks. Passes -> (request, [], declaration). Fails -> (None, [findings], ''): the request is dropped, never half-kept."""
    if request is None:
        return None, [], ""
    result = CHECKS[role](request, outcome)
    if result.passed:
        return request, [], declaration(role, result, outcome)
    return None, ["authorization_request dropped by the self-check: " + "; ".join(result.failures)], ""


def failed_note(role: str, findings: list[str]) -> str:
    """The truth, first, when a check dropped the request (the model may have written that it is ready). Code-written."""
    detail = findings[0].split(": ", 1)[-1][:600]
    return (f"**One important correction:** my own checks found a problem, so I did not prepare this for your approval. The problem: {detail}. "
            "Nothing is waiting for you to approve. Ask me again and I will fix it and re-check it first.")


# ------------------------------------------------------------------------------------------------ diagnostic block
def diagnostic_block(report: dict[str, Any]) -> str:
    """What the diagnostic really found, put FIRST in the first reply by code. Never an offer to investigate."""
    ran = "; ".join(report.get("steps", [])) or "no steps could run"
    if not report.get("ok"):
        return (f"### What I found (diagnostics already run)\nI ran the diagnostic myself just now, and it could not finish: {ran}. "
                f"**Why:** {report.get('cause')}\n\n_{report.get('limits', '')}_")
    return (f"### What I found (diagnostics already run)\nI ran the diagnostic myself just now: {ran}.\n"
            f"**Cause ({report.get('confidence', 'unknown')}):** {report.get('cause')}\n\n**The exact fix:** {report.get('fix')}\n\n_{report.get('limits', '')}_")


def reads_record(outcome: ExecOutcome) -> list[dict[str, Any]]:
    """The read log stored with the request (tool, target, ok, row count only: never the text that was read)."""
    return [dict(r) for r in outcome.reads]
