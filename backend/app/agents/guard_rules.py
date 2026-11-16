"""
The PATTERNS behind the language guard (language_guard.py) for the CAO, COO and CMO. The CTO's own list stays in language_guard.py, where its
tests and live script pin it. This file is only data and tiny predicates, so the rules can be read, argued with and extended in one place.

Three questions are answered here for one sentence at a time:
  1. Does it hand the reader's routine task to someone else?  (role_pattern: a list of targets PER role)
  2. Is it an offer to do the work instead of doing it?       (is_offer)
  3. Must it be KEPT whatever it says?                        (is_legal: a real legal or professional requirement;
                                                                keeps_substance: it carries a command, a quantity or a price)

THE LINE BETWEEN "DO THIS YOURSELF" AND "THE LAW REQUIRES A LICENSED PERSON". "Consult a fish vet before you choose the dose" is a hand-off:
the CAO is the aquaculture expert and should give the protocol. "A notifiable disease must be reported by law, and a licensed vet has to confirm
it" is not a hand-off, it is the rule, and removing it could hurt someone. is_legal() recognises the second kind by its own words (notifiable,
statutory, by law, prescription, regulations require ...). It deliberately errs towards KEEPING: a clumsy sentence that survives costs nothing,
a legal warning that vanishes can cost a farm.
"""

from __future__ import annotations

import re

# Who each executive must NOT hand ROUTINE work to. (The CTO's list is in language_guard.DEFLECTION.)
ROLE_WORDS = {
    "cao": (r"(?:fish\s+vets?|fish\s+health\s+(?:specialists?|professionals?|experts?)|(?:aquaculture|aquatic|animal\s+health|fisheries)\s+"
            r"(?:vets?|veterinarians?|consultants?|specialists?|experts?|professionals?)|veterinarians?|vets?|biologists?|aquaculture\s+consultants?"
            r"|(?:hatchery|farm)\s+(?:managers?|technicians?)|water\s+quality\s+(?:specialists?|experts?))"),
    "coo": (r"(?:operations\s+(?:managers?|team|leads?|directors?|staff)|ops\s+(?:managers?|team)|(?:finance|accounting|procurement|purchasing|"
            r"logistics|supply\s+chain|hr)\s+(?:team|managers?|department|leads?|officers?|staff)|accountants?|bookkeepers?|"
            r"financial\s+(?:advis[eo]rs?|controllers?|analysts?|planners?)|cfo|project\s+managers?|management\s+consultants?)"),
    "cmo": (r"(?:(?:marketing|creative|advertising|pr|digital|branding|design)\s+agenc(?:y|ies)|agenc(?:y|ies)|copywriters?|"
            r"(?:graphic\s+)?designers?|freelancers?|marketing\s+(?:team|managers?|consultants?|staff)|content\s+(?:team|creators?|writers?)|"
            r"pr\s+(?:firm|team)|social\s+media\s+(?:managers?|team))"),
}
_VERBS = (r"(?:get|hire|ask|find|consult|contact|call|involve|bring\s+in|speak\s+(?:to|with)|talk\s+(?:to|with)|check\s+with|engage|"
          r"commission|outsource\s+(?:this\s+)?to|refer\s+(?:this\s+)?to|defer\s+to|escalate\s+(?:this\s+)?to)")
_DOES = (r"(?:do|run|handle|take\s+care\s+of|prepare|write|draft|set\s+up|produce|create|design|calculate|schedule|plan|build|"
         r"decide|choose|pick|select|determine|review|assess|advise)")
GENERIC = (r"\b(?:hand|pass|delegate|escalate)\s+(?:this|it|the\s+task)\s+(?:off\s+)?to\s+(?:a|an|your|someone|somebody)\b"
           r"|\b(?:someone|somebody)\s+(?:more\s+(?:experienced|senior|qualified)|who\s+knows\s+(?:more|this|the\s+field))\b")


def role_pattern(words: str) -> re.Pattern[str]:
    """The hand-off pattern for one role: a verb that sends the reader away, then one of that role's targets."""
    return re.compile(
        rf"\b{_VERBS}\s+(?:with\s+)?(?:a|an|your|the|our|some|any|one|someone|somebody)\s+(?:[\w-]+\s+){{0,2}}{words}\b(?!['\u2019])"
        rf"|\b(?:have|let)\s+(?:a|an|your|the)\s+(?:[\w-]+\s+){{0,2}}{words}\s+{_DOES}\b"
        rf"|\b(?:your|the|our)\s+{words}\s+(?:can|should|could|will|must|needs?\s+to|to)\s+{_DOES}\b"
        rf"|\brecommend\s+(?:a|an)\s+(?:[\w-]+\s+){{0,2}}{words}\b(?!['\u2019])|" + GENERIC,
        re.IGNORECASE,
    )


