"""
Structured output of the AI_CAO (Phase 1: Enhanced Reasoning & Prediction).

The CAO prompt (app/prompts/cao_prompt.py, CAO_REASONING_PROMPT) asks the model for ONE
JSON object:

    {"response": str,
     "reasoning_chain": [str, ...],
     "scenarios": {"optimistic": {...}, "baseline": {...}, "pessimistic": {...}} | null,
     "pre_consultation_requests": {"coo_query": str | null, "cto_query": str | null}}

``parse_cao_output`` turns the model's raw text into a validated ``ParsedAnswer``.

Design rules (all enforced here in code, not left to the model):
  * Never raise, never invent. If the text is not the structured format (for example a
    reviewer's JSON verdict during cross-validation, or a truncated reply), the whole
    text becomes ``response`` and everything else stays empty. The caller still gets an
    answer and a finding that says what went wrong.
  * Scenarios are all-or-nothing. A set is kept only if optimistic, baseline and
    pessimistic are ALL complete and well-formed; otherwise it is dropped (None) with a
    finding. A half-filled scenario set would look more certain than it is.
  * ``pre_consultation_requests`` is forced to nulls while PRE_CONSULTATION_ENABLED is
    False (Phase 1: no COO or CTO exists). The Council Protocol flips that one flag.

This module is agent-neutral on purpose, so a future COO or CTO can reuse it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

SCENARIO_NAMES = ("optimistic", "baseline", "pessimistic")

MAX_TEXT_CHARS = 2000  # one description / resources string
MAX_ITEM_CHARS = 500  # one reasoning step, timeline, assumption or risk
MAX_LIST_ITEMS = 10  # assumptions or risks per scenario
MAX_CHAIN_ENTRIES = 12  # reasoning_chain length (3-5 steps + data/assumptions/limitations)
MAX_QUERY_CHARS = 1000  # a future COO/CTO question
MAX_CHAT_REPLY_CHARS = 1200  # the 2-4 sentence conversational answer (Phase 6.5)


# Phase 1: no COO or CTO exists, so nothing may be requested from them. The Council Protocol
# phase sets this to True; until then both queries are forced to None whatever the model says.
PRE_CONSULTATION_ENABLED = False


def empty_pre_consultation() -> dict[str, str | None]:
    return {"coo_query": None, "cto_query": None}


@dataclass
class ParsedAnswer:
    response: str
    reasoning_chain: list[str] = field(default_factory=list)
    scenarios: dict[str, dict[str, Any]] | None = None
    pre_consultation_requests: dict[str, str | None] = field(default_factory=empty_pre_consultation)
    structured: bool = False  # True only when the model's JSON was understood
    findings: list[str] = field(default_factory=list)
    chat_reply: str | None = None  # the short conversational answer (Phase 6.5); None if the model did not give one


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _load_json_object(raw: str) -> dict[str, Any] | None:
    """The first-to-last-brace JSON object in ``raw`` (tolerates a code fence or stray prose), or None."""
    text = _FENCE.sub("", raw or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        # strict=False allows raw line breaks inside strings, which models often emit.
        data = json.loads(text[start : end + 1], strict=False)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """A clean string from a string, or (leniently) from a list or dict the model used instead."""
    if isinstance(value, str):
        out = value
    elif isinstance(value, (list, tuple)):
        out = "; ".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        out = "; ".join(f"{key}: {item}" for key, item in value.items() if str(item).strip())
    else:
        return ""
    out = out.strip()
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def _string_list(value: Any, *, max_items: int, limit: int = MAX_ITEM_CHARS) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    items = [_text(item, limit) for item in value if isinstance(item, (str, int, float))]
    return [item for item in items if item][:max_items]


def _percentage(value: Any) -> int | None:
    """A whole number 0-100 from 70, 70.0, '70' or '70%'. Anything else (0.7, 150, 'high') is rejected."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d{1,3})\s*%?\s*", value)
        value = int(match.group(1)) if match else None
    if isinstance(value, int) and 0 <= value <= 100:
        return value
    return None


