"""
WHEN A CTO TASK MUST GO TO THE CEO: the code floor under the COO's approval authority (management by exception).

The COO may approve routine CTO tasks (coo_tools.review_cto_task), but the model is never the only gate. This module reads the STORED task and
lists every reason it is the CEO's, whatever the COO says. If there is any reason, an approval is refused and the task is escalated instead.

THE CEO'S ESCALATION LIST, enforced here as far as code can tell:
  * a database schema or migration change,
  * a security or authentication change,
  * a new third-party integration or dependency,
  * a change to financial logic,
  * a change to governance rules (the approval flow, the constitution, these very checks),
  * and anything that is not fully reported, failed its self-check, is rated critical, or carries commands for a person to run.

HOW IT DECIDES. Mostly from the file PATHS in the plan, which are reliable, and a little from the plain words of the summary, impact and pull
request text. It does NOT read the code inside a file: it cannot tell whether code in a harmless-looking file is harmless. A false alarm sends
a routine task to the CEO, which is the safe direction; a miss is why the result of an approval is only a pull request that a person merges.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents import coo_review, plan_checks

MAX_REASONS = 8

# (label, pattern) matched against each file path in the plan.
PATH_RULES = (
    ("a database schema or migration file", re.compile(r"(?i)(^|/)prisma/|\.sql$|(^|/)migrations?/")),
    ("a security or authentication file", re.compile(r"(?i)(auth|security|middleware|proxy|clerk|login|password|credential|secret|permission|\.env|mc-(?:gate|session|backend))")),
    ("a governance or approval-rule file", re.compile(
        r"(?i)(governance|constitution|approval|authoriz|safe_to_implement|coo_review|coo_tools|coo_escalation|plan_checks|exec_tools|exec_checks|"
        r"exec_requests|execution_plan|language_guard|kill_?switch|(^|/)\.github/)")),
    ("a dependency, build or deployment file", re.compile(
        r"(?i)((^|/)requirements[^/]*\.txt$|(^|/)package(?:-lock)?\.json$|(^|/)(?:render\.yaml|vercel\.json|dockerfile|pyproject\.toml|pnpm-lock\.yaml|yarn\.lock)$)")),
    ("a billing, payment or rate-limit file", re.compile(r"(?i)(billing|payment|invoice|pricing|stripe|subscription|checkout|rate[-_]?limit)")),
)

# (label, pattern) matched against the task's own words: its summary, impact, and pull request title and body. Kept to high-signal phrases on
# purpose. A broad word such as "authorization" or "migration" would also catch a routine task that merely MENTIONS it ("no migration needed",
# or a fix to the authorization loop itself), which the COO is meant to approve. The PATH rules above already catch real migration files.
# Matching words cannot read negation, so "does not touch credentials" still escalates: a false alarm, the safe direction.
TEXT_RULES = (
    ("it changes or removes database structure", re.compile(
        r"(?i)\b(?:drop|truncate)\s+(?:table|column|database|schema)\b|\bdelete\s+from\b|\balter\s+table\b|"
        r"\b(?:restructur\w*|rework\w*|redesign\w*)\s+(?:of\s+)?(?:the\s+)?(?:database|schema|tables?)\b")),
    ("it touches authentication, credentials or encryption", re.compile(
        r"(?i)\b(?:authentication|credentials?|passwords?|encrypt\w*|csrf|cors|session\s+cookies?|api[ _-]?keys?)\b")),
    ("it adds a third-party service or dependency", re.compile(
        r"(?i)\b(?:third[- ]party|webhooks?|oauth|new\s+(?:api|integration|vendor|provider|sdk|dependency|package|library|service))\b")),
    ("it changes financial logic", re.compile(r"(?i)\b(?:billing|payments?|invoic\w*|pricing|refunds?|stripe|subscriptions?|token\s+balance)\b")),
    ("it changes governance rules", re.compile(r"(?i)\b(?:governance|constitution\w*|kill[- ]switch)\b")),
)


def _text(request: dict[str, Any], plan: dict[str, Any] | None) -> str:
    parts = [request.get("action_summary"), request.get("impact"), request.get("plan_summary"), (plan or {}).get("pr_title"), (plan or {}).get("pr_body")]
    return " ".join(str(p) for p in parts if p)


def escalation_reasons(request: dict[str, Any]) -> list[str]:
    """
    Every reason this stored authorization request is not the COO's to approve, in plain words ([] means none was found): the CRITICAL topics first
    (critical_reasons), then the completeness problems (completeness_reasons). Pure: no database, no network.
    NEVER RAISES for a malformed request: a request that cannot be read is itself a reason.
    """
    return list(dict.fromkeys([*critical_reasons(request), *completeness_reasons(request)]))[:MAX_REASONS]


def critical_reasons(request: dict[str, Any]) -> list[str]:
    """
    The reasons a task is CRITICAL, so it is the CEO's decision and the CEO is notified: critical risk, commands for a person to run, and the CEO's
    list (database schema, security or authentication, governance rules, dependencies or deployment, billing, a new third-party service). Decided
    from the file PATHS and the plain words of the task, never from the code inside a file. NEVER RAISES: an unreadable request is critical.
    """
    try:
        return _critical(request)
    except Exception as exc:  # a checker that crashes must never wave a task through
        return [f"the task could not be checked ({type(exc).__name__})"]


def completeness_reasons(request: dict[str, Any]) -> list[str]:
    """
    Why a task is not FULLY REPORTED or failed its code self-check. These send a task back; they are not critical, so the CEO is not notified for them.
    A plan submitted through submit_plan_for_approval carries its own plan_summary instead of the three-part plain summary, so that is what is checked.
    """
    try:
        return _completeness(request)
    except Exception as exc:
        return [f"the task could not be checked ({type(exc).__name__})"]


def _plan(request: dict[str, Any]) -> dict[str, Any] | None:
    return request.get("execution_plan") if isinstance(request.get("execution_plan"), dict) else None


def _critical(request: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if str(request.get("risk_level")) == "critical":
        reasons.append("it is rated critical risk")
    if request.get("proposed_commands") or request.get("deployment_commands"):  # the CTO names them proposed_commands, the CMO deployment_commands
        reasons.append("it carries commands for a person to run")
    plan = _plan(request)
    paths = [str(i.get("path", "")) for i in (plan or {}).get("files_to_update", []) if isinstance(i, dict)]
    paths += [str(p) for p in request.get("files_to_change") or [] if isinstance(p, str)]  # a plan submitted through the tool names its files here
    for path in paths:
        reasons.extend(f"it changes {label} ({path})" for label, pattern in PATH_RULES if pattern.search(path))
    text = _text(request, plan)
    reasons.extend(label for label, pattern in TEXT_RULES if pattern.search(text))
    return list(dict.fromkeys(reasons))[:MAX_REASONS]


def _completeness(request: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    plan = _plan(request)
    self_check_passed = True
    if plan is not None:
        check = plan_checks.check_plan(plan, None)
        self_check_passed = check.passed
        if not check.passed:
            reasons.append("its plan failed the code self-check: " + "; ".join(check.failures[:2]))
    if request.get("submitted_via"):  # a tool-submitted plan: its plan_summary is the plain-language report
        if not str(request.get("plan_summary") or "").strip():
            reasons.append("it has no plan summary")
        if not str(request.get("rollback_plan") or "").strip():
            reasons.append("it has no rollback plan")
        if not str(request.get("impact") or "").strip():
            reasons.append("it does not say what is affected")
    else:
        reasons.extend(coo_review.reported_problems(request, self_check_passed))
    return list(dict.fromkeys(reasons))[:MAX_REASONS]
