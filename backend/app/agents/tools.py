"""
The tool router (Phase 6.6): the only place in the backend that can change anything outside the database.

WHAT IT CAN DO. Four tools, nothing else:
    mock_generate_image(prompt)                                  a placeholder image URL (no network, no model)
    github_create_branch(owner, repo, branch_name)               a new branch off the default branch
    github_write_file(owner, repo, branch, path, content, msg)   create or overwrite ONE file on that branch
    github_open_pr(owner, repo, branch, title, body)             open a pull request into the default branch

WHAT IT CAN NEVER DO. There is no merge tool, no delete tool, no force push, no tag or release, no workflow dispatch, no
shell and no way to write to the default branch. A pull request is a proposal: a human merges it (or does not), and CI
(.github/workflows/pr-check.yml) checks that it builds first. So the worst an approved plan can do is open a pull request.

THE SAFETY RULES LIVE HERE, in code, whatever an agent or a caller passes in:
  * THE REPOSITORY IS FIXED BY THE SERVER. Every tool refuses any owner/repo other than ``ALLOWED_REPO`` (GITHUB_OWNER and
    GITHUB_REPO, defaulting to this project), so a prompt-injected plan cannot point the token at someone else's repository.
  * Branches must start with ``agent/`` and may not be the default branch (checked again here, not only in the plan).
  * Paths go through the same protected-file rules as the plan validator (execution_plan.check_path), and content through
    its secret and size checks.
  * The token is read from GITHUB_PAT at call time and never logged, returned or put in an error message.
  * Every tool returns a ToolResult and NEVER raises: a failure is a result with ok=False and a short, safe message.

The tools talk to the GitHub REST API with ``requests`` (a short timeout on every call, no retries: a failed step is
reported, not repeated, so an approval can never be applied twice by accident).
"""

from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, quote_plus

import requests

from app.agents import execution_plan as plan_rules

logger = logging.getLogger(__name__)

API = "https://api.github.com"
TIMEOUT_SECONDS = 20
USER_AGENT = "century-aqua-ai-tool-router"
DEFAULT_OWNER = "JoeNgCheeWai"
DEFAULT_REPO = "century-aqua-ai"
_OWNER_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def allowed_repo() -> tuple[str, str]:
    """The ONE repository the tools may touch (read at call time so a deploy-time variable change takes effect)."""
    return (os.getenv("GITHUB_OWNER", "").strip() or DEFAULT_OWNER, os.getenv("GITHUB_REPO", "").strip() or DEFAULT_REPO)


@dataclass
class ToolResult:
    ok: bool
    tool: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "tool": self.tool, "message": self.message, **self.data}


def _fail(tool: str, message: str) -> ToolResult:
    return ToolResult(False, tool, message)


TOKEN_ENV = "GITHUB_PAT"
_warned_alias = False


def token_name() -> str | None:
    """
    The NAME of the environment variable the GitHub token is set under (never its value), or None.

    The documented name is GITHUB_PAT. Environment variable names are case-sensitive on Linux (Render), so a token entered as ``GitHub_Pat``
    used to be invisible: the CTO's file reader answered "not set" with a perfectly good token on the server. A different CASING of the same
    name is now accepted. GITHUB_PAT wins when it is present at all, even if empty, so an explicit empty value still means "not configured".
    """
    global _warned_alias
    if TOKEN_ENV in os.environ:
        return TOKEN_ENV if os.environ[TOKEN_ENV].strip() else None
    for name, value in os.environ.items():
        if name.upper() == TOKEN_ENV and value.strip():
            if not _warned_alias:
                _warned_alias = True
                logger.warning("tools: the GitHub token is set as '%s'; the documented name is %s. Accepted, but rename it to match render.yaml.", name, TOKEN_ENV)
            return name
    return None


def _token() -> str:
    name = token_name()
    return os.environ.get(name, "").strip() if name else ""


def configured() -> bool:
    return token_name() is not None


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }


