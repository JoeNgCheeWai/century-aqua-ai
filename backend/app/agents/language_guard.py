"""
The LANGUAGE GUARD for all four executives: robotic labels, "hand it to someone else" deflections and "I can draft that if you like" offers are
fixed in CODE, not only asked of the model.

WHY. The CEO kept seeing "ESCALATE TO HUMAN:", "Waiting for your decision" and "I recommend a technical person does this" on routine work.
A prompt is followed most of the time, not every time, so the part that can be exact is exact here. It began with the CTO and now covers the
CAO, COO and CMO, each with its OWN list of people it must not hand routine work to (guard_rules.py): a fish vet, an operations manager, an agency.

WHAT IT DOES, on an executive's reply text:
  1. RELABEL (exact): "ESCALATE TO HUMAN" becomes "Recommendation for CEO review"; "Waiting for your decision" becomes "Ready for your approval".
  2. REMOVE DEFLECTIONS: a sentence that tells the reader to hand THEIR routine task to someone else. The expert is the expert.
  3. REMOVE OFFERS INSTEAD OF DOING (CAO, COO, CMO): "I can draft that if you like." The artefact should already be in the reply. A conditional
     that asks for something the expert truly needs ("once you send the pond size I can build the schedule") is kept.
  4. READY NOTE: ready_note() is the one line code adds under a request, in collaborative words (CTO and CMO have requests).

THE LINE THAT MATTERS: "do this yourself, here is how" versus "the law requires a licensed person". A deflecting sentence is KEPT, never removed, when it:
  * states a LEGAL OR PROFESSIONAL REQUIREMENT (a notifiable disease, a statutory report, a prescription-only medicine, a licence the law demands),
    or is a SAFETY-CRITICAL ESCALATION (mass mortality, an outbreak, an emergency, a risk to public health). Reported as kept_required;
  * carries a COMMAND or a CONCRETE INSTRUCTION (a code span, a quantity with a unit, a price): losing the substance is worse. Reported as kept;
  * says the OPPOSITE ("you do not need a developer").
If removing a sentence would remove the "Recommendation for human review:" line the constitution requires, the label is put back with a neutral ending.

NOT FOR VERDICTS. A cross-check reviewer's answer is a bare JSON verdict that another program parses. The first version of this guard (CTO only)
damaged one: a deflecting sentence inside the "reasoning" string was cut out and the object no longer parsed. guard() returns such text unchanged.

WHAT IT DOES NOT DO. It fixes WORDS. It cannot write the protocol, schedule or copy the model failed to give, and its lists are patterns: a new
phrasing gets past until it is added. The CTO keeps its original behaviour exactly (no offer filter: its prompt and its diagnostic block cover that).
Every removal is logged. It never raises.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.agents import guard_rules

RECOMMENDATION_LABEL = "Recommendation for CEO review"
READY_LABEL = "Ready for your approval"
ROLES = ("cto", "cao", "coo", "cmo")
OFFER_ROLES = ("cao", "coo", "cmo")  # the CTO's offers are handled by its prompt and its code-written diagnostic block

# The CTO's own list (unchanged: its tests and its live script pin it). Roles it must not hand routine work to.
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
PATTERNS: dict[str, re.Pattern[str]] = {"cto": DEFLECTION, **guard_rules.PATTERNS}

# A safety-critical escalation is never a hand-off: it is "act now, and here is who to call".
# It errs towards KEEPING: a clumsy "call a vet" that survives costs nothing, while a vanished warning about dying fish can cost a farm. Words that
# are only about time ("immediately", "urgent", "today") are deliberately NOT here: a lazy hand-off must not hide behind them.
_SAFETY = re.compile(
    r"\b(?:mass\s+mortalit\w*|mortality|mortalities|fish\s+kill|die-?off|dying|dead\s+(?:fish|animals?|stock|shrimp)|(?:unusual|sudden|unexplained)\s+deaths?|"
    r"deaths?\s+(?:in|of|among)|die\b|died\b|gasping|lesions?|haemorrhag\w*|hemorrhag\w*|red\s+sores?|outbreaks?|emergency|"
    r"(?:serious|contagious|infectious|suspected|unknown|spreading)\s+(?:disease|infection|illness)|signs\s+of\s+(?:a\s+)?(?:serious\s+)?(?:disease|infection)|"
    r"public\s+health|human\s+health|food\s+safety|zoonotic)\b",
    re.IGNORECASE)
_NEGATED = guard_rules.NEGATED
_ONLY_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])?\s*(?:#{1,6})?\s*$")
_CLOSING_LABEL = re.compile(r"^\W*(Recommendation\s+for\s+(?:human|CEO)\s+review\s*:)", re.IGNORECASE)


@dataclass
class GuardResult:
    text: str
    removed: list[str] = field(default_factory=list)  # deflecting sentences taken out
    kept: list[str] = field(default_factory=list)  # deflecting sentences kept: a command, a quantity, or the opposite
    kept_required: list[str] = field(default_factory=list)  # deflecting sentences kept: a legal or safety-critical escalation
    offers: list[str] = field(default_factory=list)  # "I can draft that if you like" sentences taken out


def looks_like_verdict(text: str | None) -> bool:
    """A cross-check reviewer's bare JSON verdict ({valid, reasoning, ...}). Another program parses it, so the guard must never touch it."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return False
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return False
    return isinstance(data, dict) and "valid" in data and "reasoning" in data


