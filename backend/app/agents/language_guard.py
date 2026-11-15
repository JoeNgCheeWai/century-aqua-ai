"""
The CTO's LANGUAGE GUARD: robotic labels and "hand it to someone else" deflections are fixed in CODE, not only asked of the model.

WHY. The CEO kept seeing "ESCALATE TO HUMAN:", "Waiting for your decision" and "I recommend a technical person does this" on routine work (a
standard database migration, a one-line label change), after the prompts had been rewritten twice. A prompt is an instruction the model follows
most of the time, not every time, so the part that can be exact is exact here, the same way the plan-dropped note and the Self-check line are.

WHAT IT DOES, on the CTO's reply text:
  1. RELABEL (exact): "ESCALATE TO HUMAN" becomes "Recommendation for CEO review", and "Waiting for your decision" becomes "Ready for your
     approval". The constitution's scorer accepts both forms, so scoring is unchanged.
  2. REMOVE DEFLECTIONS: a sentence that tells the CEO to hand the work to "a technical person", "a developer", "an engineer", "your IT team" or
     "someone technical" is removed. The CTO IS the technical person. Two kinds of sentence are KEPT even if they match: one that says the
     opposite ("you do not need a developer"), and one that carries a code span (backticks), because it may hold the exact command the CEO
     needs and losing it is worse than keeping a clumsy sentence. Kept ones are returned so they can be logged as a finding.
  3. READY NOTE: ready_note() is the one line code adds under a request, in collaborative words.

WHAT IT DOES NOT DO. It does not judge whether the advice is right, and it cannot write the copy-pasteable solution the model failed to give: if
the model deflects and gives no commands, removing the deflection leaves a shorter answer, not a better one. The prompt (persona.py, ZERO-FRICTION)
and scripts/try_cto_zero_friction.py are what make the model give the commands; this guard makes sure the wrong words never reach the CEO.
It never raises.
"""

from __future__ import annotations

import re

RECOMMENDATION_LABEL = "Recommendation for CEO review"
READY_LABEL = "Ready for your approval"

# Roles a task might be handed to. "the technical expert" is NOT matched (the CTO may say it is the technical expert).
_ROLE = (r"(?:developers?|engineers?|dbas?|database\s+administrators?|devops(?:\s+engineers?)?|technicians?|programmers?"
         r"|it\s+(?:team|department|staff|support)|tech\s+support)")
DEFLECTION = re.compile(
    r"\b(?:a|an|some|any)\s+(?:more\s+|qualified\s+|experienced\s+)?technical\s+(?:person|people|expert|specialist|professional|staff|resource)\b"
    r"|\b(?:get|hire|ask|find|consult|contact|call|involve|bring\s+in)\s+(?:a|an|your|the|someone|somebody|one)\s+" + _ROLE + r"\b"
    r"|\b(?:have|let)\s+(?:a|an|your|the)\s+" + _ROLE + r"\s+(?:do|run|handle|take\s+care|apply|set\s+up|fix|look)\b"
    r"|\b(?:your|the|our)\s+" + _ROLE + r"\s+(?:can|should|could|will|must|to)\s+(?:do|run|handle|take\s+care|check|apply|set\s+up|fix)\b"
    r"|\b(?:someone|somebody)\s+(?:technical|more\s+technical|who\s+knows\s+(?:databases?|code|the\s+system))\b"
    r"|\b(?:hand|pass|delegate|escalate)\s+(?:this|it|the\s+task)\s+(?:off\s+)?to\s+(?:a|an|your|someone|somebody)\b"
    r"|\brecommend\s+(?:a|an)\s+(?:technical|qualified)\b",
    re.IGNORECASE,
)
# A sentence that says the opposite is kept ("you do not need a developer for this").
_NEGATED = re.compile(r"\b(?:no\s+need|not\s+need|don'?t\s+need|doesn'?t\s+need|never|without|rather\s+than|instead\s+of)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)\s*")
_ONLY_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])?\s*(?:#{1,6})?\s*$")

def relabel(text: str) -> str:
    """The two robotic labels, replaced exactly (a capital first letter stays a capital). Never raises."""
    out = text or ""
    try:
        out = re.sub(r"(?i)ESCALATE\s+TO\s+HUMAN", RECOMMENDATION_LABEL, out)
        return re.sub(r"(?i)\bwaiting\s+for\s+your\s+decision\b",
                      lambda m: READY_LABEL if m.group(0)[:1].isupper() else READY_LABEL.lower(), out)
    except Exception:
        return text or ""


def _clean_line(line: str, removed: list[str], kept: list[str]) -> str | None:
    """One line with its deflecting sentences taken out. None when nothing is left of a line that had text (the line goes, marker and all)."""
    survivors = []
    for piece in _SENTENCE.findall(line) or [line]:
        if not DEFLECTION.search(piece):
            survivors.append(piece)
        elif "`" in piece or _NEGATED.search(piece):
            survivors.append(piece)  # it may hold the exact command, or it says the opposite: keep it, and report it
            kept.append(piece.strip())
        else:
            removed.append(piece.strip())
    rebuilt = "".join(survivors).rstrip()
    if _ONLY_MARKER.match(rebuilt):  # "1." or "-" with nothing after it: the whole list item went, so its number goes too
        return None
    return rebuilt if rebuilt.strip() else None


def strip_deflections(text: str) -> tuple[str, list[str], list[str]]:
    """
    Remove sentences that hand the work to someone else. Returns (the text, the sentences removed, the deflecting sentences KEPT). Line breaks,
    lists and headings are preserved; only whole sentences go, and a line that held nothing else goes with them. Never raises.
    """
    removed: list[str] = []
    kept: list[str] = []
    try:
        out_lines: list[str] = []
        for line in (text or "").split("\n"):
            if not DEFLECTION.search(line):
                out_lines.append(line)
                continue
            rebuilt = _clean_line(line, removed, kept)
            if rebuilt is not None:
                out_lines.append(rebuilt)
        if not removed:
            return text or "", [], kept
        return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip(), removed, kept
    except Exception:
        return text or "", [], []


def clean(text: str | None) -> tuple[str | None, list[str], list[str]]:
    """relabel() then strip_deflections(). None and empty text pass through unchanged."""
    if not text or not text.strip():
        return text, [], []
    cleaned, removed, kept = strip_deflections(relabel(text))
    return cleaned, removed, kept


def ready_note(has_plan: bool) -> str:
    """The one line code adds under a request, so the CEO always reads it in collaborative words (written by code, never by the model)."""
    tail = ("Nothing is applied until you approve it, and approving only opens a pull request that is not live until it is merged."
            if has_plan else "Nothing is applied until you approve it.")
    return f"**{READY_LABEL}:** this is ready whenever you are. {tail}"