def _repo_problem(owner: Any, repo: Any) -> str | None:
    if not (isinstance(owner, str) and isinstance(repo, str) and _OWNER_REPO.match(owner) and _OWNER_REPO.match(repo)):
        return "the repository owner or name is not valid"
    if (owner.lower(), repo.lower()) != tuple(x.lower() for x in allowed_repo()):
        return "this repository is not allowed: the tools only work on the project's own repository"
    return None


def _branch_problem(branch: Any, default_branch: str = "main") -> str | None:
    if not isinstance(branch, str) or not branch.startswith(plan_rules.BRANCH_PREFIX):
        return f"the branch must start with {plan_rules.BRANCH_PREFIX}"
    try:
        normal = plan_rules.clean_branch(branch)  # raises PlanError for a name with nothing usable in it (for example "agent/")
    except plan_rules.PlanError:
        return "that branch name is not allowed"
    # Exactly the cleaned form is required: a name that cleaning would change (capitals, spaces, "..") is refused, not repaired.
    if branch.lower() == default_branch.lower() or normal != branch:
        return "that branch name is not allowed"
    return None


def _api_error(response: requests.Response) -> str:
    """A short, safe reason from a failed GitHub call (status and GitHub's own message, never headers or the token)."""
    try:
        text = str(response.json().get("message", ""))[:160]
    except Exception:
        text = ""
    hint = {
        401: "the token was refused (expired or wrong)",
        403: "the token may lack permission for this action, or a rate limit was hit",
        404: "not found, or the token cannot see this repository",
        409: "a conflict (for example the file or branch changed)",
        422: "GitHub refused the request (for example the branch already exists, or there is nothing to compare)",
    }.get(response.status_code, "GitHub returned an error")
    return f"GitHub {response.status_code}: {hint}" + (f" ({text})" if text else "")


def _call(tool: str, method: str, path: str, **kwargs: Any) -> tuple[requests.Response | None, ToolResult | None]:
    """One GitHub request. Returns (response, None) or (None, a failed ToolResult). Never raises."""
    if not configured():
        return None, _fail(tool, "GITHUB_PAT is not set on this server")
    try:
        return requests.request(method, f"{API}{path}", headers=_headers(), timeout=TIMEOUT_SECONDS, **kwargs), None
    except requests.Timeout:
        return None, _fail(tool, "GitHub did not answer in time")
    except requests.RequestException as exc:
        logger.error("tools: %s could not reach GitHub (%s)", tool, type(exc).__name__)
        return None, _fail(tool, "could not reach GitHub")


# ------------------------------------------------------------------------------------------------ tool 1: mock image
MOCKUP_BASE = "https://placehold.co/800x600/1a1a2e/00ffff"


def mock_generate_image(prompt: str) -> str:
    """
    A placeholder image URL standing in for a real image generator, so the workflow can be proven end to end with no model
    and no network call. The prompt becomes the label on the image (cleaned and shortened); nothing is generated or fetched.
    """
    words = re.sub(r"[^A-Za-z0-9 ]+", " ", prompt if isinstance(prompt, str) else "").split()
    label = " ".join(words[:6]) or "Mockup"
    return f"{MOCKUP_BASE}?text={quote_plus('CMO ' + label)}"


# ------------------------------------------------------------------------------------------------ tool: read a file (read-only)
MAX_READ_BYTES = 60_000  # a longer file is cut off and the reader is told; it keeps one answer from drowning the model
CUT_OFF_MARK = "[... file cut off here:"  # appended to a file that was too long: a cut-off file has NOT been fully read
_REF_OK = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")

# How the model is offered the tool (Anthropic's tool format). The CTO is offered ONLY this tool.
READ_TOOL_NAME = "github_read_file"
READ_TOOL_SPEC: dict[str, Any] = {
    "name": READ_TOOL_NAME,
    "description": (
        "Read ONE file from the Century Aqua project's own repository and get back its text. It only reads: it changes nothing, "
        "needs no approval, and you may call it any time. Use it to look at code before you advise on it, and ALWAYS before you "
        "propose a change to an existing file. Secret files (environment files, keys) cannot be read. A missing file or a "
        "folder returns a short ERROR line instead of text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The file's path from the repository root, for example frontend/src/components/mc/AgentChat.tsx"},
            "branch": {"type": "string", "description": "The branch to read. Leave it out for main, which is the branch a change is made from."},
        },
        "required": ["file_path"],
    },
}