PATTERNS: dict[str, re.Pattern[str]] = {role: role_pattern(words) for role, words in ROLE_WORDS.items()}


# An offer to do the work INSTEAD of doing it ("I can draft that if you like"). The verbs are the work itself.
_OFFER = re.compile(
    r"\b(?:i\s+can(?:\s+also)?|i\s+could|i(?:'d|\s+would)\s+be\s+(?:happy|glad)\s+to|happy\s+to|would\s+you\s+like\s+me\s+to|shall\s+i|should\s+i|"
    r"want\s+me\s+to|do\s+you\s+want\s+me\s+to|let\s+me\s+know\s+if\s+you(?:'d|\s+would)?\s+(?:like|want)(?:\s+me)?\s+to)\b"
    r"[^.!?\n]{0,60}?\b(?:draft|write|prepare|put\s+together|produce|create|build|outline|calculate|cost|schedule|design|mock\s*up|compose|"
    r"lay\s+out|work\s+out|investigate|look\s+into|diagnose|dig\s+into)\b",
    re.IGNORECASE,
)
_IF_YOU_LIKE = re.compile(r"\bif\s+you(?:'d|\s+would)?\s+(?:like|want|prefer|wish)\b", re.IGNORECASE)
_CONDITIONAL = re.compile(r"\b(?:if|once|when|as\s+soon\s+as|after\s+you|provided|unless)\b", re.IGNORECASE)

# A command or a concrete instruction: a code span, a quantity with a unit, or a price. Losing it is worse than keeping a clumsy sentence.
QUANTITY = re.compile(
    r"(?:[$\u20ac\u00a3]|S\$)\s?\d|\d[\d,.]*\s?(?:%|mg/l|mg|g/m3|g|kg|tonnes?|ppm|ppt|ml|litres?|liters?|l|hours?|hrs?|minutes?|mins?|days?|weeks?|"
    r"months?|usd|sgd|\u00b0\s?c|ha|m2|m3)(?![A-Za-z])", re.IGNORECASE)

# A real legal or professional requirement. Strong markers always keep the sentence.
_LEGAL_STRONG = re.compile(
    r"\b(?:by\s+law|legally|legal\s+(?:requirement|obligation|duty)|law\s+requires|required\s+by\s+(?:law|regulation|statute)|statut\w+|notifiable|"
    r"reportable|must\s+be\s+(?:reported|notified)|mandatory|binding\s+(?:legal|tax|financial|regulatory)|prescription|prescrib\w+|woah|"
    r"(?:regulations?|regulators?|authorit(?:y|ies)|legislation)\s+(?:requires?|demands?|mandates?)|"
    r"report\s+(?:it|this|the\s+(?:outbreak|disease|case))\s+to\s+the\s+(?:\w+\s+){0,2}(?:authorit\w+|regulator|competent))\b", re.IGNORECASE)
# "Licensed", "registered" and "certified" only count when the sentence also says it is an obligation.
_LEGAL_ROLE = re.compile(r"\b(?:licen[cs]ed|registered|certified|chartered|accredited)\b", re.IGNORECASE)
_OBLIGATION = re.compile(r"\b(?:required|requires?|must|only|has\s+to|have\s+to|need(?:s)?\s+to\s+be|cannot|can'?t|may\s+only|law|legal)\b", re.IGNORECASE)
NEGATED = re.compile(r"\b(?:no\s+need|not\s+need|don'?t\s+need|doesn'?t\s+need|never|without|rather\s+than|instead\s+of)\b", re.IGNORECASE)


def is_legal(sentence: str) -> bool:
    """True if the sentence states a legal or professional REQUIREMENT (a notifiable disease, a statutory report, a prescription, a licence the law demands)."""
    return bool(_LEGAL_STRONG.search(sentence) or (_LEGAL_ROLE.search(sentence) and _OBLIGATION.search(sentence)))


def keeps_substance(sentence: str) -> bool:
    """True if the sentence carries a command (a code span), a bold key term or line of copy, or a concrete number with a unit or a price."""
    return "`" in sentence or "**" in sentence or bool(QUANTITY.search(sentence))  # bold is how a reply marks its key figure, name or line of copy


def is_offer(sentence: str) -> bool:
    """
    True if the sentence only OFFERS to do the work ("I can draft that if you like", "Would you like me to write it?"). A conditional that
    asks for something the expert truly needs ("once you send the pond size I can build the schedule") is not an offer, and neither is a
    sentence that already contains the work (a quantity, a code span, or more than a short remark).
    """
    if not _OFFER.search(sentence):
        return False
    if _IF_YOU_LIKE.search(sentence) or sentence.strip().endswith("?"):
        return True
    if _CONDITIONAL.search(sentence):
        return False
    return not keeps_substance(sentence) and len(sentence.split()) <= 16
