"""
"SAFE TO IMPLEMENT": decided by CODE, never by a model.

The CEO asked for minor, safe optimizations to stop reaching them and go to the COO. A model must not decide what counts as "safe":
if the CTO labelled its own task safe and the COO (also a model) agreed, nothing deterministic would sit between a bad idea and a pull
request. So the classification here is a short ALLOW-LIST. Anything that does not clearly fit goes to the CEO exactly as before. It
FAILS CLOSED: when in doubt, not safe.

A plan is SAFE only if ALL of these hold:
  1. it has an execution_plan that passed the plan rules, and the request has NO commands (a command is never "minor");
  2. at most MAX_FILES files and MAX_FILE_BYTES per file (a small change);
  3. every path is on the allow-list, by kind:
       * a UI component under frontend/src/components/ (not a page, a route or a library);
       * a prompt text file under backend/app/prompts/ (wording only; the self-check refuses the wording the model has refused);
       * a test under backend/tests/ or frontend/scripts/, or a document (.md);
       * a STRICTLY ADDITIVE database migration (a new prisma/migrations folder whose SQL only adds) with its schema change;
  4. the request's risk level is low or medium (or high ONLY because the paths force it: see the comment in _classify), never critical,
     and the plan self-check (plan_checks) passed;
  5. nothing touches a login, key, secret, dependency, build or deploy file, or a file the plan rules protect.

What this does NOT do: it reads PATHS, sizes, the migration text and a short list of risky code (calls to the admin or approval endpoints,
cookies and storage, dynamic code, network calls). It cannot tell that a prompt edit changes behaviour badly, or that a harmless-looking
component is wrong in some way the list does not name. That is why the COO still judges it, why the pull request is still reviewed and merged by a human
(main is protected and CI runs), and why the feature ships OFF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agents.execution_plan import plan_risk_floor

MAX_FILES = 3
MAX_FILE_BYTES = 12_000
MAX_TOTAL_BYTES = 24_000

_UI_COMPONENT = re.compile(r"(?i)^frontend/src/components/[\w./-]+\.(tsx|ts|css)$")
_PROMPT = re.compile(r"(?i)^backend/app/prompts/[\w.-]+\.py$")
_TEST = re.compile(r"(?i)^(backend/tests/[\w.-]+\.py|frontend/scripts/[\w.-]+\.(mjs|js))$")
_DOC = re.compile(r"(?i)^[\w./-]+\.md$")
_MIGRATION = re.compile(r"(?i)^frontend/prisma/migrations/\d{14}_[a-z0-9_]+/migration\.sql$")
_SCHEMA = "frontend/prisma/schema.prisma"
# Never safe, whatever else is true: access, secrets, dependencies, build and deploy, and the guardrails themselves.
_NEVER = re.compile(
    r"(?i)(^|/)(package(-lock)?\.json|requirements[\w.-]*\.txt|dockerfile|next\.config\.\w+|tsconfig[\w.-]*\.json|render\.yaml|vercel\.json)$"
    r"|(^|/)(\.github|\.vercel|governance)/|(^|/)middleware\.ts$|(^|/)proxy\.ts$|(^|/)\.env|auth|login|secret|credential|clerk"
)
# "auth" is deliberately a substring: it also holds back the approval screens (AuthorizationDetail.tsx), because a change that hides a risk
# level from the person approving is not minor. "session" is NOT here: it blocked harmless Boardroom components such as SessionsBadge.tsx (a live
# run caught that), and the session LOGIC lives in frontend/src/lib/, which is not on the allow-list at all.


# A CONTENT screen on top of the path rules. A path cannot tell a display-only badge from a button that resets an agent, and a live run showed
# exactly that case sailing through as "UI fix" (the COO caught it, but a model must not be the only layer). A minor change does not call the
# admin or approval endpoints, read cookies or storage, run dynamic code, inject raw HTML, read environment values or send data anywhere. A real
# component that needs one of these is a decision for a person, which costs nothing.
_RISKY_CONTENT = re.compile(
    r"/api/(admin|authorization|council|audit)|document\.cookie|localStorage|sessionStorage|\beval\s*\(|new\s+Function\s*\(|dangerouslySetInnerHTML"
    r"|process\.env|\bXMLHttpRequest\b|\bsendBeacon\b|\bfetch\s*\(|\bWebSocket\b|\bimport\s*\(|child_process|\bsubprocess\b|\bos\.system\b|\bexec\s*\(",
    re.IGNORECASE,
)
_CODE_FILE = re.compile(r"(?i)\.(tsx?|jsx?|mjs|py|css)$")


@dataclass
class Verdict:
    safe: bool
    kind: str = ""  # a short label of what the change is, when safe
    reasons: list[str] = field(default_factory=list)  # why it is NOT safe, or what made it safe (plain words)


def kind_of(path: str) -> str | None:
    """The allow-list kind of a path, or None when the path is not on the list."""
    if _NEVER.search(path):
        return None
    if _MIGRATION.search(path) or path.lower() == _SCHEMA:
        return "database addition"
    if _UI_COMPONENT.search(path):
        return "UI fix"
    if _PROMPT.search(path):
        return "prompt wording"
    if _TEST.search(path):
        return "test"
    if _DOC.search(path):
        return "document"
    return None


def classify(request: dict[str, Any] | None, self_check_passed: bool) -> Verdict:
    """Decide whether this authorization_request is 'Safe to Implement'. Never raises; any surprise is 'not safe'."""
    try:
        return _classify(request, self_check_passed)
    except Exception as exc:  # the classifier must never open a door by crashing
        return Verdict(False, "", [f"the safety check could not run ({type(exc).__name__}), so a person must decide"])


def _classify(request: dict[str, Any] | None, self_check_passed: bool) -> Verdict:
    if not isinstance(request, dict):
        return Verdict(False, "", ["there is no request"])
    plan = request.get("execution_plan")
    if not isinstance(plan, dict) or not plan.get("files_to_update"):
        return Verdict(False, "", ["it has no execution plan, so there is nothing a pull request could safely contain"])
    why: list[str] = []
    # The CTO's steps are proposed_commands and the CMO's are deployment_commands: either one is a command, and a command is never minor.
    if request.get("proposed_commands") or request.get("deployment_commands"):
        why.append("it proposes commands or steps to run, and a command is never a minor change")
    # The risk level has a floor set by the PATHS (execution_plan.plan_risk_floor): anything under backend/ or prisma/ is at least HIGH, whatever
    # the agent says, and those are exactly the kinds a prompt tweak or a database addition live in. So HIGH is accepted only when the paths alone
    # explain it (the floor is high too). A HIGH the agent chose for a UI file, or any CRITICAL, is the agent flagging a worry: a person decides.
    risk = request.get("risk_level")
    if risk not in ("low", "medium", "high") or (risk == "high" and plan_risk_floor(plan) != "high"):
        why.append(f"its risk level is {risk!s}, which a minor change does not carry")
    if not self_check_passed:
        why.append("the self-check on the plan did not pass")
    files = plan["files_to_update"]
    if len(files) > MAX_FILES:
        why.append(f"it changes {len(files)} files (the limit for a minor change is {MAX_FILES})")
    kinds: list[str] = []
    total = 0
    for item in files:
        path, content = str(item.get("path", "")), str(item.get("content", ""))
        size = len(content.encode("utf-8"))
        total += size
        kind = kind_of(path)
        if kind is None:
            why.append(f"{path} is not on the list of safe kinds of file")
        else:
            kinds.append(kind)
        if size > MAX_FILE_BYTES:
            why.append(f"{path} is {size:,} bytes, which is more than a minor change")
        # Tests and documents may name these things (a test about the admin endpoint is fine); code that RUNS in the product may not use them.
        if kind in ("UI fix", "prompt wording") and _CODE_FILE.search(path) and (hit := _RISKY_CONTENT.search(content)):
            why.append(f"{path} uses '{hit.group(0).strip()}', which is not a minor change (it reaches an endpoint, storage or dynamic code)")
    if total > MAX_TOTAL_BYTES:
        why.append(f"the files total {total:,} bytes, which is more than a minor change")
    has_schema = any(str(i.get("path", "")).lower() == _SCHEMA for i in files)
    has_migration = any(_MIGRATION.search(str(i.get("path", ""))) for i in files)
    if has_schema and not has_migration:
        why.append("a schema change without its migration folder")
    if has_migration and not has_schema:
        why.append("a migration without the matching schema change")
    if why:
        return Verdict(False, "", why)
    unique = list(dict.fromkeys(kinds))
    detail = f"{len(files)} small file(s), all on the safe list ({', '.join(unique)}); no commands; risk {request.get('risk_level')}"
    return Verdict(True, " + ".join(unique), [detail])