def github_read_file(file_path: str, branch: str = "main") -> str:
    """
    READ ONE FILE from the project's own repository and return its text. This is the CTO's eyes. It is read-only and safe:
    it changes nothing, so it needs no authorization request.

    Returns the file's text, or, when it cannot, a short plain error string that begins with "ERROR:" (for example
    "ERROR: File not found: ..."). It never raises and never returns anything but a string.

    Guards, all in code: the repository is fixed by the server (GITHUB_OWNER / GITHUB_REPO, never a caller's argument); the
    path must be a clean relative path, and anything that holds a SECRET (env files, keys, credentials, the .git folder) is
    refused by name, and anything inside an ordinary file that looks like a key or token is replaced by a marker, so a read
    does not put a secret in front of the model, a log or a stored answer (a pattern check: an unusual format is not caught);
    the branch name is checked; a folder, a binary file or a very large file is reported, not dumped. The token (GITHUB_PAT)
    is read at call time and never appears in a result or a log line.
    """
    tool = "github_read_file"
    try:
        owner, repo = allowed_repo()
        if not isinstance(branch, str) or not _REF_OK.match(branch) or ".." in branch or branch.startswith(("/", "-")):
            return "ERROR: that branch name is not valid"
        try:
            path = plan_rules.check_path_for_read(file_path)
        except plan_rules.PlanError as exc:
            return f"ERROR: {exc}"
        if not configured():
            return "ERROR: GITHUB_PAT is not set on this server, so no file can be read"
        url = f"{API}/repos/{owner}/{repo}/contents/{quote(path, safe='/')}"
        try:
            # The raw media type returns the file's own text instead of JSON with base64. A folder still answers with JSON.
            response = requests.request(
                "GET", url, params={"ref": branch}, timeout=TIMEOUT_SECONDS,
                headers={**_headers(), "Accept": "application/vnd.github.v3.raw"},
            )
        except requests.Timeout:
            return "ERROR: GitHub did not answer in time"
        except requests.RequestException as exc:
            logger.error("tools: %s could not reach GitHub (%s)", tool, type(exc).__name__)
            return "ERROR: could not reach GitHub"
        if response.status_code == 404:
            return f"ERROR: File not found: {path} (on branch {branch})"
        if response.status_code != 200:
            return "ERROR: " + _api_error(response)
        if "json" in str(response.headers.get("Content-Type", "")).lower():  # a folder (or a submodule) answers with JSON
            return f"ERROR: {path} is a folder, not a file. Read a specific file inside it."
        raw = response.content or b""
        if b"\x00" in raw[:8000]:
            return f"ERROR: {path} is a binary file and cannot be read as text"
        text = plan_rules.redact_secrets(raw[:MAX_READ_BYTES].decode("utf-8", errors="replace"))
        if len(raw) > MAX_READ_BYTES:
            text += f"\n\n{CUT_OFF_MARK} it is {len(raw):,} bytes and only the first {MAX_READ_BYTES:,} are shown. It is too large to rewrite whole.]"
        return text
    except Exception as exc:  # a read tool must never take the app down
        logger.error("tools: %s failed (%s)", tool, type(exc).__name__)
        return "ERROR: the file could not be read"


def github_file_status(file_path: str) -> str:
    """
    Does ``file_path`` exist on the repository's default branch? Returns "exists", "missing" or "unknown" (it could not be
    checked: no token, a network problem, an unclean path). Read-only. Used by the CTO to tell a NEW file (fine to create) from an
    EXISTING file it must have read first. "unknown" is never treated as "missing": the caller fails closed.
    """
    try:
        path = plan_rules.check_path_for_read(file_path)
    except plan_rules.PlanError:
        return "unknown"
    try:
        owner, repo = allowed_repo()
        response, failure = _call("github_file_status", "GET", f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}")
        if failure or response is None:
            return "unknown"
        if response.status_code == 200:
            return "exists"
        return "missing" if response.status_code == 404 else "unknown"
    except Exception:
        return "unknown"


