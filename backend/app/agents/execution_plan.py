"""
The execution plan: what the CTO or CMO would have written to the repository, as a pull request, once a human approves
(Phase 6.6). This module is the VALIDATOR. The proposal is checked here when the agent writes it (so the human sees only a
sound plan) and again, from the stored copy, immediately before anything is executed (defence in depth: the database copy
is never trusted on its own).

    {"branch_name": "agent/short-description",
     "files_to_update": [{"path": "frontend/src/...", "content": "<the COMPLETE new file>", "commit_message": "..."}],
     "pr_title": "...", "pr_body": "..."}

ENFORCED HERE IN CODE, not left to the model:
  * ALL OR NOTHING. If any part is invalid the whole plan is dropped (with a finding). A plan with some files silently
    removed would open a pull request that is missing pieces, which is worse than no pull request.
  * The branch always lives under ``agent/``, so an agent can never name ``main`` or any other branch. A name without the
    prefix has it added.
  * Paths are relative and clean: no absolute paths, no ``..``, no backslashes, no hidden control characters.
  * GUARDRAIL FILES ARE OFF LIMITS (PROTECTED_PREFIXES): the CI workflows, environment and key files, the governance and
    authorization code, the tool router itself and the auth lock. An agent must not be able to rewrite the rules that
    constrain it. This list is a guard against an agent editing its own cage, NOT a security boundary against a reviewer
    who approves without reading: the pull request review is the real control.
  * Size limits (files, bytes per file, bytes in total), and content that looks like a secret (a token or a private key)
    is refused.

What this does NOT do: it does not check that the code is correct, that it builds, or that it is safe. It does not know
what the file contains today: the tool OVERWRITES a whole file, so a plan that updates a file the agent never saw replaces
it with a guess. The human reads the pull request diff, and CI checks the build, before anything is merged.
"""

from __future__ import annotations

import re
from typing import Any

BRANCH_PREFIX = "agent/"
MAX_BRANCH_CHARS = 80
MAX_FILES = 8
MAX_FILE_BYTES = 40_000
MAX_TOTAL_BYTES = 100_000
MAX_PATH_CHARS = 200
MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 8_000
MAX_COMMIT_CHARS = 200

# Files and folders an agent may never write. Compared case-insensitively against the whole path (a prefix match, so a
# folder name ends in "/").
PROTECTED_PREFIXES = (
    ".github/", ".git/", ".vercel/", "node_modules/",
    "backend/app/governance/",
    "backend/app/agents/tools.py", "backend/app/agents/execution_plan.py", "backend/app/agents/base_constitution.py",
    "backend/app/agents/cto_authorization.py", "backend/app/agents/cmo_authorization.py",
    "backend/app/api/authorization.py", "backend/app/api/security.py",
    "frontend/src/proxy.ts", "frontend/src/lib/mc-auth.ts", "frontend/src/lib/mc-gate.ts",
    "frontend/src/lib/mc-session.ts", "frontend/src/lib/mc-backend.ts",
    "render.yaml", "vercel.json", "backend/dockerfile",
)
# File NAMES (any folder) that hold secrets or keys.
_PROTECTED_NAME = re.compile(r"(?i)(^\.env|\.pem$|\.key$|^id_rsa|^id_ed25519|^\.npmrc$|^\.netrc$|credentials)")

