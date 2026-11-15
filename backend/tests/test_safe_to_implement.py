"""
"Safe to Implement" is decided by CODE (app/agents/safe_to_implement.py): an allow-list of small kinds of change, never a model's say-so.

These tests pin both directions: what is let through (small UI, prompt wording, test, document, strictly additive database change) and,
more importantly, everything that must NOT be (commands, login or secret files, dependencies, deployment, big changes, anything critical,
a plan that failed its self-check). The classifier fails closed: a surprise is "not safe".

    python -m unittest tests.test_safe_to_implement   (from backend/)
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import safe_to_implement as sti  # noqa: E402

UI = "frontend/src/components/mc/Badge.tsx"
PROMPT_FILE = "backend/app/prompts/new_prompt.py"
TEST_FILE = "backend/tests/test_new_thing.py"
MIGRATION = "frontend/prisma/migrations/20261115000000_add_note/migration.sql"
SCHEMA = "frontend/prisma/schema.prisma"


def request(*paths, risk="low", commands=None, **extra):
    files = [{"path": p, "content": "x = 1\n", "commit_message": "m"} for p in paths]
    base = {"action_summary": "A small thing", "risk_level": risk, "impact": "One file.", "rollback_plan": "Close the pull request.",
            "proposed_commands": commands or [], "execution_plan": {"branch_name": "agent/x", "pr_title": "t", "pr_body": "", "files_to_update": files}}
    return {**base, **extra}


def verdict(*paths, check=True, **kwargs):
    return sti.classify(request(*paths, **kwargs), check)


class WhatIsLetThroughTests(unittest.TestCase):
    def test_each_allowed_kind_of_small_change_is_safe_and_labelled(self):
        for path, kind in ((UI, "UI fix"), (PROMPT_FILE, "prompt wording"), (TEST_FILE, "test"), ("docs/NOTES.md", "document")):
            result = verdict(path, risk="medium")
            self.assertTrue(result.safe, (path, result.reasons))
            self.assertEqual(result.kind, kind)

    def test_a_strictly_additive_database_change_needs_the_schema_and_its_migration_together(self):
        self.assertTrue(verdict(SCHEMA, MIGRATION, risk="high").safe)
        self.assertFalse(verdict(SCHEMA).safe)  # a schema change with no migration folder
        self.assertFalse(verdict(MIGRATION).safe)  # a migration with no matching schema change

    def test_a_backend_prompt_is_high_risk_only_because_the_paths_force_it_and_that_is_accepted(self):
        self.assertTrue(verdict(PROMPT_FILE, risk="high").safe)  # plan_risk_floor makes anything under backend/ at least high

    def test_a_small_mix_of_allowed_kinds_is_safe(self):
        result = verdict(UI, TEST_FILE, "README.md", risk="high")
        self.assertTrue(result.safe, result.reasons)
        self.assertEqual(result.kind, "UI fix + test + document")


class WhatMustNeverBeLetThroughTests(unittest.TestCase):
    def test_any_command_or_deployment_step_makes_it_not_minor(self):
        self.assertFalse(verdict(UI, commands=["npm run build"]).safe)
        self.assertFalse(sti.classify(request(UI, deployment_commands=["publish it"]), True).safe)

    def test_a_critical_risk_or_a_high_risk_the_agent_chose_for_a_ui_file_goes_to_a_person(self):
        self.assertFalse(verdict(UI, risk="critical").safe)
        self.assertFalse(verdict(PROMPT_FILE, risk="critical").safe)
        self.assertFalse(verdict(UI, risk="high").safe)  # the paths alone do not make a UI file high, so the agent flagged a worry
        self.assertFalse(verdict(UI, risk="surprise").safe)
        self.assertFalse(sti.classify({**request(UI), "risk_level": None}, True).safe)

    def test_login_secret_dependency_build_and_deploy_files_are_never_safe(self):
        for path in ("frontend/src/proxy.ts", "frontend/src/lib/mc-auth.ts", "frontend/src/components/mc/LoginForm.tsx", ".env", "frontend/package.json",
                     "frontend/package-lock.json", "backend/requirements.txt", "backend/Dockerfile", "render.yaml", "vercel.json", "frontend/next.config.ts",
                     ".github/workflows/pr-check.yml", "backend/app/governance/audit.py", "frontend/src/lib/mc-session.ts",
                     "backend/app/prompts/credentials_prompt.py", "frontend/src/components/clerk/Widget.tsx",
                     "frontend/src/components/mc/AuthorizationDetail.tsx"):  # the approval screen: hiding a risk from the approver is not minor
            self.assertFalse(verdict(path).safe, path)

    def test_a_harmless_component_whose_name_merely_contains_session_is_safe(self):
        # Found by the live run: an over-broad "session" rule blocked this, which is exactly the kind of small Boardroom UI work the COO is for.
        for path in ("frontend/src/components/mc/SessionsBadge.tsx", "frontend/src/components/mc/SessionPanel.tsx"):
            self.assertTrue(verdict(path, risk="medium").safe, path)

    def test_files_off_the_list_are_not_safe(self):
        for path in ("backend/app/agents/cto_agent.py", "backend/app/api/cto.py", "frontend/src/app/page.tsx", "frontend/src/lib/mc-backend.ts",
                     "backend/app/main.py", "scripts/run.sh", "frontend/prisma/migrations/not_a_timestamp/migration.sql"):
            self.assertFalse(verdict(path).safe, path)

    def test_one_bad_file_among_good_ones_makes_the_whole_task_not_safe(self):
        self.assertFalse(verdict(UI, TEST_FILE, "backend/app/agents/cto_agent.py").safe)

    def test_size_limits_a_fourth_file_a_big_file_and_a_big_total(self):
        self.assertFalse(verdict(UI, TEST_FILE, PROMPT_FILE, "a.md", risk="high").safe)  # four files
        big = request(UI)
        big["execution_plan"]["files_to_update"][0]["content"] = "x" * (sti.MAX_FILE_BYTES + 1)
        self.assertFalse(sti.classify(big, True).safe)
        many = request(UI, TEST_FILE, "docs/a.md")
        for item in many["execution_plan"]["files_to_update"]:
            item["content"] = "x" * (sti.MAX_FILE_BYTES - 100)
        self.assertFalse(sti.classify(many, True).safe)  # each file is small, the three together are not

    def test_a_ui_file_that_reaches_an_endpoint_storage_or_dynamic_code_is_not_minor(self):
        # The live run's mismatch: "a display-only badge" that is really a button calling the admin reset endpoint. Paths alone let it through.
        for body in ("<button onClick={() => fetch('/api/admin/agents/cto/reset', { method: 'POST' })}>x</button>", "const c = document.cookie;",
                     "localStorage.setItem('a', 'b');", "eval(text);", "<div dangerouslySetInnerHTML={{ __html: x }} />", "const k = process.env.SECRET;",
                     "navigator.sendBeacon('/x', data);", "fetch('/api/anything');", "const m = await import('./x');"):
            req = request(UI, risk="medium")
            req["execution_plan"]["files_to_update"][0]["content"] = body
            result = sti.classify(req, True)
            self.assertFalse(result.safe, body)
            self.assertIn("not a minor change", " ".join(result.reasons), body)

    def test_a_test_or_a_document_may_name_those_things_because_it_does_not_run_in_the_product(self):
        for path in (TEST_FILE, "docs/NOTES.md"):
            req = request(path, risk="medium")
            req["execution_plan"]["files_to_update"][0]["content"] = "The endpoint /api/admin/agents/cto/reset uses fetch( and document.cookie.\n"
            self.assertTrue(sti.classify(req, True).safe, path)

    def test_a_plain_display_component_still_passes_the_content_screen(self):
        req = request(UI, risk="medium")
        req["execution_plan"]["files_to_update"][0]["content"] = "export default function B({ n }: { n: number }) {\n  return <span>{n} today</span>;\n}\n"
        self.assertTrue(sti.classify(req, True).safe)

    def test_a_plan_that_failed_its_self_check_is_never_safe(self):
        self.assertFalse(verdict(UI, check=False).safe)

    def test_no_plan_or_a_malformed_request_is_not_safe_and_never_raises(self):
        for bad in (None, "x", {}, {"execution_plan": None}, {"execution_plan": {"files_to_update": []}}, {"execution_plan": "plan"},
                    {"risk_level": "low", "execution_plan": {"files_to_update": [5]}}, {"risk_level": "low", "execution_plan": {"files_to_update": [{"path": None}]}}):
            result = sti.classify(bad, True)
            self.assertFalse(result.safe, bad)
            self.assertTrue(result.reasons)


if __name__ == "__main__":
    unittest.main()
