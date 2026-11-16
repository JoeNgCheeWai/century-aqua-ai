"""
The CAO's and COO's RECOMMENDATION REQUEST: what makes their advice approvable.

Until now only the CTO and CMO could store an ``authorization_request``, so the CAO's and COO's rows in ApprovedStrategies stayed empty (the learning
loop only saves what a person approved, and there was nothing for a person to approve). This module gives them the same object, in the same
AgentAuditLog.authorizationRequest column, decided through the SAME endpoint (authorization.apply_decision) with the SAME audit and strategy save.

WHAT APPROVING A RECOMMENDATION DOES: it records a decision and remembers the strategy. It runs nothing and opens no pull request: there is no
execution_plan, and the CAO and COO have no GitHub access. ``executed`` is forced False.

WHO MAY DECIDE, DECIDED BY CODE (never the model): sensitivity() reads the whole request for animals, chemicals, statutory reporting and money at or above a
threshold. A request that hits any of them is the CEO's, ALWAYS: it can never be approved by the COO's delegation (exec_approval enforces it, and
authorization.apply_decision checks it again).
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.agents.cto_authorization import RISK_LEVELS, STATUS_PENDING
from app.agents.execution_plan import higher_risk, redact_secrets
from app.agents.plain_summary import normalize_plain_summary

KIND = "recommendation"
ROLES = ("cao", "coo")
MAX_SUMMARY, MAX_IMPACT, MAX_TEXT, MAX_STEPS, MAX_ITEM, MAX_TARGETS = 600, 2000, 12000, 20, 500, 12
DEFAULT_MONEY_THRESHOLD = 1000.0  # USD; override with EXEC_MONEY_THRESHOLD

# Categories that ALWAYS go to the CEO. Pattern based: a list of wording, not understanding. It errs towards sending a request to the CEO.
_ANIMALS = re.compile(r"(?i)\b(fish|shrimp|prawns?|tilapia|barramundi|salmon|stock(?:ing)?|biomass|fry|fingerlings?|juveniles?|larvae|post-?larvae|broodstock|"
                      r"hatchery|nursery|pond|tank|mortalit\w*|survival|disease|pathogen|vaccin\w*|antibiotic\w*|feed|feeds|feeding|feed rate|harvest|cull\w*|welfare|animals?|livestock)\b")
_CHEMICALS = re.compile(r"(?i)\b(chemicals?|probiotic|disinfect\w*|chlorine|formalin|formaldehyde|peroxide|permanganate|copper|sulph\w+|sulf\w+|lime|ammonia|nitrite|nitrate|"
                        r"ph adjust\w*|buffer|antibiotic\w*|medic\w+|treatment|dos(?:e|es|age|ing)|ppm|mg/l|ml/l|g/kg|reagent|pesticide|herbicide|fertili[sz]er|additive)\b")
_STATUTORY = re.compile(r"(?i)\b(statutor\w*|regulat\w*|legal(?:ly)?|law|licen[cs]\w*|permit\w*|compliance|complian\w+|audit(?:or)?s?|certif\w+|asc|bap|globalg\.?a\.?p|"
                        r"authority|ministry|agency|ava|sfa|filing|report(?:ing)? (?:to|requirement)s?|notif(?:y|ication)|disclos\w*|tax|customs|inspection)\b")
_MONEY = re.compile(r"(?i)(?:(?:usd|sgd|us\$|s\$|eur|gbp|rm|myr|vnd|thb|idr)\s?|[$\u20ac\u00a3]\s?)(\d[\d,]*(?:\.\d+)?)\s?(k|m|million|thousand)?|"
                    r"(\d[\d,]*(?:\.\d+)?)\s?(k|m|million|thousand)?\s?(?:usd|sgd|eur|gbp|myr|vnd|thb|idr|dollars)\b")
_MONEY_WORDS = re.compile(r"(?i)\b(budget|spend|spending|invest\w*|capex|opex|purchase|procure\w*|payment|pay|cost(?:s|ing)?|price|quote|contract|fund\w*|loan|salary|payroll)\b")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}


def money_threshold() -> float:
    try:
        value = float(os.getenv("EXEC_MONEY_THRESHOLD", ""))
        return value if value > 0 else DEFAULT_MONEY_THRESHOLD
    except ValueError:
        return DEFAULT_MONEY_THRESHOLD


def amounts(text: str) -> list[float]:
    """Every money figure in the text, in its stated currency's units (no conversion: a figure at or above the threshold counts, whatever the currency)."""
    found = []
    for m in _MONEY.finditer(text or ""):
        number, scale = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            found.append(float(number.replace(",", "")) * _SCALE.get((scale or "").lower(), 1.0))
        except ValueError:
            continue
    return found


def request_text(request: dict[str, Any]) -> str:
    """Everything a person would be approving, as one string, for the classifier to read."""
    summary = request.get("executive_summary") if isinstance(request.get("executive_summary"), dict) else {}
    parts = [request.get("action_summary"), request.get("impact"), request.get("proposed_content"), request.get("rollback_plan"),
             *(request.get("deployment_commands") or []), *(request.get("claims_to_verify") or []), *(summary.get(k) for k in ("purpose", "problem", "risk"))]
    return "\n".join(str(p) for p in parts if p)