_SECRET = re.compile(
    r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{32,}"
    r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_SLUG = re.compile(r"[^a-z0-9._/-]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class PlanError(ValueError):
    """The plan (or one part of it) is not acceptable. The message is safe to show a human."""


def clean_branch(value: Any) -> str:
    """The branch name made safe and put under ``agent/``. Raises PlanError if nothing usable is left."""
    if not isinstance(value, str) or not value.strip():
        raise PlanError("branch_name is missing")
    name = _SLUG.sub("-", value.strip().lower()).strip("/-.")
    name = re.sub(r"/{2,}", "/", name)
    if name.startswith(BRANCH_PREFIX):
        name = name[len(BRANCH_PREFIX):]
    name = name.strip("/-.")
    if not name or ".." in name or name.endswith(".lock"):
        raise PlanError("branch_name has no usable characters")
    full = BRANCH_PREFIX + name
    if len(full) > MAX_BRANCH_CHARS:
        raise PlanError(f"branch_name is longer than {MAX_BRANCH_CHARS} characters")
    return full


def check_path(path: Any) -> str:
    """A clean repository-relative path, or PlanError. Also used by the file tool, so the rules cannot be bypassed."""
    if not isinstance(path, str) or not path.strip():
        raise PlanError("a file has no path")
    text = path.strip()
    if len(text) > MAX_PATH_CHARS:
        raise PlanError(f"path is longer than {MAX_PATH_CHARS} characters: {text[:40]}...")
    if _CONTROL.search(text) or "\\" in text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise PlanError(f"path is not a clean relative path: {text[:60]}")
    parts = text.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PlanError(f"path is not a clean relative path: {text[:60]}")
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in PROTECTED_PREFIXES) or _PROTECTED_NAME.search(parts[-1]):
        raise PlanError(f"{text} is a protected file that an agent may not write")
    return text


def check_content(path: str, content: Any) -> str:
    """The file text, or PlanError (not text, too big, binary, or it looks like a secret)."""
    if not isinstance(content, str):
        raise PlanError(f"{path}: content must be the file's text")
    if "\x00" in content:
        raise PlanError(f"{path}: binary content is not allowed")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise PlanError(f"{path}: larger than {MAX_FILE_BYTES // 1000} KB")
    if _SECRET.search(content):
        raise PlanError(f"{path}: the content looks like it contains a secret or a key")
    return content


def _line(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def normalize_execution_plan(raw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """
    Validate a plan. Returns (plan or None, findings). None with no findings means the agent sent no plan; None with a
    finding means one was sent and refused, and the finding says why. Never raises.
    """
    if raw is None:
        return None, []
    try:
        if not isinstance(raw, dict):
            raise PlanError("execution_plan was not an object")
        branch = clean_branch(raw.get("branch_name"))
        title = _line(raw.get("pr_title"), MAX_TITLE_CHARS)
        if not title:
            raise PlanError("pr_title is missing")
        body = raw.get("pr_body")
        body = body.strip()[:MAX_BODY_CHARS] if isinstance(body, str) else ""
        items = raw.get("files_to_update")
        if not isinstance(items, list) or not items:
            raise PlanError("files_to_update is empty")
        if len(items) > MAX_FILES:
            raise PlanError(f"more than {MAX_FILES} files")
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        total = 0
        for item in items:
            if not isinstance(item, dict):
                raise PlanError("a files_to_update entry was not an object")
            path = check_path(item.get("path"))
            if path.lower() in seen:
                raise PlanError(f"{path} appears twice")
            seen.add(path.lower())
            content = check_content(path, item.get("content"))
            total += len(content.encode("utf-8"))
            message = _line(item.get("commit_message"), MAX_COMMIT_CHARS) or f"Update {path}"
            files.append({"path": path, "content": content, "commit_message": message})
        if total > MAX_TOTAL_BYTES:
            raise PlanError(f"the files total more than {MAX_TOTAL_BYTES // 1000} KB")
        return {"branch_name": branch, "files_to_update": files, "pr_title": title, "pr_body": body}, []
    except PlanError as exc:
        return None, [f"execution_plan refused and dropped: {exc}"]
    except Exception as exc:  # never raise out of a validator
        return None, [f"execution_plan refused and dropped: unreadable ({type(exc).__name__})"]


def plan_text(plan: dict[str, Any] | None) -> str:
    """Everything the plan would write, as one string, so the risk-floor patterns can read it."""
    if not plan:
        return ""
    return "\n".join([plan["branch_name"], *(f"{f['path']}\n{f['content']}" for f in plan["files_to_update"])])


_RISK_ORDER = ("low", "medium", "high", "critical")
# Paths whose change reaches production or the database when merged: dependencies, the database schema and migrations,
# and anything in the backend. A plan touching one is at least HIGH, whatever the agent labels it.
_HIGH_PATH = re.compile(r"(?i)(^backend/|(^|/)package(-lock)?\.json$|(^|/)prisma/|(^|/)requirements.*\.txt$|(^|/)next\.config\.|(^|/)tsconfig)")


def plan_risk_floor(plan: dict[str, Any] | None) -> str:
    """
    The lowest risk level a plan allows. Any plan writes files, so it is at least MEDIUM; a plan touching the backend,
    dependencies, the database schema or the build configuration is at least HIGH. The floor only RAISES a level. It reads
    the PATHS, not the code: it cannot tell whether the code inside a harmless-looking file is harmless.
    """
    if not plan:
        return "low"
    return "high" if any(_HIGH_PATH.search(f["path"]) for f in plan["files_to_update"]) else "medium"


def higher_risk(a: str, b: str) -> str:
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


def describe(plan: dict[str, Any] | None) -> str:
    """One line for the stored response text."""
    if not plan:
        return ""
    n = len(plan["files_to_update"])
    return f"Approving would open a pull request from branch {plan['branch_name']} with {n} file{'s' if n != 1 else ''}. It is never merged automatically."

