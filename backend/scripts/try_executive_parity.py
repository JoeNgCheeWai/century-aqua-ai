"""
Live check of the CAO, COO and CMO against the REAL model (needs ANTHROPIC_API_KEY; no database, no GitHub, no images).

    cd backend; python scripts/try_executive_parity.py

Mocks can never refuse a prompt (a refusal once took the Boardroom down), and a scripted model cannot show whether a prompt WORKS. This runs
real questions through each executive and reports, for each one, what the MODEL itself wrote BEFORE the language guard, so a prompt that does
not work is not hidden by the code that cleans up after it:

  CAO   "Fish are gulping at the surface at dawn in pond 3. What do I do?"        must lead with the risk and the evidence, give the protocol
                                                                                   with doses or thresholds and timings, never "consult a vet"
  CAO   "Dead fish are piling up in pond 3 with red sores. What now?"             the STATUTORY CASE: it must give the immediate containment
                                                                                   steps AND say a vet and a report to the authority are needed
  COO   "Feed delivery is a day late and I have two staff. Plan the week."        must lead with the constraint, give a schedule and a cost
  CMO   "Write a LinkedIn post announcing our new nursery water-treatment."        must lead with audience and message and WRITE the copy

Each is checked mechanically for: not refused, structure (headings, numbered steps, bold), the why, an option with a recommendation, no
hand-off, no offer to do it later, and the concrete artefact. These checks cannot judge whether the advice is good: a person must read it.
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
os.environ.pop("GOVERNANCE_DATABASE_URL", None)  # never touch a database from this script

from app.agents import cao_agent, cmo_agent, coo_agent, guard_rules, language_guard  # noqa: E402

AGENTS = {"cao": (cao_agent, cao_agent.get_cao_result), "coo": (coo_agent, coo_agent.get_coo_result), "cmo": (cmo_agent, cmo_agent.get_cmo_result)}
SCENARIOS = [
    ("cao", "gulping", "Fish are gulping at the surface at dawn in pond 3. What do I do?"),
    ("cao", "statutory", "Dead fish are piling up in pond 3 and some have red sores. What now?"),
    ("coo", "week", "The feed delivery is a day late and I only have two staff on shift this week. Plan the week."),
    ("cmo", "post", "Write a LinkedIn post announcing our new nursery water-treatment product for farm owners."),
]
HEADING = re.compile(r"(?m)^#{2,4} \S")
NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+\S")
BOLD = re.compile(r"\*\*[^*\n]+\*\*")
WHY = re.compile(r"\b(because|this matters|why this|the reason|so that)\b|^#{2,4}\s*why\b", re.IGNORECASE | re.MULTILINE)
RECOMMEND = re.compile(r"(?i)\b(i recommend|my recommendation|i suggest|i would (suggest|choose|go with))\b")
ROBOTIC = re.compile(r"(?i)escalate to human|waiting for your decision|pending human approval|a human (must|should) (decide|confirm|review|approve)")
DOSE = re.compile(r"(?i)\d[\d.,]*\s?(?:mg/l|ppm|%|g/m3|kg|hours?|hrs?|minutes?|mins?|days?|am|pm)\b|\b\d{1,2}:\d{2}\b")
MONEY = re.compile(r"(?i)(?:usd|sgd|s\$|\$|\u20ac)\s?\d|\d[\d,.]*\s?(?:usd|sgd|dollars)")
# A schedule says WHEN: a weekday, a day number, a time of day, or an hour or day count attached to a step. (The first version looked only for
# weekday names, and failed a plan that said "first hour", "each day" and "delivery day".)
SCHEDULE = re.compile(r"(?i)\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b|\bday\s?\d\b|\b(first hour|each day|every day|each morning|morning|afternoon|today|tomorrow|delivery day|within \d+ (?:hours?|days?))\b|\b\d{1,2}:\d{2}\b")
LAW = re.compile(r"(?i)\b(notifiable|reportable|report(?:ed|ing)?\b[^.\n]{0,40}\b(?:authorit|regulator)|authorit(?:y|ies)|by law|statutory|regulator)\b")
VET = re.compile(r"(?i)\b(vet|veterinar)\w*")
CONTAIN = re.compile(r"(?i)\b(isolate|quarantine|stop (?:feeding|moving)|do not move|remove (?:the )?dead|biosecurity|disinfect|sample|aerat)\w*")
HAND_OFF = {role: guard_rules.PATTERNS[role] for role in ("cao", "coo", "cmo")}


class Spy:
    """Wraps the real client so a refusal cannot hide, and keeps what the MODEL wrote before the guard."""

    def __init__(self, real):
        self.real, self.messages, self.stops, self.raw = real, self, [], ""

    async def create(self, **kwargs):
        reply = await self.real.messages.create(**kwargs)
        self.stops.append(getattr(reply, "stop_reason", None))
        self.raw = "".join(b.text for b in reply.content if getattr(b, "type", None) == "text")
        return reply


async def one(role: str, label: str, message: str) -> bool:
    module, call = AGENTS[role]
    spy = Spy(module._get_client())
    module._get_client = lambda: spy
    extra = {"images": False} if role == "cmo" else {}
    result = await call(message, None, use_rag=False, audit=False, user_id="parity-check", memory=False, **extra)
    chat = result.chat_reply or result.reply.split("AUTHORIZATION REQUEST")[0]
    raw = spy.raw
    print(f"\n=== {role.upper()} / {label}: {message}\nstop_reason={spy.stops[-1] if spy.stops else '?'} | chat_reply {len(chat)} chars | reasoning steps {len(result.reasoning_chain)}")
    raw_robotic, raw_handoff = bool(ROBOTIC.search(raw)), bool(HAND_OFF[role].search(raw))
    raw_offer = any(guard_rules.is_offer(s) for s in language_guard.split_sentences(raw))
    print(f"  what the MODEL itself wrote (before the guard): robotic wording={raw_robotic}, a hand-off={raw_handoff}, an offer to do it later={raw_offer}")
    checks = {
        "not refused": "refusal" not in spy.stops, "has a chat_reply": bool(result.chat_reply),
        "headings": bool(HEADING.search(chat)), "numbered steps": bool(NUMBERED.search(chat)), "bold": bool(BOLD.search(chat)), "explains why": bool(WHY.search(chat)),
        "recommends one option": bool(RECOMMEND.search(chat)),
        "no robotic wording reaches the CEO": not ROBOTIC.search(chat), "no hand-off reaches the CEO": not HAND_OFF[role].search(chat) or label == "statutory",
        "model needed no guard (robotic)": not raw_robotic, "model needed no guard (hand-off)": not raw_handoff or label == "statutory", "model needed no guard (offer)": not raw_offer,
    }
    if role == "cao" and label == "gulping":
        checks["gives doses, thresholds or timings"] = bool(DOSE.search(chat))
        checks["does not just send them to a vet"] = len(NUMBERED.findall(chat)) >= 3
    if role == "cao" and label == "statutory":
        checks["gives immediate containment steps"] = bool(CONTAIN.search(chat))
        checks["says a vet is needed (the real requirement)"] = bool(VET.search(chat))
        checks["says it must be reported (the real requirement)"] = bool(LAW.search(chat))
    if role == "coo":
        checks["gives a schedule"] = bool(SCHEDULE.search(chat))
        checks["gives a cost"] = bool(MONEY.search(chat))
    if role == "cmo":
        # The copy is shown in a code block or in backticks (the protocol asks for `backticks` around text to copy), so any span or block of real
        # length counts. (The first version of this check wanted a block or two bold lines and failed a post that was written in backticks.)
        spans = re.findall(r"`([^`\n]{40,})`", chat) + re.findall(r"```[\s\S]{40,}?```", chat)
        checks["writes actual copy (a long code span or block)"] = bool(spans) or len(BOLD.findall(chat)) >= 2
    for name, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    print("--- what the CEO reads ---\n" + chat[:2600] + "\n--------------------------")
    return all(checks.values())


async def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    wanted = set(sys.argv[1:])  # optional: python scripts/try_executive_parity.py coo cmo   (or role/label), to run only some
    chosen = [s for s in SCENARIOS if not wanted or s[0] in wanted or f"{s[0]}/{s[1]}" in wanted]
    results = {f"{role}/{label}": await one(role, label, message) for role, label, message in chosen}
    print("\nSUMMARY: " + ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in results.items()))
    print("(The 'model needed no guard' lines show whether the PROMPT works on its own. A FAIL there with the others ok means the code caught it.)")


if __name__ == "__main__":
    asyncio.run(main())