def sensitivity(request: dict[str, Any]) -> list[str]:
    """
    The reasons this request is the CEO's, decided by CODE from its text. Empty means none of the four triggers matched. Triggers: animals, chemicals,
    statutory reporting, and money at or above the threshold (or a money topic with no figure: an unquantified spend is not 'below the threshold').
    """
    text = request_text(request)
    reasons: list[str] = []
    if _ANIMALS.search(text):
        reasons.append("it involves animals (stock, feeding, health or welfare)")
    if _CHEMICALS.search(text):
        reasons.append("it involves chemicals, treatments or dosing")
    if _STATUTORY.search(text):
        reasons.append("it involves statutory, regulatory or certification reporting")
    figures = amounts(text)
    threshold = money_threshold()
    if any(f >= threshold for f in figures):
        reasons.append(f"it involves money at or above the {threshold:,.0f} threshold")
    elif not figures and _MONEY_WORDS.search(text):
        reasons.append("it involves money with no figure stated, so it cannot be shown to be below the threshold")
    return reasons


def _text(value: Any, limit: int) -> str:
    """Clean text cut to ``limit``: secrets masked; short fields have their whitespace collapsed, long ones keep their line breaks."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()) if limit < 2000 else value.strip()
    return redact_secrets(text)[:limit]

def _list(value: Any, limit: int, count: int) -> list[str]:
    return [t for t in (_text(v, limit) for v in value[:count]) if t] if isinstance(value, list) else []


def normalize_recommendation(raw: Any, role: str) -> tuple[dict[str, Any] | None, list[str]]:
    """
    Validate a CAO or COO ``authorization_request`` (a recommendation for a person to approve). Returns (request or None, findings). Never raises.
    Anything that could EXECUTE is stripped: there is no execution_plan, no mockups, and ``executed`` is False. The status is forced to pending, the
    risk level has a floor from the text, and ``ceo_only`` / ``ceo_only_reasons`` are written by code (sensitivity()), never taken from the model.
    """
    if role not in ROLES or not isinstance(raw, dict):
        return None, []
    findings: list[str] = []
    summary = _text(raw.get("action_summary"), MAX_SUMMARY)
    content = _text(raw.get("proposed_content"), MAX_TEXT)
    steps = _list(raw.get("steps") or raw.get("deployment_commands"), MAX_ITEM, MAX_STEPS)
    if not summary:
        return None, ["authorization_request had no action_summary; dropped"]
    if not content and not steps:
        return None, ["authorization_request had no proposed_content or steps to approve; dropped"]
    if raw.get("execution_plan"):
        findings.append("an execution_plan was removed: the CAO and COO recommend, they never open pull requests")
    declared = raw.get("risk_level") if raw.get("risk_level") in RISK_LEVELS else None
    if declared is None:
        declared = "high"
        findings.append("authorization_request risk_level missing or unknown; set to high")
    request: dict[str, Any] = {
        "kind": KIND, "proposed_by": role, "action_summary": summary, "risk_level": declared, "impact": _text(raw.get("impact"), MAX_IMPACT),
        "proposed_content": content or None, "deployment_commands": steps, "rollback_plan": _text(raw.get("rollback_plan"), 1500) or None,
        "claims_to_verify": _list(raw.get("claims_to_verify"), 300, 25), "targets": _list(raw.get("targets"), 200, MAX_TARGETS),
        "execution_plan": None, "mockup_urls": [],
    }
    if not request["impact"]:
        request["impact"] = f"Not stated by the {role.upper()}. Treat the impact as unknown and review before approving."
        findings.append("authorization_request had no impact; placeholder added")
    reasons = sensitivity(request)
    floor = "high" if reasons else "medium"
    level = higher_risk(declared, floor)
    if level != declared:
        findings.append(f"authorization_request risk_level raised from {declared} to {level} ({'; '.join(reasons) or 'a recommendation is at least medium'})")
    request["risk_level"] = level
    request["ceo_only"] = bool(reasons)
    request["ceo_only_reasons"] = reasons
    plain, plain_findings = normalize_plain_summary(raw.get("executive_summary"), risk_level=level, allow_identifiers=True)
    request["executive_summary"] = plain
    findings.extend(plain_findings)
    request.update({"status": STATUS_PENDING, "requires_human_approval": True, "executed": False})
    return request, findings


def audit_summary(request: dict[str, Any] | None) -> str:
    """A short text block for the stored reply, so the row reads on its own: it was only asked for, nothing was done, and who decides."""
    if not request:
        return ""
    who = "the CEO only (" + "; ".join(request.get("ceo_only_reasons") or []) + ")" if request.get("ceo_only") else "the CEO, or the COO under delegation where the code allows it"
    lines = ["RECOMMENDATION FOR APPROVAL (pending; nothing has been done)", f"- Action: {request['action_summary']}", f"- Risk level: {request['risk_level']}",
             f"- Impact: {request['impact']}", f"- Who may decide: {who}"]
    if request.get("claims_to_verify"):
        lines.append(f"- Claims to substantiate first: {len(request['claims_to_verify'])}")
    if request.get("proposed_content"):
        lines.append("- Proposed protocol or plan: included in the structured authorization_request.")
    return "\n".join(lines)