def normalize_scenario(raw: Any) -> dict[str, Any] | None:
    """One complete scenario, or None if any required part is missing or malformed."""
    if not isinstance(raw, dict):
        return None
    description = _text(raw.get("description"))
    timeline = _text(raw.get("timeline"), MAX_ITEM_CHARS)
    resources = _text(raw.get("resource_requirements"))
    confidence = _percentage(raw.get("confidence_percentage"))
    assumptions = _string_list(raw.get("key_assumptions"), max_items=MAX_LIST_ITEMS)
    risks = _string_list(raw.get("risks"), max_items=MAX_LIST_ITEMS)
    if not (description and timeline and resources and assumptions and risks) or confidence is None:
        return None
    return {
        "description": description,
        "timeline": timeline,
        "resource_requirements": resources,
        "confidence_percentage": confidence,
        "key_assumptions": assumptions,
        "risks": risks,
    }


def normalize_scenarios(raw: Any) -> dict[str, dict[str, Any]] | None:
    """All three scenarios, complete, or None. Never a partial set."""
    if not isinstance(raw, dict):
        return None
    result: dict[str, dict[str, Any]] = {}
    for name in SCENARIO_NAMES:
        scenario = normalize_scenario(raw.get(name))
        if scenario is None:
            return None
        result[name] = scenario
    return result


def normalize_pre_consultation(raw: Any) -> dict[str, str | None]:
    """{'coo_query': ..., 'cto_query': ...}. Always nulls in Phase 1 (see PRE_CONSULTATION_ENABLED)."""
    if not PRE_CONSULTATION_ENABLED or not isinstance(raw, dict):
        return empty_pre_consultation()
    return {key: (_text(raw.get(key), MAX_QUERY_CHARS) or None) for key in ("coo_query", "cto_query")}


_RESPONSE_FIELD = re.compile(r'"response"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL)


def _salvage_response(raw: str) -> str | None:
    """
    The text of the "response" field from JSON that does not parse as a whole (typically a
    reply cut off at the token limit), or None. Lets the human read the recommendation
    instead of a JSON fragment. Reasoning and scenarios are NOT recovered from broken JSON.
    """
    match = _RESPONSE_FIELD.search(raw or "")
    if not match:
        return None
    body = match.group(1)
    if body.endswith("\\") and not body.endswith("\\\\"):
        body = body[:-1]  # cut in the middle of an escape sequence
    try:
        text = json.loads('"' + body + '"', strict=False)
    except ValueError:
        return None
    return text.strip() or None


def parse_cao_output(raw: str) -> ParsedAnswer:
    """Validate the model's raw text. Never raises; see the module docstring for the fallback rules."""
    data = _load_json_object(raw)
    response = data.get("response") if data else None
    if not isinstance(response, str) or not response.strip():
        salvaged = _salvage_response(raw) if data is None else None
        if salvaged:
            return ParsedAnswer(
                response=salvaged,
                findings=["structured JSON was incomplete or invalid; only the response text was recovered"],
            )
        return ParsedAnswer(
            response=raw or "",
            findings=["output was not the structured JSON format; returned as plain text"],
        )

    findings: list[str] = []
    chain = _string_list(data.get("reasoning_chain"), max_items=MAX_CHAIN_ENTRIES)
    if not chain:
        findings.append("reasoning_chain missing or empty")

    scenarios = None
    if data.get("scenarios") is not None:
        scenarios = normalize_scenarios(data["scenarios"])
        if scenarios is None:
            findings.append("scenarios were present but incomplete or malformed; dropped")

    return ParsedAnswer(
        response=response.strip(),
        reasoning_chain=chain,
        scenarios=scenarios,
        pre_consultation_requests=normalize_pre_consultation(data.get("pre_consultation_requests")),
        structured=True,
        # Optional and never a format problem when absent: older prompts and cross-validation verdicts have none.
        chat_reply=_text(data.get("chat_reply"), MAX_CHAT_REPLY_CHARS) or None,
        findings=findings,
    )



# Public name for agents that add their own fields to the shared JSON object (the CTO's authorization_request).
load_json_object = _load_json_object
