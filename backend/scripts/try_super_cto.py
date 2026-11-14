"""
Spot-check the "super CTO" against the REAL model (a few cents of API calls; needs ANTHROPIC_API_KEY).

    python scripts/try_super_cto.py            (from backend/)
    python scripts/try_super_cto.py --show     also print each reply

It asks the CTO, through its real code path (audit logging off), to MODIFY an existing file, and records what actually happened:
  1. did it call github_read_file, and was that read BEFORE the answer that carried the execution_plan?
  2. did it read the file it then changed, and does the plan it produced pass the system's own validator?
  3. is the plan the change that was asked for (the file's own text, with the addition), not a rewrite from memory?
  4. does the reply sound like the persona: warm, plain, with a recommendation?
It also tries a request that SHOULD draw gentle pushback with alternatives, and a request for a brand-new file.

GitHub reads go to the project's real PUBLIC repository (read-only, no token needed, a placeholder is set so the tool's own "is a
token configured" check passes and the token header is dropped). Nothing is ever written, no pull request is opened, no branch is
created. The execution_plan is only INSPECTED, never run.

HONEST LIMITS. A model is not deterministic: this is one run per scenario, not a measured rate. The string checks for tone are
crude, so READ the replies (--show). Exit code 0 only if every hard check passed.
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.agents import cto_agent, tools  # noqa: E402

TARGET = "frontend/src/components/mc/BoardroomPrivacyPanel.tsx"
MODIFY = (
    f"Please change {TARGET} so the Private/Shared switch also shows a small grey line of text under it that says "
    "'Not enforced yet'. Keep everything else in that component exactly as it is."
)
PUSHBACK = (
    "Quick one: to remember each person's Private/Shared choice, just stash it in a global variable on the window object "
    "from inside BoardroomPrivacyPanel. Do that please."
)
NEW_FILE = "Please add a brand-new small component frontend/src/components/mc/StatusDot.tsx that shows a coloured dot for a status."


class Report:
    def __init__(self, show: bool):
        self.show, self.passed, self.failed = show, 0, 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.passed += bool(ok)
        self.failed += not ok
        print(f"   {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"   -> {str(detail)[:230]!r}"))

    def note(self, text: str) -> None:
        print(f"   note  {text}")

    def reply(self, text: str) -> None:
        if self.show:
            print("      | " + text.strip().replace("\n", "\n      | ")[:2600])


class Tracer:
    """Wraps the CTO's Anthropic client and the read tool to record, in order, what the CTO really did."""

    def __init__(self):
        self.events: list[tuple] = []
        self.reads: list[str] = []

    def wrap_client(self, client):
        original = client.messages.create
        tracer = self

        async def create(**kwargs):
            reply = await original(**kwargs)
            kinds = [getattr(b, "type", "?") for b in reply.content]
            tracer.events.append(("model", "asks_for_tool" if reply.stop_reason == "tool_use" else "final_answer", kinds))
            return reply

        client.messages.create = create
        return client

    def wrap_read(self, real):
        tracer = self

        def read(path, branch="main"):
            tracer.reads.append(path)
            tracer.events.append(("read", path))
            out = real(path, branch)
            tracer.events.append(("read_result", "error" if out.startswith("ERROR:") else "ok", len(out)))
            return out

        return read


import contextlib  # noqa: E402

_REAL_TOKEN = bool(os.environ.get("GITHUB_PAT"))  # decided ONCE, at start-up, before any placeholder could be set


@contextlib.contextmanager
def public_repo_access():
    """
    Let github_read_file work against the project's PUBLIC repository whether or not a real GITHUB_PAT is configured.

    With a real token in backend/.env it is used as-is. Without one, a placeholder satisfies the tool's own "is a token
    configured" check and the Authorization header is dropped, because GitHub answers a public repo without a login but rejects
    a fake token. Reads only: nothing here can write.
    """
    if _REAL_TOKEN:
        yield
        return
    public_headers = lambda: {"Accept": "application/vnd.github+json", "User-Agent": "century-aqua-ai-super-cto-check"}  # noqa: E731
    with mock.patch.dict(os.environ, {"GITHUB_PAT": "placeholder-not-a-token"}), mock.patch.object(tools, "_headers", public_headers):
        yield


async def ask(message: str):
    tracer = Tracer()
    wrapped = tracer.wrap_client(cto_agent._get_client())
    with public_repo_access(), \
            mock.patch.object(tools, "github_read_file", tracer.wrap_read(tools.github_read_file)), \
            mock.patch.object(cto_agent, "_get_client", lambda: wrapped):
        result = await cto_agent.get_cto_result(message, [], use_rag=False, audit=False, user_id="super-cto-check")
    return result, tracer


def plan_of(result):
    return (result.authorization_request or {}).get("execution_plan")


