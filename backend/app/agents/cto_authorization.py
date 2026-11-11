"""
The CTO's Authorization Workflow (Phase 3 of the AI Executive Team).

THE RULE: the AI_CTO never executes a code change or a system amendment. It has no tools,
no shell and no deploy access. When a request involves changing code, configuration, the
database, infrastructure, access or secrets, the CTO must output a structured
``authorization_request`` for a HUMAN to review and approve. Only a person (or Cline, the
coding agent, working under that person's explicit approval) can carry the change out.

    {"action_summary": str,            # what would change, in one or two sentences
     "risk_level": "low" | "medium" | "high" | "critical",
     "impact": str,                    # what is affected, and what could break
     "proposed_code_diff": str | null, # a unified diff or code, as TEXT for review
     "proposed_commands": [str],       # commands for a human to run, as TEXT for review
     "rollback_plan": str | null,
     "status": "pending_human_approval",    # forced by code, never taken from the model
     "requires_human_approval": true,       # forced by code
     "executed": false}                     # forced by code

What is enforced HERE in code, not left to the model (the model is only instructions):
  * status, requires_human_approval and executed are always the three values above, so an
    authorization_request can never claim to be approved or executed.
  * A missing or unknown risk_level becomes "high" (fail safe, never "low").
  * The risk level has a FLOOR set by the text of the proposed commands and diff. A command
    that destroys data or history, or alters production state, cannot be labelled lower than
    the floor, however the model labels it. The floor can only raise the level.
  * A request with nothing to review (no summary, or neither a diff nor commands) is not
    returned as a request; a finding says so.
  * Lengths are capped, and an authorization_request is never produced from nothing.

What this does NOT do: it does not run anything and it does not prove a diff is correct or
safe. The floor is a keyword check and will miss risky changes that do not look risky in
text. A human must still read the diff (Clause 0).
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.execution_plan import describe as describe_plan
from app.agents.execution_plan import higher_risk, normalize_execution_plan, plan_risk_floor, plan_text
from app.agents.plain_summary import normalize_plain_summary

RISK_LEVELS = ("low", "medium", "high", "critical")
STATUS_PENDING = "pending_human_approval"

MAX_SUMMARY_CHARS = 600
MAX_IMPACT_CHARS = 2000
MAX_DIFF_CHARS = 12000
MAX_COMMANDS = 20
MAX_COMMAND_CHARS = 500
MAX_ROLLBACK_CHARS = 1500

# Risk floors. Each pattern is matched against the proposed commands and diff (case-insensitive).
_CRITICAL = [
    r"\bdrop\s+(table|database|schema)\b",
    r"\btruncate\s+(table\s+)?\"?\w+",
    r"\bdelete\s+from\s+\"?\w+\"?\s*(;|$)",  # DELETE FROM with no WHERE
    r"\bprisma\s+(migrate\s+reset|db\s+push\b.*--force-reset)",
    r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\*|\.\s|\.$)",
    r"\bgit\s+push\b.*(--force\b|-f\b)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bformat\s+[a-z]:",
    r"\b(mkfs|dd\s+if=)\b",
]
_HIGH = [
    r"\bmigrate\s+(deploy|dev|resolve)\b",
    r"\bdb\s+push\b",
    r"\balter\s+table\b",
    r"\bdelete\s+from\b",
    r"\bkubectl\s+(apply|delete|rollout)\b",
    r"\bterraform\s+(apply|destroy)\b",
    r"\b(docker|docker-compose)\s+(rm|system\s+prune|compose\s+down)\b",
    r"\bgit\s+push\b",
    r"\b(chmod|chown)\s+-r\b",
    r"\b(INTERNAL_(CAO|COO|CTO)_KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY|PINECONE_API_KEY|CLERK_SECRET_KEY|DATABASE_URL|GOVERNANCE_DATABASE_URL)\b",
    r"\bsecret|password|credential|private[_ ]key|api[_ ]key\b",
    r"\b(render|vercel|neon)\b.*\b(deploy|env|delete|promote)\b",
    r"\bpip\s+install\b|\bnpm\s+(install|i)\b|\bnpm\s+uninstall\b",
]
_MEDIUM = [
    r"\bgit\s+(commit|merge|rebase|checkout\s+-b)\b",
    r"\bnpx?\s+prisma\b",
    r"\bsed\s+-i\b|\bRemove-Item\b|\bdel\s+/",
    r"^\s*[-+]{3}\s+\S+",  # any unified-diff file header: files will change
]
_FLOORS = (("critical", _CRITICAL), ("high", _HIGH), ("medium", _MEDIUM))
_ORDER = {level: index for index, level in enumerate(RISK_LEVELS)}


def risk_floor(commands: list[str], diff: str | None) -> str:
    """The lowest risk level the proposed text allows: 'low' if nothing alarming is found."""
    haystack = "\n".join(list(commands) + ([diff] if diff else []))
    for level, patterns in _FLOORS:
        for pattern in patterns:
            if re.search(pattern, haystack, re.IGNORECASE | re.MULTILINE):
                return level
    return "low"


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


def _commands(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [line for line in value.splitlines() if line.strip()]
    if not isinstance(value, (list, tuple)):
        return []
    cleaned = [_text(item, MAX_COMMAND_CHARS) for item in value if isinstance(item, (str, int, float))]
    return [item for item in cleaned if item][:MAX_COMMANDS]


def _risk(value: Any) -> str | None:
    """The model's risk level if it is a known one, else None (the caller then fails safe to 'high')."""
    if isinstance(value, str) and value.strip().lower() in RISK_LEVELS:
        return value.strip().lower()
    return None