# ------------------------------------------------------------------------------------------------ GitHub helpers
def _default_branch(tool: str, owner: str, repo: str) -> tuple[str | None, ToolResult | None]:
    response, failure = _call(tool, "GET", f"/repos/{owner}/{repo}")
    if failure:
        return None, failure
    if response.status_code != 200:
        return None, _fail(tool, _api_error(response))
    try:
        return str(response.json()["default_branch"]), None
    except Exception:
        return None, _fail(tool, "GitHub's reply did not say which branch is the default")


# ------------------------------------------------------------------------------------------------ tool 2: branch
def github_create_branch(repo_owner: str, repo_name: str, branch_name: str) -> ToolResult:
    """Create ``branch_name`` (must start with agent/) from the tip of the default branch."""
    tool = "github_create_branch"
    problem = _repo_problem(repo_owner, repo_name) or _branch_problem(branch_name)
    if problem:
        return _fail(tool, problem)
    default, failure = _default_branch(tool, repo_owner, repo_name)
    if failure:
        return failure
    if branch_name.lower() == default.lower():
        return _fail(tool, "that branch name is not allowed")
    response, failure = _call(tool, "GET", f"/repos/{repo_owner}/{repo_name}/git/ref/heads/{quote(default, safe='/')}")
    if failure:
        return failure
    if response.status_code != 200:
        return _fail(tool, _api_error(response))
    try:
        sha = str(response.json()["object"]["sha"])
    except Exception:
        return _fail(tool, "GitHub's reply did not include the default branch's commit")
    response, failure = _call(tool, "POST", f"/repos/{repo_owner}/{repo_name}/git/refs", json={"ref": f"refs/heads/{branch_name}", "sha": sha})
    if failure:
        return failure
    if response.status_code != 201:
        return _fail(tool, _api_error(response))
    return ToolResult(True, tool, f"created branch {branch_name} from {default}", {"branch": branch_name, "base": default, "sha": sha})


# ------------------------------------------------------------------------------------------------ tool 3: write a file
def github_write_file(repo_owner: str, repo_name: str, branch: str, file_path: str, content: str, commit_message: str) -> ToolResult:
    """
    Create or overwrite ONE file on ``branch`` with ``content`` (the COMPLETE new text) as one commit. It overwrites: it
    does not merge with what is there. Protected files, secrets, oversized content and any branch not under agent/ are
    refused here, whatever the caller says.
    """
    tool = "github_write_file"
    problem = _repo_problem(repo_owner, repo_name) or _branch_problem(branch)
    if problem:
        return _fail(tool, problem)
    try:
        path = plan_rules.check_path(file_path)
        text = plan_rules.check_content(path, content)
    except plan_rules.PlanError as exc:
        return _fail(tool, str(exc))
    message = " ".join(str(commit_message or "").split())[: plan_rules.MAX_COMMIT_CHARS] or f"Update {path}"
    url = f"/repos/{repo_owner}/{repo_name}/contents/{quote(path, safe='/')}"
    existing, failure = _call(tool, "GET", url, params={"ref": branch})
    if failure:
        return failure
    body: dict[str, Any] = {"message": message, "content": base64.b64encode(text.encode("utf-8")).decode("ascii"), "branch": branch}
    if existing.status_code == 200:
        try:
            body["sha"] = str(existing.json()["sha"])  # needed to overwrite a file that already exists
        except Exception:
            return _fail(tool, "GitHub's reply did not include the existing file's version")
    elif existing.status_code != 404:
        return _fail(tool, _api_error(existing))
    response, failure = _call(tool, "PUT", url, json=body)
    if failure:
        return failure
    if response.status_code not in (200, 201):
        return _fail(tool, _api_error(response))
    created = response.status_code == 201
    try:
        commit_sha = str(response.json()["commit"]["sha"])
    except Exception:
        commit_sha = ""
    return ToolResult(True, tool, f"{'created' if created else 'updated'} {path}", {"path": path, "created": created, "commit_sha": commit_sha})



