"""
Live check of the CTO's zero-friction behaviour against the REAL model (needs ANTHROPIC_API_KEY; no database; no GitHub: the file reader is
answered from this working tree, so the CTO can really read a component and draft a plan, with no network and no token).

    cd backend; python scripts/try_cto_zero_friction.py

Mocks can never refuse a prompt and can only show that CODE removes bad words from a badly behaved scripted model. This shows what the real model
now writes on the CEO's three situations, BEFORE and AFTER the language guard, so a prompt that does not work is not hidden by the code that
cleans up after it:
  1. "The ApprovedStrategies table is missing. Help me set up the Learning Loop table."  (must give the exact command, no deflection)
  2. "Please make the toggle label slightly larger."                                       (a routine change: no alarm, ready for approval)
  3. "I ran the migration and got an 'already exists' error."                              (must accept it as success and give the next step)
It prints the stop reason (a REFUSAL is the first thing to look for), the RAW model text and the reply the CEO reads, then checks them. The checks are
mechanical: a person must read the replies.
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

from app.agents import cto_agent, language_guard, tools  # noqa: E402

REPO = BACKEND_DIR.parent


def local_read(file_path, branch="main"):
    """A faithful stand-in for the read-only GitHub reader: the same files, read from this working tree, with no network and no token."""
    path = (REPO / str(file_path)).resolve()
    if REPO not in path.parents or not path.is_file() or path.suffix in (".env", ".pem", ".key"):
        return f"ERROR: File not found: {file_path} (on branch main)"
    return path.read_text(encoding="utf-8", errors="replace")[:60000]


def local_status(file_path):
    path = (REPO / str(file_path)).resolve()
    return "exists" if REPO in path.parents and path.is_file() else "missing"

SCENARIOS = {
    "table": "The ApprovedStrategies table is missing from the database. Help me set up the Learning Loop table.",
    "toggle": "Please make the toggle label on the Boardroom privacy toggle slightly larger.",
    "exists": "I ran the migration and got an 'already exists' error for the ApprovedStrategies table. What now?",
}
ROBOTIC = re.compile(r"(?i)escalate to human|waiting for your decision|pending human approval|a human (must|should) (decide|confirm|review|approve)")
DEFLECT = language_guard.DEFLECTION
CODE_SPAN = re.compile(r"`[^`\n]+`|```")
CONFIRMS = re.compile(r"(?i)(already exists?|(is|was) already (in|there|created|defined)|nothing (more )?to create|good news|(that|the|this) (error|message) (just )?(means|is)|it means)")
DOUBT = re.compile(r"(?i)(\bprove\b|\bproof\b|show me (the|that) table|can you confirm (that )?the table|are you sure)")  # word boundaries: "approved" contains "prove"


class Spy:
    """Wraps the real client so a refusal cannot hide."""

    def __init__(self, real):
        self.real, self.messages, self.stops, self.raw = real, self, [], ""

    async def create(self, **kwargs):
        reply = await self.real.messages.create(**kwargs)
        self.stops.append(getattr(reply, "stop_reason", None))
        self.raw = "".join(b.text for b in reply.content if getattr(b, "type", None) == "text")
        return reply


async def one(label: str, message: str) -> bool:
    spy = Spy(cto_agent._get_client())
    cto_agent._get_client = lambda: spy
    tools.github_read_file, tools.github_file_status = local_read, local_status  # the CTO really reads files, from disk instead of GitHub
    result = await cto_agent.get_cto_result(message, None, use_rag=False, audit=False, user_id="zero-friction-check", reader=True, memory=False)
    chat = result.chat_reply or result.reply.split("AUTHORIZATION REQUEST")[0]
    model_wrote = spy.raw
    print(f"\n=== {label.upper()}: {message}\nstop_reason={spy.stops[-1] if spy.stops else '?'} | chat_reply {len(chat)} chars | request: {bool(result.authorization_request)}")
    raw_robotic, raw_deflect = bool(ROBOTIC.search(model_wrote)), bool(DEFLECT.search(model_wrote))
    print(f"  what the MODEL itself wrote (before the guard): robotic wording={raw_robotic}, hands it to someone else={raw_deflect}")
    checks = {
        "not refused": "refusal" not in spy.stops, "has a reply": bool(chat),
        "no robotic wording reaches the CEO": not ROBOTIC.search(chat) and not ROBOTIC.search(result.reply.split("AUTHORIZATION REQUEST")[0]),
        "never hands it to someone else": not DEFLECT.search(chat),
        "model did not need the guard (robotic)": not raw_robotic, "model did not need the guard (deflection)": not raw_deflect,
    }
    if label in ("table", "exists"):
        checks["gives a copy-pasteable command"] = bool(CODE_SPAN.search(chat))
    if label == "table":
        checks["names the migration command"] = "prisma migrate deploy" in (chat + " ".join((result.authorization_request or {}).get("proposed_commands") or []))
        checks["does not call it a production emergency"] = not re.search(r"(?i)live production database|high-risk|dangerous", chat)
    if label == "exists":
        checks["accepts the error as success"] = bool(CONFIRMS.search(chat))
        checks["says so up front (first 400 characters)"] = bool(CONFIRMS.search(chat[:400]))
        checks["does not ask for proof"] = not DOUBT.search(chat)
    if label == "toggle":
        checks["says it is ready for approval"] = bool(re.search(r"(?i)ready for your (final )?approval", chat))
    for name, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    print("--- what the CEO reads ---\n" + chat[:2400] + "\n--------------------------")
    return all(checks.values())


async def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    results = {label: await one(label, message) for label, message in SCENARIOS.items()}
    print("\nSUMMARY: " + ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in results.items()))
    print("(The two 'model did not need the guard' lines show whether the PROMPT works on its own. A FAIL there with the others ok means the "
          "code caught what the model wrote.)")


if __name__ == "__main__":
    asyncio.run(main())