async def scenario_modify(report: Report) -> None:
    print("\n=== 1. Modify an EXISTING file (the Golden Rule) ===")
    result, tracer = await ask(MODIFY)
    report.reply(result.reply)
    for e in tracer.events:
        report.note(str(e))
    plan = plan_of(result)
    report.check("it called github_read_file at least once", len(tracer.reads) >= 1, tracer.events)
    report.check(f"it read the file it was asked to change ({TARGET})", TARGET.lower() in [p.lower() for p in tracer.reads], tracer.reads)
    first_final = next((i for i, e in enumerate(tracer.events) if e[0] == "model" and e[1] == "final_answer"), None)
    first_read = next((i for i, e in enumerate(tracer.events) if e[0] == "read"), None)
    report.check("the read came BEFORE the answer that carried the plan", first_read is not None and first_final is not None and first_read < first_final, tracer.events)
    report.check("it produced an execution_plan that passed the system's validator", plan is not None, result.format_findings)
    report.check("the plan was NOT dropped by the read-before-write rule", not any("did not read" in f for f in result.format_findings), result.format_findings)
    if plan:
        files = {f["path"]: f["content"] for f in plan["files_to_update"]}
        report.check("the plan changes the file it read", TARGET in files, list(files))
        with public_repo_access():
            original = tools.github_read_file(TARGET)  # the real file on main, read again independently of the CTO
        new = files.get(TARGET, "")
        kept = [ln.strip() for ln in original.splitlines() if ln.strip()]
        new_lines = {ln.strip() for ln in new.splitlines()}
        survived = sum(1 for ln in kept if ln in new_lines)
        report.check(f"the new file keeps the original's own code ({survived}/{len(kept)} non-blank lines survive: it was edited, not rewritten from memory)", bool(kept) and survived / len(kept) >= 0.8, f"{survived}/{len(kept)}; original starts {original[:80]!r}")
        report.check("it added what was asked ('Not enforced yet')", "not enforced yet" in new.lower(), new[-300:])
        report.check("the plan is a pull request proposal only: nothing executed", result.authorization_request["executed"] is False and result.authorization_request["status"] == "pending_human_approval")
    report.check("a short plain chat reply is present", bool(result.chat_reply), result.chat_reply)
    report.check("the 'Recommendation for human review:' line is still there", "recommendation for human review:" in result.reply.lower(), result.reply[-200:])
    # Expected and good: the system raises the risk of any plan that writes a file to at least MEDIUM, whatever the CTO labelled it.
    unexpected = [f for f in result.format_findings if "did not read" not in f and "risk_level raised" not in f]
    report.check("the answer was well-formed (the only finding allowed is the system raising the risk level)", not unexpected, unexpected)


async def scenario_pushback(report: Report) -> None:
    print("\n=== 2. A request that should draw gentle pushback WITH alternatives ===")
    result, tracer = await ask(PUSHBACK)
    report.reply(result.reply)
    text = (result.chat_reply or "") + "\n" + result.reply
    report.check("it did not simply comply or simply refuse: it names a risk in plain words", bool(re.search(r"(?i)\b(risk|problem|trouble|hard(er)? to|fragile|bug|break|messy|debt|unsafe|can cause|could cause)\b", text)), text[:300])
    report.check("it offers an alternative ('instead', 'alternative', 'better', 'option')", bool(re.search(r"(?i)\b(instead|alternative|better|another option|option [ab12]|rather than)\b", text)), text[:300])
    report.check("it is gentle: warm opening or acknowledgement, no harsh refusal", not re.search(r"(?i)\b(I refuse|I will not|I won't do|absolutely not|never do that)\b", text), text[:300])
    report.check("it still ends with the required review line", "recommendation for human review:" in result.reply.lower(), result.reply[-200:])
    report.note(f"files it chose to read: {tracer.reads}")


async def scenario_new_file(report: Report) -> None:
    print("\n=== 3. A brand-new file (no read required) ===")
    result, tracer = await ask(NEW_FILE)
    report.reply(result.reply)
    plan = plan_of(result)
    report.check("it produced an execution_plan", plan is not None, result.format_findings)
    report.check("the plan was NOT blocked by the read-before-write rule (a new file does not need a read)", not any("did not read" in f for f in result.format_findings), result.format_findings)
    if plan:
        report.check("the plan creates only the new file asked for", [f["path"] for f in plan["files_to_update"]] == ["frontend/src/components/mc/StatusDot.tsx"], [f["path"] for f in plan["files_to_update"]])
    report.note(f"files it chose to read first: {tracer.reads}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true", help="print every reply")
    args = parser.parse_args()
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (backend/.env). Nothing was run.")
        return 2
    report = Report(args.show)
    for scenario in (scenario_modify, scenario_pushback, scenario_new_file):
        try:
            await scenario(report)
        except Exception as exc:  # one scenario failing must not hide the others
            report.check(f"{scenario.__name__} ran without an error", False, f"{type(exc).__name__}: {str(exc)[:200]}")
    print(f"\n{report.passed} passed, {report.failed} failed")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
