"""
Vetting of the conversational ``chat_reply`` (Phase 6.5).

The chat window shows ``chat_reply`` first and the full ``response`` behind a "technical details" button. The constitutional
score is computed from ``response`` (the audited recommendation), NOT from ``chat_reply``, so the short text would
otherwise escape the one automated check on what an executive claims to have done. This closes that gap in code: a
chat_reply that says the agent itself approved, executed, sent, published or deployed something is dropped, and the UI
falls back to the full response.

What this does NOT do: it does not check that the chat_reply agrees with the response, or that it is accurate.
"""

from __future__ import annotations

import re

from app.governance.scoring import claims_action


_REVIEW_LINE = re.compile(r"(?mi)^[ \t>*_-]*recommendation for human review\s*:.*(?:\n|$)")
_CONFIDENCE_TAIL = re.compile(r"(?i)[ \t]*\*{0,2}\bconfidence\*{0,2}\s*:\s*\*{0,2}(\d{1,3})\s*%\*{0,2}\.?")
_BOILERPLATE_HEADING = re.compile(r"(?i)^\s*(?:#{1,6}\s*|\*\*)\s*(assumptions?|what to check|what to look for after|things to check)\b[^\n]*$")
_ANY_HEADING = re.compile(r"^\s*#{1,6}\s+\S")


def strip_boilerplate(value: str | None) -> str | None:
    """
    Remove what the CTO's RESPONSE FORMAT says the CEO must not have to read: an "Assumptions" or "What to check" section, a "Confidence: NN%" rating and the
    "Recommendation for human review:" line. CODE does this on the CHAT reply only, because a prompt cannot guarantee it. The audited ``response`` (behind the
    details button) is untouched and is what the Constitution scores, so the score does not change.

    Kept on purpose: a LOW confidence (below 60%), because the CEO must be told plainly when the answer is shaky, and "Recommendation for CEO review:"
    (an escalation). Never raises. Returns None for an empty value, and the original text if stripping would leave nothing.
    """
    if not value or not value.strip():
        return value
    try:
        text = _REVIEW_LINE.sub("", value)
        text = _CONFIDENCE_TAIL.sub(lambda m: m.group(0) if int(m.group(1)) < 60 else "", text)
        kept: list[str] = []
        skipping = False
        for line in text.split("\n"):
            if _BOILERPLATE_HEADING.match(line):
                skipping = True  # drop the heading and its body, up to the next heading
                continue
            if skipping and _ANY_HEADING.match(line):
                skipping = False
            if not skipping:
                kept.append(line)
        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        return cleaned or value
    except Exception:
        return value


def vetted_chat_reply(value: str | None) -> str | None:
    """The chat_reply if it is usable, else None (empty, or it claims the agent took an action)."""
    if not value or not value.strip():
        return None
    return None if claims_action(value) else value.strip()