def normalize_authorization_request(raw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """
    Validate the model's ``authorization_request``. Returns (request or None, findings).

    None means "no request": the model sent null (a question that needs no change), or sent
    something with nothing to review. ``findings`` explains the second case. Never raises.
    """
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["authorization_request was not an object; dropped"]

    summary = _text(raw.get("action_summary"), MAX_SUMMARY_CHARS)
    impact = _text(raw.get("impact"), MAX_IMPACT_CHARS)
    diff = _text(raw.get("proposed_code_diff"), MAX_DIFF_CHARS) or None
    commands = _commands(raw.get("proposed_commands"))
    rollback = _text(raw.get("rollback_plan"), MAX_ROLLBACK_CHARS) or None

    # Phase 6.6: an optional execution_plan (branch, complete files, PR text). It is validated whole-or-nothing. A refused
    # plan is dropped with a finding; the rest of the request can still stand on its diff or commands.
    plan, plan_findings = normalize_execution_plan(raw.get("execution_plan"))

    if not summary:
        return None, ["authorization_request had no action_summary; dropped"]
    if not diff and not commands and plan is None:
        return None, [*plan_findings, "authorization_request had neither a proposed_code_diff, proposed_commands nor a valid execution_plan; dropped"]

    findings: list[str] = list(plan_findings)
    declared = _risk(raw.get("risk_level"))
    if declared is None:
        declared = "high"
        findings.append("authorization_request risk_level missing or unknown; set to high")
    floor = higher_risk(risk_floor(commands, diff), plan_risk_floor(plan))
    # The plan's CODE is read by the same patterns as a diff (a secret, a destructive command written into a file).
    if plan is not None:
        floor = higher_risk(floor, risk_floor([], plan_text(plan)))
    level = declared if _ORDER[declared] >= _ORDER[floor] else floor
    if level != declared:
        findings.append(f"authorization_request risk_level raised from {declared} to {level} (proposed text matches a {floor}-risk pattern)")
    if not impact:
        impact = "Not stated by the CTO. Treat the impact as unknown and review before approving."
        findings.append("authorization_request had no impact; placeholder added")

    # Phase 6.5: the plain-language summary a non-technical human decides on (purpose, problem, risk). Optional. It is
    # checked against the COMPUTED risk level, so it can never say "low risk" next to a high-risk badge (plain_summary.py).
    executive_summary, summary_findings = normalize_plain_summary(raw.get("executive_summary"), risk_level=level, allow_identifiers=False)
    findings.extend(summary_findings)

    request = {
        "action_summary": summary,
        "executive_summary": executive_summary,
        "risk_level": level,
        "impact": impact,
        "proposed_code_diff": diff,
        "proposed_commands": commands,
        "rollback_plan": rollback,
        # Phase 6.6: what approving would do. None unless the agent sent a plan that passed validation. Approving opens a
        # pull request from it (never a merge); the outcome is stored later under "execution".
        "execution_plan": plan,
        # Forced by code. Nothing the model writes can mark a request approved or executed.
        "status": STATUS_PENDING,
        "requires_human_approval": True,
        "executed": False,
    }
    return request, findings


def audit_summary(request: dict[str, Any] | None) -> str:
    """
    A short text block describing the request, appended to the reply that is stored in AgentAuditLog.
    The full structured request lives in AgentAuditLog.authorizationRequest (JSON); this block keeps the
    stored response text readable on its own: that a change was asked for, at what risk, and that it
    awaits approval with nothing executed. It omits the diff, which is only in the JSON column.
    """
    if not request:
        return ""
    lines = [
        "AUTHORIZATION REQUEST (pending human approval; nothing has been executed)",
        f"- Action: {request['action_summary']}",
        f"- Risk level: {request['risk_level']}",
        f"- Impact: {request['impact']}",
    ]
    if request.get("proposed_commands"):
        lines.append("- Proposed commands (for a human to review and run, NOT run by the CTO):")
        lines.extend(f"    {command}" for command in request["proposed_commands"])
    if request.get("proposed_code_diff"):
        lines.append("- Proposed code diff: included in the structured authorization_request.")
    if request.get("execution_plan"):
        lines.append("- " + describe_plan(request["execution_plan"]))
    if request.get("rollback_plan"):
        lines.append(f"- Rollback plan: {request['rollback_plan']}")
    return "\n".join(lines)

