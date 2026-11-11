"""
The plain-language summary of an authorization request (Phase 6.5, CTO and CMO).

The model is asked for three short parts, answering the three questions a non-technical decision-maker has:

    "executive_summary": {"purpose": "What is this task for?",
                          "problem": "What problem does it solve?",
                          "risk":    "What could go wrong, and what protects us?"}

Enforced HERE in code, not left to the model:
  * All three parts are required. A summary with a missing part is dropped whole (never half-shown), with a finding.
  * Each part is capped (MAX_PART_CHARS), cut at a sentence end where possible.
  * A part that says the agent itself already did something (approved, deployed, published...) drops the summary.
  * For the CTO (allow_identifiers=False) a part that contains code artefacts (backticks, CamelCase or snake_case
    names, file names or paths, braces) drops the summary: that is exactly what "too technical" means to a CEO. The CMO
    may name brands such as LinkedIn, so it skips this check.
  * THE RISK LEVEL IS NOT THE MODEL'S TO RATE. The badge next to this text comes from the code-computed risk level (which
    has floors the model cannot lower). If the model's risk text claims the risk is low or minimal while the computed
    level is higher, the model's text is replaced with a neutral sentence, so the screen never says "low risk" next to a
    "high risk" badge.

What this does NOT do: it does not check that the three parts are TRUE, or that the wording is simple. The identifier
check is a pattern check: it will miss jargon made of ordinary words ("idempotent", "rollback") and can wrongly flag an
unusual word. The prompt carries the plain-language instructions; this code only catches the clear failures.
"""

from __future__ import annotations

import re
from typing import Any

from app.governance.scoring import claims_action

PARTS = ("purpose", "problem", "risk")
MAX_PART_CHARS = 300

# Code artefacts a CEO should never meet in the summary.
_CODE_ARTEFACT = re.compile(
    r"`"  # inline code
    r"|\b[a-z]+_[a-z0-9_]+\b"  # snake_case name
    r"|\b(?:[A-Za-z]*[a-z][A-Z]){2}[A-Za-z]*\b"  # CouncilSessionLog: two or more lower-to-upper steps
    r"|\b[a-z]+[A-Z][A-Za-z]*\b"  # reviewNote: camelCase starting in lower case
    r"|\b[\w-]+\.(?:py|tsx?|jsx?|json|sql|prisma|ya?ml|md|env|toml|sh)\b"  # a file name
    r"|\b[\w.-]+/[\w.-]+/[\w./-]+"  # a path with two or more slashes
    r"|[{}\[\]]|\(\)|=>|::"  # braces, brackets, empty call, arrow
)

# "low risk", "risk is low", "minimal risk", "no risk", "risk-free": a claim that understates a higher computed level.
_LOW_RISK_CLAIM = re.compile(
    r"(?i)\b(?:low|minimal|minor|negligible|trivial|tiny|very small)[- ](?:\w+[- ])?risk\b"
    r"|\brisk\s+(?:is|are|looks|seems|remains|stays)\s+(?:very\s+|quite\s+|fairly\s+)?(?:low|minimal|minor|negligible|small)\b"
    r"|\bno\s+(?:real\s+|significant\s+)?risk\b|\brisk[- ]free\b"
)


def _part(value: Any) -> str:
    """One part as a clean, capped string ('' if it is not a string)."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) <= MAX_PART_CHARS:
        return text
    cut = text[:MAX_PART_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end >= MAX_PART_CHARS // 3 else cut.rstrip() + "…"


def code_risk_sentence(level: str) -> str:
    """What the screen says in place of a model's risk text that contradicts the computed level."""
    return f"The safety checks rate this {level} risk. Read the technical details before deciding."


def normalize_plain_summary(raw: Any, *, risk_level: str, allow_identifiers: bool) -> tuple[dict[str, str] | None, list[str]]:
    """
    Validate the model's ``executive_summary``. Returns (summary or None, findings). Never raises.

    None with no findings means the model sent no summary. None with a finding means one was sent and dropped.
    """
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["executive_summary was not an object with purpose, problem and risk; dropped"]

    parts = {name: _part(raw.get(name)) for name in PARTS}
    missing = [name for name, text in parts.items() if not text]
    if missing:
        return None, [f"executive_summary was missing {', '.join(missing)}; dropped"]

    joined = " ".join(parts.values())
    if claims_action(joined):
        return None, ["executive_summary claimed an action was taken; dropped"]
    if not allow_identifiers and _CODE_ARTEFACT.search(joined):
        return None, ["executive_summary used code or file names (not plain language); dropped"]

    findings: list[str] = []
    if risk_level != "low" and _LOW_RISK_CLAIM.search(parts["risk"]):
        parts["risk"] = code_risk_sentence(risk_level)
        findings.append(f"executive_summary understated the risk (computed level is {risk_level}); the risk text was replaced")
    return parts, findings