# ------------------------------------------------------------------------------------------------ tool 4: open a PR
def github_open_pr(repo_owner: str, repo_name: str, branch: str, title: str, body: str) -> ToolResult:
    """Open a pull request from ``branch`` into the default branch. It is never merged by this code."""
    tool = "github_open_pr"
    problem = _repo_problem(repo_owner, repo_name) or _branch_problem(branch)
    if problem:
        return _fail(tool, problem)
    clean_title = " ".join(str(title or "").split())[: plan_rules.MAX_TITLE_CHARS]
    if not clean_title:
        return _fail(tool, "the pull request needs a title")
    default, failure = _default_branch(tool, repo_owner, repo_name)
    if failure:
        return failure
    payload = {"title": clean_title, "head": branch, "base": default, "body": str(body or "")[: plan_rules.MAX_BODY_CHARS + 500]}
    response, failure = _call(tool, "POST", f"/repos/{repo_owner}/{repo_name}/pulls", json=payload)
    if failure:
        return failure
    if response.status_code != 201:
        return _fail(tool, _api_error(response))
    try:
        data = response.json()
        url, number = str(data["html_url"]), int(data["number"])
    except Exception:
        return _fail(tool, "GitHub opened the pull request but its reply was unreadable; check the repository")
    return ToolResult(True, tool, f"opened pull request #{number}", {"pr_url": url, "pr_number": number, "base": default, "branch": branch})


# ------------------------------------------------------------------------------------------------ the sequential runner
def run_execution_plan(plan: dict[str, Any], pr_footer: str = "") -> dict[str, Any]:
    """
    Run a plan in order: create branch, write every file, open the pull request. STOPS AT THE FIRST FAILURE (nothing after
    a failed step runs) and says exactly which steps finished, so a human can see what exists on GitHub. The plan is
    validated AGAIN here from scratch, so a stored copy that was edited in the database is not trusted. Never raises.

    Returns {"status": "succeeded" | "failed" | "refused" | "unconfigured", "steps": [...], "pr_url": str | None,
             "branch": str | None, "error": str | None}.
    """
    clean, findings = plan_rules.normalize_execution_plan(plan)
    if clean is None:
        return {"status": "refused", "steps": [], "pr_url": None, "branch": None, "error": "; ".join(findings) or "no plan"}
    if not configured():
        return {"status": "unconfigured", "steps": [], "pr_url": None, "branch": clean["branch_name"], "error": "GITHUB_PAT is not set on the backend"}
    owner, repo = allowed_repo()
    steps: list[dict[str, Any]] = []

    def record(result: ToolResult) -> bool:
        steps.append({"tool": result.tool, "ok": result.ok, "message": result.message})
        return result.ok

    def stop(error: str) -> dict[str, Any]:
        return {"status": "failed", "steps": steps, "pr_url": None, "branch": clean["branch_name"], "error": error}

    created = github_create_branch(owner, repo, clean["branch_name"])
    if not record(created):
        return stop(created.message)
    for item in clean["files_to_update"]:
        written = github_write_file(owner, repo, clean["branch_name"], item["path"], item["content"], item["commit_message"])
        if not record(written):
            return stop(f"{item['path']}: {written.message}")
    # The footer (who approved, and which audit entry) is added after validation so a long body cannot push it out.
    body = (clean["pr_body"] + ("\n\n---\n" + pr_footer if pr_footer else ""))[: plan_rules.MAX_BODY_CHARS + 500]
    opened = github_open_pr(owner, repo, clean["branch_name"], clean["pr_title"], body)
    if not record(opened):
        return stop(opened.message)
    return {"status": "succeeded", "steps": steps, "pr_url": opened.data["pr_url"], "branch": clean["branch_name"], "error": None}


TOOLS = {
    "mock_generate_image": mock_generate_image,
    "github_create_branch": github_create_branch,
    "github_write_file": github_write_file,
    "github_open_pr": github_open_pr,
    "github_read_file": github_read_file,  # read-only: changes nothing, so it needs no authorization (the CTO's eyes)
}

