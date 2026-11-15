"""
Live check of the CTO's communication style against the REAL model (needs ANTHROPIC_API_KEY, no database, no GitHub; the read tool is off).

    cd backend; python scripts/try_cto_style.py

Four realistic CEO messages go through the real CTO. For each it prints the model's stop reason (a REFUSAL here is the first thing to look for:
the Boardroom prompt was once refused outright and every mock accepted it), then checks the chat_reply the CEO reads first against the
COMMUNICATION PROTOCOL: structured layout where it guides, numbered steps, bold, a "why" before the ask, solutions with a recommendation,
partner wording instead of gatekeeper wording, and no claim that anything was done. Then it prints the chat_reply so a person can read it.
These are mechanical checks of the layout and of banned phrases. They cannot judge whether the explanation is good; a person must read it.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.agents import cto_agent  # noqa: E402
from app.governance.scoring import claims_action  # noqa: E402

SCENARIOS = {
    "guide": "I want to see whether the Learning Loop is actually saving my approvals. Where do I look and what should I see?",
    "broken": "The Boardroom said 'only 0 usable proposals' and I do not know what that means or what to do about it.",
    "simple": "Is it safe to leave the Mission Control login PIN as it is for now?",
    "change": "Please add a small badge on the Boardroom page that shows how many sessions ran today. Draft the change for me.",
}
GATEKEEPER = re.compile(r"(?i)a human (must|should) (decide|confirm|review|approve)|nothing has been changed by me|pending human approval|the human must")
HEADING = re.compile(r"(?m)^#{2,4} \S")
NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+\S")
BOLD = re.compile(r"\*\*[^*\n]+\*\*")
WHY = re.compile(r"\b(because|this matters|why this|the reason|so that|foundation)\b|^#{2,4}\s*why\b", re.IGNORECASE | re.MULTILINE)
RECOMMEND = re.compile(r"(?i)\b(i recommend|my recommendation|i suggest|what i suggest|i('d| would) (suggest|choose|go with))\b")


class Spy:
    def __init__(self, real):
        self.real, self.messages, self.stops = real, self, []

    async def create(self, **kwargs):
        reply = await self.real.messages.create(**kwargs)
        self.stops.append(getattr(reply, "stop_reason", None))
        return reply


async def one(label: str, message: str) -> bool:
    spy = Spy(cto_agent._get_client())
    cto_agent._get_client = lambda: spy
    result = await cto_agent.get_cto_result(message, None, use_rag=False, audit=False, user_id="style-check", reader=False, memory=False)
    # What the CEO reads first: the chat_reply, or (when the model gave none) the full recommendation, exactly as the screen does.
    chat = result.chat_reply or result.reply.split("AUTHORIZATION REQUEST")[0]
    print(f"\n=== {label.upper()}: {message}")
    print(f"stop_reason={spy.stops[-1] if spy.stops else '?'} | chat_reply {len(chat)} chars | reasoning steps {len(result.reasoning_chain)} | "
          f"plan present: {bool(result.authorization_request and result.authorization_request.get('execution_plan'))}")
    structured = label != "simple"
    checks = {
        "not refused": "refusal" not in spy.stops,
        "has a chat_reply": bool(chat),
        "no gatekeeper wording": not GATEKEEPER.search(chat) and not GATEKEEPER.search(result.reply.split("AUTHORIZATION REQUEST")[0]),
        "claims no action": not claims_action(chat),
        # If the model said a plan is ready but code dropped it, the correction must come FIRST (before the promise) in what the CEO reads.
        "a dropped plan is corrected first": ("prepared the plan" not in chat.lower() or bool((result.authorization_request or {}).get("execution_plan"))
                                              or chat.lstrip().startswith("**One important correction:**")),
    }
    if structured:
        checks.update({"headings": bool(HEADING.search(chat)), "bold": bool(BOLD.search(chat)), "explains why": bool(WHY.search(chat))})
        if label in ("guide", "broken"):
            checks["numbered steps"] = bool(NUMBERED.search(chat))
        if label == "broken":
            checks["recommends one option"] = bool(RECOMMEND.search(chat))
    for name, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    print("--- chat_reply ---\n" + chat + "\n------------------")
    return all(checks.values())


async def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    results = {}
    for label, message in SCENARIOS.items():
        results[label] = await one(label, message)
    print("\nSUMMARY: " + ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in results.items()))


if __name__ == "__main__":
    asyncio.run(main())
