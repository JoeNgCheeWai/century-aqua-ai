"""
The CMO's Authorization Workflow (Phase 4 of the AI Executive Team).

THE RULE: the AI_CMO never publishes anything. It has no social, email, ad, website or CMS access
and no tools. It may DRAFT freely (posts, emails, scripts, storyboards, image prompts) and show the
draft to a human in its answer. When a request involves PUBLISHING content, SCHEDULING or SENDING it,
LAUNCHING a campaign, SPENDING on ads, or CHANGING public-facing material, it must output a structured
``authorization_request`` for a HUMAN to review and approve instead of acting.

    {"action_summary": str,             # what would go public or change, in one or two sentences
     "risk_level": "low" | "medium" | "high" | "critical",
     "impact": str,                     # audience, reach, reputation, legal and regulatory exposure
     "proposed_content": str | null,    # the exact copy / script / creative to publish, as TEXT for review
     "deployment_commands": [str],      # the exact steps a human would take to publish it, as TEXT
     "rollback_plan": str | null,       # how to take it down or correct it
     "claims_to_verify": [str],         # factual claims in the content that a human must substantiate first
     "status": "pending_human_approval",     # forced by code, never taken from the model
     "requires_human_approval": true,        # forced by code
     "executed": false}                      # forced by code

It is stored whole in AgentAuditLog.authorizationRequest (the column added for the CTO in Phase 3). The CMO's
request has DIFFERENT fields from the CTO's (content, not code), so a reader must use AgentAuditLog.agentId
to know which shape it is.

What is enforced HERE in code, not left to the model (the model is only instructions):
  * status, requires_human_approval and executed are always the three values above, so a request can
    never claim to be approved, published or executed.
  * A missing or unknown risk_level becomes "high" (fail safe, never "low").
  * The risk level has a FLOOR from the text. Anything public is at least "medium" (public content cannot be
    fully recalled: it is screenshotted, cached and quoted). Guarantee, cure or risk-free claims, or a
    scraped or purchased contact list, push it to "critical". Investor material, paid spend, mass sends,
    competitor comparisons, testimonials, personal data, "world's first", certification wording, any SND
    Probiotic mention, and website or production deployment push it to at least "high". The floor only
    RAISES a level, never lowers one.
  * Factual-claim markers found in the draft (percentages, multipliers, "proven", "world's first",
    "certified" and similar) are added to claims_to_verify by CODE, labelled as detected by the system, so a
    human sees them even if the model listed none.
  * A request with nothing to review (no summary, or neither content nor deployment steps) is not returned
    as a request; a finding says so. Lengths are capped.

What this does NOT do: it does not publish anything and it does not check that a claim is TRUE. The floor and
the claim detector are pattern checks and will miss risky wording that does not look risky. A human must read
the content and substantiate every claim before anything goes public (Clause 0).
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.cto_authorization import RISK_LEVELS, STATUS_PENDING

MAX_SUMMARY_CHARS = 600
MAX_IMPACT_CHARS = 2000
MAX_CONTENT_CHARS = 12000
MAX_COMMANDS = 20
MAX_COMMAND_CHARS = 500
MAX_ROLLBACK_CHARS = 1500
MAX_CLAIMS = 25
MAX_CLAIM_CHARS = 300
MAX_DETECTED_CLAIMS = 10

# Anything public is at least this risky, whatever the text says: it cannot be fully recalled.
BASE_FLOOR = "medium"

# Risk floors, matched case-insensitively against the content, the deployment steps and the claims.
_CRITICAL = [
    r"\bguarantee[sd]?\b.{0,40}\b(yield|survival|return|roi|profit|result|growth|cure|safe)",
    r"\b(cures?|cured|curing)\b",
    r"\b(100\s*%|zero)\s*(survival|mortality|effective|safe|disease|risk)",
    r"\brisk[- ]free\b|\bno[- ]risk\b|\bwithout (any )?risk\b",
    r"\beliminates?\s+(all\s+)?(disease|pathogens?|mortality)",
    r"\b(purchased|bought|scraped|harvested)\s+(email|contact|mailing)?\s*(list|database|emails?)",
    r"\bguaranteed\s+(returns?|profits?|income)",
]
_HIGH = [
    r"\b(investors?|investment|fundrais\w*|valuation|equity|securities|dividends?)\b",
    r"\bpaid\s+(ads?|campaign|promotion|social)\b|\b(ad|advertising|marketing|campaign|media)\s+(spend|budget)\b|\bsponsored\b|\bboost(ed|ing)?\s+(post|ad)\b",
    r"[$\u20ac\u00a3]\s?\d|\b(usd|sgd|eur|gbp)\s?\d|\b\d[\d,.]*\s?(usd|sgd|eur|gbp)\b",
    r"\b(mailing list|newsletter|bulk (e-?mail|send)|send to all|all (users|subscribers|customers|contacts)|cold (e-?mail|outreach))\b",
    r"\b(better|faster|cheaper|safer|superior|outperforms?|beats?)\b.{0,30}\b(than|vs\.?|versus)\b|\bvs\.?\b|\bversus\b|\bcompared (to|with)\b",
    r"\b(testimonials?|endorse(ment|d|s)?|case stud(y|ies)|customer logos?|named customers?)\b",
    r"\b(e-?mail address(es)?|phone numbers?|personal data|contact (list|details)|personally identifiable)\b",
    r"world['\u2019]?s (1st|first)|\b#\s?1\b|\bnumber (one|1)\b|\bmarket[- ]leading\b|\bbest[- ]in[- ]class\b",
    r"\b(certified|accredited|approved by|asc|bap|globalg\.?a\.?p|organic|eco-?certified)\b",
    r"\bsnd\b|\bsea tilapia strain\b",
    r"\b(vercel|git push|git commit|deploy(ment)?|production|go live|going live|homepage|landing page|website copy)\b",
    r"\b(prevents?|treats?|treatment of|heals?|vaccin\w+)\b.{0,30}\b(disease|infection|pathogen|vibrio|streptococc\w*)",
]
_MEDIUM = [
    r"\b(publish\w*|post(ing)?\b|schedul\w+|send(ing)?\b|launch\w*|go live|tweet|share)\b",
    r"\b(linkedin|facebook|instagram|tiktok|youtube|x\.com|twitter|whatsapp|wechat|press release|webinar|newsletter)\b",
    r"\b(delete|remove|take down|unpublish|replace|update|edit)\b.{0,40}\b(post|page|copy|banner|video|article|deck)\b",
]
_FLOORS = (("critical", _CRITICAL), ("high", _HIGH), ("medium", _MEDIUM))
_ORDER = {level: index for index, level in enumerate(RISK_LEVELS)}

# Factual-claim markers the CODE looks for in the draft. Each hit is reported for a human to substantiate.
_CLAIM_MARKERS = [
    r"\d+(?:\.\d+)?\s?%",
    r"\b\d+(?:\.\d+)?\s?(?:x|times|fold)\b",
    r"\bproven\b|\b(?:clinically|scientifically|independently)\s+(?:tested|validated|verified|shown|proven)\b",
    r"\b(?:guarantee[sd]?|certified|accredited|approved|patented|peer[- ]reviewed)\b",
    r"world['\u2019]?s (?:1st|first)|\b#\s?1\b|\bnumber (?:one|1)\b|\bmarket[- ]leading\b|\bbest[- ]in[- ]class\b",
    r"\b(?:increase[sd]?|reduce[sd]?|cut|improve[sd]?|boost(?:s|ed)?|save[sd]?)\b[^.\n]{0,40}\b\d",
]


def risk_floor(content: str | None, steps: list[str], claims: list[str]) -> str:
    """The lowest risk level the text allows. Anything public is at least BASE_FLOOR ('medium')."""
    haystack = "\n".join([content or "", *steps, *claims])
    for level, patterns in _FLOORS:
        for pattern in patterns:
            if re.search(pattern, haystack, re.IGNORECASE | re.MULTILINE):
                return level if _ORDER[level] >= _ORDER[BASE_FLOOR] else BASE_FLOOR
    return BASE_FLOOR


def detect_claims(content: str | None) -> list[str]:
    """
    Sentences in the draft that contain a factual-claim marker, for a human to substantiate. Pattern based:
    it finds wording that LOOKS like a claim and cannot tell whether it is true or whether it was missed.
    """
    if not content:
        return []
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", content):
        text = sentence.strip()
        if not text:
            continue
        if any(re.search(marker, text, re.IGNORECASE) for marker in _CLAIM_MARKERS):
            clipped = text if len(text) <= MAX_CLAIM_CHARS else text[: MAX_CLAIM_CHARS - 1].rstrip() + "…"
            if clipped not in found:
                found.append(clipped)
        if len(found) >= MAX_DETECTED_CLAIMS:
            break
    return found



def _text(value: Any, limit: int) -> str:
    """A clean string from a string, or leniently from a list or dict the model used instead."""
    if isinstance(value, str):
        out = value
    elif isinstance(value, (list, tuple)):
        out = "\n".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        out = "; ".join(f"{key}: {item}" for key, item in value.items() if str(item).strip())
    else:
        return ""
    out = out.strip()
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def _string_list(value: Any, *, max_items: int, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [line for line in value.splitlines() if line.strip()]
    if not isinstance(value, (list, tuple)):
        return []
    cleaned = [_text(item, limit) for item in value if isinstance(item, (str, int, float))]
    return [item for item in cleaned if item][:max_items]


def _risk(value: Any) -> str | None:
    """The model's risk level if it is a known one, else None (the caller then fails safe to 'high')."""
    if isinstance(value, str) and value.strip().lower() in RISK_LEVELS:
        return value.strip().lower()
    return None


