"""
Live check of one COMPLETE Boardroom session against the REAL model (about 11 calls for 3 seats; needs ANTHROPIC_API_KEY, no database).

    cd backend; python scripts/try_boardroom_session.py [cao coo cmo ...]

Prints the status, the calls and tokens, then every turn grouped by round: the position, how much private reasoning it wrote, and for the
cross-response round what each agent said to each other agent. It also lists any call the model REFUSED or that was cut off. This is
the check that mocks cannot give: the unit tests use a scripted model, and the first production run of the cross-response upgrade
failed because the real model refused a prompt that every mock accepted.
"""

from __future__ import annotations

import asyncio
import logging
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

QUERY = ("Should we launch the new customer portal this quarter? Pilot budget cap USD 150,000; initial markets Vietnam and Thailand; "
         "the portal lets farms order feed and probiotics and see their water-quality reports.")
ROUND_NAME = {1: "Initial positions", council.CROSS_ROUND: "Cross-response", 2: "Chair's moderation", 3: "Revision", 4: "Second revision",
              5: "Synthesis", 6: "Red team"}


class Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


async def main(agents: list[str]) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    collector = Collect()
    logging.getLogger("app.agents.council").addHandler(collector)
    started = time.perf_counter()
    result = await council.run_council_session(QUERY, agents)
    elapsed = time.perf_counter() - started
    print(f"STATUS {result['status']} in {elapsed:.0f}s | {result['model_calls']} calls | {result['total_tokens']:,} tokens "
          f"(in {result['input_tokens']:,} / out {result['output_tokens']:,}) | seats {result['participating_agents']}")
    if result["escalation_reason"]:
        print(f"REASON: {result['escalation_reason']}")
    for round_no in sorted({t["round"] for t in result["turns"]}, key=lambda r: 1.5 if r == council.CROSS_ROUND else r):
        print(f"\n--- {ROUND_NAME.get(round_no, round_no)}")
        for t in [t for t in result["turns"] if t["round"] == round_no]:
            reasoning = len(t.get("reasoning") or "")
            print(f"  {t['agent_id'].upper()} conf={t['confidence']} tokens={t['tokens_used']} reasoning={reasoning} chars | {t['position'][:150]}")
            for r in t.get("responses") or []:
                print(f"      -> {r['to'].upper()} [{r['verdict']}] {r['adds_or_alternative'][:130]}")
    if result["options"]:
        print("\nOPTIONS: " + " | ".join(f"{o['option_id']}: {o['title']}" for o in result["options"]))
    problems = [line for line in collector.lines if "REFUSED" in line or "no text" in line or "cross-response" in line or "failed" in line]
    print("\nPROBLEMS LOGGED: " + (f"{len(problems)}" if problems else "none"))
    for line in problems:
        print("  - " + line[:200])


if __name__ == "__main__":
    asyncio.run(main([a.lower() for a in sys.argv[1:]] or ["cao", "coo", "cmo"]))
