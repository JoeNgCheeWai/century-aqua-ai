"""
The CTO's SELF-CHECK: deterministic checks an execution_plan must pass before the CEO is told it was verified.

WHY THIS EXISTS. The CTO cannot run tests, a build or a script: it has read-only tools and nothing else. So "I have verified this
solution" can never be a claim the MODEL makes about its own work. It is a claim CODE makes, after code has really run checks, and the
claim lists exactly what was checked and what was not. If a check fails the plan is dropped (all or nothing, like the read-before-write
rule) and the CEO is told why.

WHAT IS CHECKED (everything is static: nothing the model wrote is ever executed or imported):
  * a Python file must parse (ast.parse); a JSON file must load;
  * no file may be cut short with a placeholder such as "// ... rest of the file", which would destroy the real file when it is
    overwritten whole, and none may carry merge-conflict markers or NUL characters;
  * an existing file that was read must not shrink to less than half its size (the usual sign of a truncated rewrite);
  * wording the real model has REFUSED in a prompt (the Boardroom outage) may not appear in a prompt file;
  * a database migration may only ADD (tables, indexes, enums, columns that are nullable or have a default), and the Prisma schema may
    only gain lines.

WHAT IS NOT CHECKED, and the declaration says so: the full test suite and the frontend build. They run on the pull request (CI) before
anyone merges. A TypeScript file is NOT compiled here (no compiler on the server), so its check is only the placeholder, conflict and
shrink checks above.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import dataclass, field

# Wording the real model refused in the Boardroom (see PROJECT_CONTEXT.md, "The refusal outage"): a request to hide its reasoning.
REFUSED_WORDING = ("private working", "behind a toggle", "your private", "keep this secret", "hide your")
MIN_KEPT_FRACTION = 0.5  # an existing file may not shrink below this fraction of what was read
SHRINK_FLOOR_CHARS = 600  # smaller files may legitimately be rewritten short
NOT_RUN = ("the full test suite", "the frontend build")

_PLACEHOLDER = re.compile(
    r"(?im)(?:^|\s)(?://|#|/\*|<!--|\{/\*)\s*(?:\.{3}|\u2026)\s*(?:rest|remaining|existing|unchanged|other|same|\(|$)"
    r"|(?:^|\s)(?://|#)\s*(?:rest of (?:the )?(?:file|code|component|function|content)|remaining (?:code|content)|"
    r"existing code(?: here)?|code unchanged|unchanged)\b"
)
_CONFLICT = re.compile(r"(?m)^(?:<{7} |>{7} |={7}$)")
_FORBIDDEN_SQL = re.compile(r"\b(drop|delete|update|truncate|rename|grant|revoke|insert|copy|execute|call|do|using)\b|alter\s+column|set\s+not\s+null", re.I)
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_MIGRATION_PATH = re.compile(r"(?i)(^|/)prisma/migrations/[^/]+/migration\.sql$")


@dataclass
class PlanCheck:
    passed: bool
    checks: list[str] = field(default_factory=list)  # what really ran and passed, in plain words
    failures: list[str] = field(default_factory=list)
    not_run: tuple[str, ...] = NOT_RUN


def check_additive_sql(sql: str) -> list[str]:
    """Problems that make a migration NOT strictly additive (empty list = it only adds). Fails closed on anything unrecognised."""
    text = _SQL_COMMENT.sub(" ", sql or "")
    problems: list[str] = []
    bad = _FORBIDDEN_SQL.search(text)
    if bad:
        problems.append(f"the migration contains '{bad.group(0).strip()}', which is not strictly additive")
    statements = [s.strip() for s in text.split(";") if s.strip()]
    if not statements:
        return problems + ["the migration is empty"]
    ident = r'(?:"\w+"|\w+)'
    for statement in statements:
        norm = " ".join(statement.split()).lower()
        if (re.match(rf"^create table (?:if not exists )?{ident} \(", norm) or re.match(rf"^create (?:unique )?index (?:if not exists )?{ident} on ", norm)
                or re.match(rf"^create type {ident} as enum \(", norm)):
            continue
        alter = re.match(rf"^alter table {ident} (.+)$", norm)
        if not alter:
            problems.append(f"an unrecognised statement: {norm[:60]}")
            continue
        for clause in re.split(r",\s*(?=add column)", alter.group(1)):
            if not clause.startswith("add column"):
                problems.append(f"an ALTER TABLE that is not ADD COLUMN: {clause[:50]}")
            elif " not null" in clause and " default " not in clause:
                problems.append("a NOT NULL column without a default would fail on rows that already exist")
    return problems


def schema_only_adds(old: str, new: str) -> bool:
    """True if every non-empty line of the old Prisma schema is still in the new one (the schema only GAINED lines)."""
    kept = {line.strip() for line in new.splitlines() if line.strip()}
    return all(line.strip() in kept for line in old.splitlines() if line.strip())


def _file_problems(path: str, content: str, old: str | None) -> list[str]:
    problems: list[str] = []
    lower = path.lower()
    if "\x00" in content:
        problems.append(f"{path}: contains a NUL character")
    if _CONFLICT.search(content):
        problems.append(f"{path}: contains merge-conflict markers")
    if not lower.endswith(".md") and _PLACEHOLDER.search(content):
        problems.append(f"{path}: contains a placeholder comment (for example '// ... rest of the file'); overwriting would delete the real code")
    if old is not None and len(old) > SHRINK_FLOOR_CHARS and len(content) < MIN_KEPT_FRACTION * len(old):
        problems.append(f"{path}: the new text is {100 * len(content) // len(old)}% of the file I read, which looks like a truncated rewrite")
    if lower.endswith(".py"):
        try:
            ast.parse(content)
        except SyntaxError as exc:
            problems.append(f"{path}: Python syntax error at line {exc.lineno}")
        if "/prompts/" in lower:
            hit = next((w for w in REFUSED_WORDING if w in content.lower()), None)
            if hit:
                problems.append(f"{path}: the prompt contains '{hit}', wording the real model refused in the Boardroom")
    elif lower.endswith(".json"):
        try:
            json.loads(content)
        except ValueError:
            problems.append(f"{path}: not valid JSON")
    elif _MIGRATION_PATH.search(path):
        problems.extend(f"{path}: {p}" for p in check_additive_sql(content))
    elif lower.endswith(".prisma"):
        if content.count("{") != content.count("}"):
            problems.append(f"{path}: unbalanced braces")
        if old is not None and not schema_only_adds(old, content):
            problems.append(f"{path}: the schema changed or removed existing lines (only additions are allowed)")
    return problems


def _what_ran(path: str, existing: bool) -> list[str]:
    lower = path.lower()
    ran = ["no placeholder, merge-conflict or NUL text"]
    if existing:
        ran.append("the new text is not a truncated rewrite of the file I read")
    if lower.endswith(".py"):
        ran.append("it parses as Python")
        if "/prompts/" in lower:
            ran.append("no wording the real model has refused")
    elif lower.endswith(".json"):
        ran.append("it is valid JSON")
    elif _MIGRATION_PATH.search(path):
        ran.append("the migration only adds (no drop, delete, rename or tightening)")
    elif lower.endswith(".prisma"):
        ran.append("the schema only gained lines")
    return ran


def check_plan(plan: dict | None, originals: dict[str, str] | None = None) -> PlanCheck:
    """
    Run every static check on an (already rule-checked) execution_plan. ``originals`` maps a path (lower-case) to the text the CTO READ
    this answer, for the files the plan overwrites; a path missing from it is a new file. Never raises: a check that cannot run is a
    failure, not a pass.
    """
    originals = originals or {}
    if not plan or not plan.get("files_to_update"):
        return PlanCheck(False, [], ["there is no plan to check"])
    failures: list[str] = []
    ran: list[str] = []
    try:
        for item in plan["files_to_update"]:
            path, content = item["path"], item["content"]
            old = originals.get(path.strip().lower())
            problems = _file_problems(path, content, old)
            failures.extend(problems)
            if not problems:
                ran.append(f"{path}: " + "; ".join(_what_ran(path, old is not None)))
    except Exception as exc:  # a checker that crashes must never pass a plan
        return PlanCheck(False, [], [f"the self-check itself failed ({type(exc).__name__})"])
    return PlanCheck(not failures, ran if not failures else [], failures)


def declaration(result: PlanCheck, schema_read: bool, tests_read: bool) -> str:
    """
    The sentence the CEO sees about verification, written by CODE after the checks passed. It states what was checked, what was read
    for context, and what was NOT run, so it cannot claim more than was done. Returns "" when the checks did not pass.
    """
    if not result.passed:
        return ""
    context = []
    if schema_read:
        context.append("the database schema")
    if tests_read:
        context.append("the existing tests")
    read = (" I also read " + " and ".join(context) + " to see how this fits.") if context else ""
    return (
        "**Self-check:** I have run these checks on the plan: " + " | ".join(result.checks) + "." + read
        + " I have not run " + " or ".join(result.not_run) + "; they run on the pull request before anything is merged."
    )
