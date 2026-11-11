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

from app.governance.scoring import claims_action


def vetted_chat_reply(value: str | None) -> str | None:
    """The chat_reply if it is usable, else None (empty, or it claims the agent took an action)."""
    if not value or not value.strip():
        return None
    return None if claims_action(value) else value.strip()
