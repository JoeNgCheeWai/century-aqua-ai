"""
Live check of ONE Boardroom round-1 turn per seated agent against the REAL model (a few cheap calls; needs ANTHROPIC_API_KEY).

    cd backend; python scripts/try_boardroom_turn.py [cao coo cmo ...]

For each agent it sends exactly what a real session sends in round 1 (the council system prompt and TURN_CONTRACT), through the
council's own call_model, and prints: whether the call raised (and the real error), the stop reason, how many tokens were thinking
and how many were text, the seconds it took, the first 400 characters of the reply, and what normalize_turn made of it. This is the
tool that shows WHY a session says "only 0 usable proposals".
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.agents import council  # noqa: E402
from app.prompts import council_prompts as prompts  # noqa: E402

QUERY = "Should we launch the new customer portal this quarter? Pilot budget cap USD 150,000; initial market Vietnam."


async def one(agent_id: str) -> None:
    session = council.Session("live-check", QUERY, ["cao", "coo", "cmo"], "check")
    user = f"{prompts.TURN_CONTRACT}\n\n{council._table(session)}THE CEO'S QUESTION:\n{council._wrap(QUERY)}\n\nGive your proposal."
    started = time.perf_counter()
    print(f"\n=== {agent_id.upper()} (chair: {session.chair.upper()}) | model={council.COUNCIL_MODEL} thinking={council.THINKING_MODE} "
          f"max_tokens={council.TURN_MAX_TOKENS} timeout={council.TURN_TIMEOUT_SECONDS}s")
    try:
        reply = await council.call_model(council.system_prompt_for(agent_id), user, max_tokens=council.TURN_MAX_TOKENS,
                                         budget=council.Budget(), keep=council.RESERVED_CALLS, timeout=council.TURN_TIMEOUT_SECONDS)
    except BaseException as exc:  # show the REAL error, which the session log reduces to its type name
        print(f"  RAISED after {time.perf_counter() - started:.1f}s: {type(exc).__name__}: {str(exc)[:600]}")
        return
    turn = council.normalize_turn(agent_id, reply.text)
    print(f"  returned in {time.perf_counter() - started:.1f}s | stop_reason={reply.stop_reason} | truncated={reply.truncated} | "
          f"output_tokens={reply.output_tokens} thinking_tokens={reply.thinking_tokens} | text chars={len(reply.text)}")
    print(f"  reply starts: {reply.text[:400]!r}")
    print(f"  normalize_turn -> valid={turn['valid']} stance={turn['stance']} confidence={turn['confidence']} "
          f"reasoning chars={len(turn['reasoning'])} position={turn['position'][:100]!r}")


async def main(agents: list[str]) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    for agent_id in agents:
        await one(agent_id)


if __name__ == "__main__":
    asyncio.run(main([a.lower() for a in sys.argv[1:]] or ["coo", "cao", "cmo"]))