def normalize_authorization_request(raw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """
    Validate the model's ``authorization_request`` for a marketing action. Returns (request or None, findings).

    None means "no request": the model sent null (a drafting or advice answer with nothing to publish), or
    sent something with nothing to review. ``findings`` explains the second case. Never raises.
    """
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["authorization_request was not an object; dropped"]

    summary = _text(raw.get("action_summary"), MAX_SUMMARY_CHARS)
    impact = _text(raw.get("impact"), MAX_IMPACT_CHARS)
    content = _text(raw.get("proposed_content"), MAX_CONTENT_CHARS) or None
    steps = _string_list(raw.get("deployment_commands"), max_items=MAX_COMMANDS, limit=MAX_COMMAND_CHARS)
    rollback = _text(raw.get("rollback_plan"), MAX_ROLLBACK_CHARS) or None
    claims = _string_list(raw.get("claims_to_verify"), max_items=MAX_CLAIMS, limit=MAX_CLAIM_CHARS)

    if not summary:
        return None, ["authorization_request had no action_summary; dropped"]
    if not content and not steps:
        return None, ["authorization_request had neither proposed_content nor deployment_commands; dropped"]

    findings: list[str] = []
    declared = _risk(raw.get("risk_level"))
    if declared is None:
        declared = "high"
        findings.append("authorization_request risk_level missing or unknown; set to high")
    floor = risk_floor(content, steps, claims)
    level = declared if _ORDER[declared] >= _ORDER[floor] else floor
    if level != declared:
        findings.append(f"authorization_request risk_level raised from {declared} to {level} (the text matches a {floor}-risk pattern)")
    if not impact:
        impact = "Not stated by the CMO. Treat the impact as unknown and review before approving."
        findings.append("authorization_request had no impact; placeholder added")

    # Claims the CODE detected in the draft that the model did not already list, labelled as such.
    listed = " ".join(claims).lower()
    detected = [c for c in detect_claims(content) if c.lower() not in listed]
    if detected:
        claims = (claims + [f"[detected by the system, substantiate before publishing] {c}" for c in detected])[:MAX_CLAIMS]
        findings.append(f"{len(detected)} factual claim(s) detected in the draft and added to claims_to_verify")

    request = {
        "action_summary": summary,
        "risk_level": level,
        "impact": impact,
        "proposed_content": content,
        "deployment_commands": steps,
        "rollback_plan": rollback,
        "claims_to_verify": claims,
        # Forced by code. Nothing the model writes can mark a request approved, published or executed.
        "status": STATUS_PENDING,
        "requires_human_approval": True,
        "executed": False,
    }
    return request, findings


def audit_summary(request: dict[str, Any] | None) -> str:
    """
    A short text block describing the request, appended to the reply that is stored in AgentAuditLog.
    The full structured request lives in AgentAuditLog.authorizationRequest (JSON); this block keeps the
    stored response text readable on its own: that publishing was asked for, at what risk, and that it
    awaits approval with nothing published. It omits the content itself, which is only in the JSON column.
    """
    if not request:
        return ""
    lines = [
        "AUTHORIZATION REQUEST (pending human approval; nothing has been published or executed)",
        f"- Action: {request['action_summary']}",
        f"- Risk level: {request['risk_level']}",
        f"- Impact: {request['impact']}",
    ]
    if request.get("claims_to_verify"):
        lines.append(f"- Claims a human must substantiate before publishing: {len(request['claims_to_verify'])}")
    if request.get("deployment_commands"):
        lines.append("- Deployment steps (for a human to review and carry out, NOT carried out by the CMO):")
        lines.extend(f"    {step}" for step in request["deployment_commands"])
    if request.get("proposed_content"):
        lines.append("- Proposed content: included in the structured authorization_request.")
    if request.get("rollback_plan"):
        lines.append(f"- Rollback plan: {request['rollback_plan']}")
    return "\n".join(lines)