def relabel(text: str) -> str:
    """The two robotic labels, replaced exactly (a capital first letter stays a capital). Never raises."""
    out = text or ""
    try:
        out = re.sub(r"(?i)ESCALATE\s+TO\s+HUMAN", RECOMMENDATION_LABEL, out)
        return re.sub(r"(?i)\bwaiting\s+for\s+your\s+decision\b",
                      lambda m: READY_LABEL if m.group(0)[:1].isupper() else READY_LABEL.lower(), out)
    except Exception:
        return text or ""


def split_sentences(line: str) -> list[str]:
    """
    One line as sentences, each keeping its trailing space so they join back into exactly the line. A full stop ends a sentence only when it is
    followed by white space or the end of the line, and NEVER inside a **bold** or `code` span. Both matter: copy often has a full stop inside bold
    ("**Fewer water changes.** Calmer ponds."), and cutting there would leave an unclosed ** behind when one half is removed; and "pH 7.5" or
    "5.5 mg/L" is not two sentences.
    """
    pieces: list[str] = []
    start = i = 0
    bold = code = False
    n = len(line)
    while i < n:
        if line[i] == "`":
            code = not code
        elif not code and line.startswith("**", i):
            closing = bold
            bold = not bold
            # A bold span that CLOSES right after a full stop and is followed by a space ends the sentence ("**Fewer water changes.** I can draft ...").
            # Without this the copy and the offer after it would be one sentence, and removing the offer would delete the copy.
            if closing and i > 0 and line[i - 1] in ".!?" and (i + 2 >= n or line[i + 2].isspace()):
                k = i + 2
                while k < n and line[k].isspace():
                    k += 1
                pieces.append(line[start:k])
                start = k
                i = k
                continue
            i += 1
        elif line[i] in ".!?" and not bold and not code:
            j = i
            while j + 1 < n and line[j + 1] in ".!?":
                j += 1
            if j + 1 >= n or line[j + 1].isspace():
                k = j + 1
                while k < n and line[k].isspace():
                    k += 1
                pieces.append(line[start:k])
                start = k
                i = k
                continue
            i = j
        i += 1
    if start < n:
        pieces.append(line[start:])
    return pieces or [line]


def _judge(piece: str, role: str, result: GuardResult, context: str = "") -> bool:
    """
    Decide ONE sentence. True means keep it. Records why in ``result`` for anything that matched a rule.

    ``context`` is the sentence just before it. A warning and its call to action are often two sentences ("Lots of dead fish at the surface?
    Call a fish vet right now."), and the second alone looks like a lazy hand-off. So a deflecting sentence is also kept when the sentence
    before it is a safety emergency or a legal requirement: an emergency must never be separable from what to do about it.
    """
    deflects = bool(PATTERNS[role].search(piece))
    offers = role in OFFER_ROLES and guard_rules.is_offer(piece)
    if not (deflects or offers):
        return True
    if deflects and (guard_rules.is_legal(piece) or _SAFETY.search(piece) or (context and (_SAFETY.search(context) or guard_rules.is_legal(context)))):
        result.kept_required.append(piece.strip())  # the law, or an emergency, is not a hand-off
        return True
    if "`" in piece or (deflects and (_NEGATED.search(piece) or guard_rules.keeps_substance(piece))):
        result.kept.append(piece.strip())  # it may hold the exact command, or the quantity, or it says the opposite
        return True
    (result.removed if deflects else result.offers).append(piece.strip())
    return False


