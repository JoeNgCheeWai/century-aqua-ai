"""
AI_CAO (Chief Aquaculture Officer): the Tier 2 strategic, enterprise-facing
executive agent. INTERNAL ONLY: served exclusively through the /api/cao endpoint,
which requires the X-Internal-CAO-Key header. It is never reachable from the
public chat or the Gatekeeper.
"""

import os

from anthropic import AsyncAnthropic

from app.prompts.cao_prompt import CAO_SYSTEM_PROMPT

# Same model tier as Dr. Aqua for now (PROJECT_CONTEXT.md, Section 4). A separate
# variable lets the CAO be scaled independently later.
CAO_MODEL = os.getenv("CAO_MODEL", "claude-sonnet-5-5")

DEFAULT_CAO_MAX_TOKENS = 4096


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


# Large enough for policy briefs and corporate roadmaps.
CAO_MAX_TOKENS = _int_env("CAO_MAX_TOKENS", DEFAULT_CAO_MAX_TOKENS)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        # Reads ANTHROPIC_API_KEY from the environment.
        _client = AsyncAnthropic()
    return _client


async def get_cao_answer(query: str, history: list[dict] | None = None) -> str:
    """Generate a strategic AI_CAO response using conversation history."""
    messages = []

    # Keep the last 20 messages, as Dr. Aqua does.
    if history:
        for msg in history[-20:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": query})

    response = await _get_client().messages.create(
        model=CAO_MODEL,
        max_tokens=CAO_MAX_TOKENS,
        system=CAO_SYSTEM_PROMPT,
        messages=messages,
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "max_tokens":
        text += "\n\n[Response reached the length limit. Ask me to continue for the remainder.]"
    return text