def _clean_line(line: str, role: str, result: GuardResult) -> str | None:
    """One line with its offending sentences taken out. None when nothing is left of a line that had text (the line goes, marker and all)."""
    pieces = split_sentences(line)
    survivors = [piece for i, piece in enumerate(pieces) if _judge(piece, role, result, pieces[i - 1] if i else "")]
    rebuilt = "".join(survivors).rstrip()
    if _ONLY_MARKER.match(rebuilt):
        return None
    return rebuilt if rebuilt.strip() else None


def _restore_closing(original_line: str, role: str) -> str:
    """The constitution's closing line must survive. If every sentence of it was removed, put the label back with a neutral ending."""
    label = _CLOSING_LABEL.match(original_line)
    return f"{label.group(1)} this is advice for your consideration." if label else ""


def guard(text: str | None, role: str = "cto", relabel_first: bool = True) -> GuardResult:
    """
    Relabel, then remove deflections (and, for the CAO, COO and CMO, offers to do the work later). ``role`` is cto, cao, coo or cmo.
    None, empty text and a cross-check verdict pass through unchanged. Never raises.
    """
    if not text or not text.strip():
        return GuardResult(text or "")
    if role not in PATTERNS or looks_like_verdict(text):
        return GuardResult(text)
    result = GuardResult(text)
    try:
        out_lines: list[str] = []
        source = relabel(text) if relabel_first else text
        for line in source.split("\n"):
            if not (PATTERNS[role].search(line) or (role in OFFER_ROLES and guard_rules.is_offer(line))):
                out_lines.append(line)
                continue
            rebuilt = _clean_line(line, role, result)
            if rebuilt is not None:
                out_lines.append(rebuilt)
            else:
                restored = _restore_closing(line, role)
                if restored:
                    out_lines.append(restored)
        if not (result.removed or result.offers):
            result.text = source  # nothing was removed: the text is exactly what came in (relabelled, unless the caller already did that)
            return result
        result.text = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
        return result
    except Exception:
        return GuardResult(relabel(text) if relabel_first else text)


def strip_deflections(text: str | None) -> tuple[str, list[str], list[str]]:
    """The CTO's original interface, kept so its callers and tests do not change: (the text, sentences removed, deflecting sentences KEPT)."""
    if not text or not text.strip():
        return text or "", [], []
    result = guard(text, "cto", relabel_first=False)
    return result.text, result.removed, result.kept + result.kept_required


def clean(text: str | None) -> tuple[str | None, list[str], list[str]]:
    """The CTO's original interface: relabel, then strip. None and empty text pass through unchanged."""
    if not text or not text.strip():
        return text, [], []
    result = guard(text, "cto")
    return result.text, result.removed, result.kept + result.kept_required


def ready_note(has_plan: bool) -> str:
    """The one line code adds under a request, so the CEO always reads it in collaborative words (written by code, never by the model)."""
    tail = ("Nothing is applied until you approve it, and approving only opens a pull request that is not live until it is merged."
            if has_plan else "Nothing is applied until you approve it.")
    return f"**{READY_LABEL}:** this is ready whenever you are. {tail}"


def findings(result: GuardResult, role: str) -> list[str]:
    """The audit-log findings for one reply: every removal and every kept-on-purpose sentence, so a person can see what the guard did."""
    out: list[str] = []
    name = role.upper()
    if result.removed:
        out.append(f"language guard ({name}) removed a deflection to someone else: " + " | ".join(result.removed[:3]))
    if result.offers:
        out.append(f"language guard ({name}) removed an offer to do the work later: " + " | ".join(result.offers[:3]))
    if result.kept:
        out.append(f"language guard ({name}) kept a deflecting sentence because it carries a command or a concrete instruction: " + " | ".join(result.kept[:2]))
    if result.kept_required:
        out.append(f"language guard ({name}) kept a sentence that states a legal, professional or safety escalation: " + " | ".join(result.kept_required[:2]))
    return out
