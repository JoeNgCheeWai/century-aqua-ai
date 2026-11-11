"""
Phase 6.6 tests: the tool router (app/agents/tools.py) and the execution-plan validator (app/agents/execution_plan.py).

NO TEST HERE REACHES THE NETWORK. Every call to GitHub goes through ``requests.request``, which is replaced by FakeGitHub: a
small in-memory stand-in that answers like the GitHub REST API (default branch, refs, contents, pulls), records every call,
and can be told to fail. A test that ever reached the real ``requests.request`` would fail loudly (see GuardedTestCase).

    python -m unittest tests.test_tools   (from backend/)
"""

import base64
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import requests  # noqa: E402

from app.agents import execution_plan as ep  # noqa: E402
from app.agents import tools  # noqa: E402

OWNER, REPO = "JoeNgCheeWai", "century-aqua-ai"
TOKEN = "github_pat_" + "A" * 40  # a fake token: the tests prove it never leaks into a result


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeGitHub:
    """An in-memory GitHub. ``fail`` maps 'METHOD path-regex' to a status (or an exception) to force a failure."""

    def __init__(self, default="main"):
        self.default, self.sha = default, "a" * 40
        self.branches = {default: self.sha}
        self.files = {}  # (branch, path) -> text
        self.prs = []
        self.calls = []
        self.fail = {}

    def __call__(self, method, url, headers=None, timeout=None, params=None, json=None, **kw):
        path = url.replace(tools.API, "")
        self.calls.append({"method": method, "path": path, "headers": headers, "timeout": timeout, "json": json, "params": params})
        for key, outcome in self.fail.items():
            m, pattern = key.split(" ", 1)
            if m == method and re.search(pattern, path):
                if isinstance(outcome, Exception):
                    raise outcome
                return FakeResponse(outcome, {"message": "forced failure"})
        if method == "GET" and path == f"/repos/{OWNER}/{REPO}":
            return FakeResponse(200, {"default_branch": self.default})
        m = re.fullmatch(rf"/repos/{OWNER}/{REPO}/git/ref/heads/(.+)", path)
        if method == "GET" and m:
            sha = self.branches.get(m.group(1))
            return FakeResponse(200, {"object": {"sha": sha}}) if sha else FakeResponse(404, {"message": "Not Found"})
        if method == "POST" and path == f"/repos/{OWNER}/{REPO}/git/refs":
            name = json["ref"].replace("refs/heads/", "")
            if name in self.branches:
                return FakeResponse(422, {"message": "Reference already exists"})
            self.branches[name] = json["sha"]
            return FakeResponse(201, {"ref": json["ref"]})
        m = re.fullmatch(rf"/repos/{OWNER}/{REPO}/contents/(.+)", path)
        if m and method == "GET":
            key = (params["ref"], m.group(1))
            return FakeResponse(200, {"sha": "f" * 40}) if key in self.files else FakeResponse(404, {"message": "Not Found"})
        if m and method == "PUT":
            key = (json["branch"], m.group(1))
            existed = key in self.files
            if existed and "sha" not in json:
                return FakeResponse(422, {"message": "sha wasn't supplied"})
            self.files[key] = base64.b64decode(json["content"]).decode("utf-8")
            return FakeResponse(200 if existed else 201, {"commit": {"sha": "c" * 40}})
        if method == "POST" and path == f"/repos/{OWNER}/{REPO}/pulls":
            number = len(self.prs) + 1
            self.prs.append(json)
            return FakeResponse(201, {"html_url": f"https://github.com/{OWNER}/{REPO}/pull/{number}", "number": number})
        return FakeResponse(404, {"message": f"unexpected request {method} {path}"})

    def methods(self):
        return [c["method"] for c in self.calls]


class GuardedTestCase(unittest.TestCase):
    """Sets the token and the fake GitHub, and fails any test that reaches the real network."""

    def setUp(self):
        self.gh = FakeGitHub()
        for patcher in (
            mock.patch.dict(os.environ, {"GITHUB_PAT": TOKEN, "GITHUB_OWNER": "", "GITHUB_REPO": ""}),
            mock.patch.object(requests, "request", self.gh),
            mock.patch("requests.sessions.Session.request", side_effect=AssertionError("a test reached the real network")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


def good_plan(**overrides):
    plan = {
        "branch_name": "agent/add-hero-banner",
        "files_to_update": [
            {"path": "frontend/src/components/Hero.tsx", "content": "export default function Hero() { return <h1>Hi</h1>; }\n", "commit_message": "Add hero"},
            {"path": "frontend/src/app/about/page.tsx", "content": "export default function About() { return null; }\n", "commit_message": "Add about"},
        ],
        "pr_title": "Add a hero banner",
        "pr_body": "Adds a hero banner. Check the home page.",
    }
    plan.update(overrides)
    return plan


class MockImageTests(unittest.TestCase):
    def test_it_returns_the_placeholder_url_without_touching_the_network(self):
        with mock.patch.object(requests, "request", side_effect=AssertionError("network")):
            url = tools.mock_generate_image("CMO Mockup")
        self.assertTrue(url.startswith("https://placehold.co/800x600/1a1a2e/00ffff?text="))
        self.assertIn("CMO+Mockup", url)

    def test_the_prompt_is_cleaned_shortened_and_url_safe_and_a_bad_prompt_never_raises(self):
        url = tools.mock_generate_image('Hero <script>alert(1)</script> & "banner" for the new home page of the dashboard today')
        self.assertNotIn("<", url)
        self.assertNotIn(" ", url)
        self.assertNotIn("script%3E", url.replace("script", "script%3E", 0))
        self.assertLessEqual(len(url), 200)
        for bad in (None, 5, "", "   ", "!!!"):
            self.assertTrue(tools.mock_generate_image(bad).startswith(tools.MOCKUP_BASE))


class RepoAndBranchGuardTests(GuardedTestCase):
    def test_only_the_projects_own_repository_is_allowed_and_no_request_is_made_for_another(self):
        for owner, repo in (("someone-else", REPO), (OWNER, "other-repo"), ("../x", REPO), (OWNER, ""), (None, None)):
            for result in (
                tools.github_create_branch(owner, repo, "agent/x"),
                tools.github_write_file(owner, repo, "agent/x", "frontend/a.ts", "x", "m"),
                tools.github_open_pr(owner, repo, "agent/x", "t", "b"),
            ):
                self.assertFalse(result.ok, (owner, repo))
        self.assertEqual(self.gh.calls, [])  # refused BEFORE any network call

    def test_the_allowed_repository_can_be_changed_only_by_the_server_environment(self):
        with mock.patch.dict(os.environ, {"GITHUB_OWNER": "acme", "GITHUB_REPO": "site"}):
            self.assertEqual(tools.allowed_repo(), ("acme", "site"))
            self.assertFalse(tools.github_create_branch(OWNER, REPO, "agent/x").ok)

    def test_a_branch_must_be_under_agent_and_the_default_branch_is_never_allowed(self):
        for branch in ("main", "master", "feature/x", "agent", "", None, "agent/../main", "Agent/x", "agent/x.lock"):
            for result in (tools.github_create_branch(OWNER, REPO, branch), tools.github_write_file(OWNER, REPO, branch, "frontend/a.ts", "x", "m"),
                           tools.github_open_pr(OWNER, REPO, branch, "t", "b")):
                self.assertFalse(result.ok, branch)
        self.assertEqual(self.gh.calls, [])

    def test_a_default_branch_that_is_not_main_is_still_protected(self):
        self.gh.default = "agent/trunk"  # an odd repository whose default branch lives under agent/
        self.gh.branches = {"agent/trunk": self.gh.sha}
        result = tools.github_create_branch(OWNER, REPO, "agent/trunk")
        self.assertFalse(result.ok)
        self.assertEqual(self.gh.branches, {"agent/trunk": self.gh.sha})


class CreateBranchTests(GuardedTestCase):
    def test_it_branches_off_the_tip_of_the_default_branch(self):
        result = tools.github_create_branch(OWNER, REPO, "agent/add-hero")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.gh.branches["agent/add-hero"], self.gh.sha)
        self.assertEqual(self.gh.methods(), ["GET", "GET", "POST"])
        self.assertEqual(result.data["base"], "main")

    def test_an_existing_branch_is_a_clean_failure_and_nothing_is_overwritten(self):
        tools.github_create_branch(OWNER, REPO, "agent/x")
        again = tools.github_create_branch(OWNER, REPO, "agent/x")
        self.assertFalse(again.ok)
        self.assertIn("422", again.message)

    def test_every_github_error_becomes_a_failed_result_never_an_exception(self):
        for status in (401, 403, 404, 409, 422, 500):
            gh = FakeGitHub()
            gh.fail = {"POST /git/refs": status}
            with mock.patch.object(requests, "request", gh):
                result = tools.github_create_branch(OWNER, REPO, "agent/x")
            self.assertFalse(result.ok, status)
            self.assertIn(str(status), result.message)

    def test_a_timeout_and_a_connection_error_are_reported_not_raised(self):
        for error, word in ((requests.Timeout(), "in time"), (requests.ConnectionError("dns"), "could not reach")):
            gh = FakeGitHub()
            gh.fail = {"GET ^/repos/[^/]+/[^/]+$": error}
            with mock.patch.object(requests, "request", gh):
                result = tools.github_create_branch(OWNER, REPO, "agent/x")
            self.assertFalse(result.ok)
            self.assertIn(word, result.message)

    def test_no_token_means_a_clear_refusal_and_no_request(self):
        with mock.patch.dict(os.environ, {"GITHUB_PAT": ""}):
            result = tools.github_create_branch(OWNER, REPO, "agent/x")
        self.assertFalse(result.ok)
        self.assertIn("GITHUB_PAT", result.message)
        self.assertEqual(self.gh.calls, [])

    def test_every_call_has_a_timeout_a_bearer_token_and_no_retry(self):
        tools.github_create_branch(OWNER, REPO, "agent/x")
        for call in self.gh.calls:
            self.assertEqual(call["timeout"], tools.TIMEOUT_SECONDS)
            self.assertEqual(call["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(len(self.gh.calls), 3)  # one attempt each, no retry loop

    def test_the_token_never_appears_in_any_result_or_message(self):
        results = [
            tools.github_create_branch(OWNER, REPO, "agent/x"),
            tools.github_create_branch(OWNER, REPO, "agent/x"),  # a failure
            tools.github_write_file(OWNER, REPO, "agent/x", "frontend/a.ts", "x", "m"),
            tools.github_open_pr(OWNER, REPO, "agent/x", "t", "b"),
        ]
        for r in results:
            self.assertNotIn(TOKEN, repr(r.as_dict()))



class WriteFileTests(GuardedTestCase):
    def setUp(self):
        super().setUp()
        tools.github_create_branch(OWNER, REPO, "agent/x")
        self.gh.calls.clear()

    def write(self, path="frontend/src/components/A.tsx", content="export const A = 1;\n", branch="agent/x"):
        return tools.github_write_file(OWNER, REPO, branch, path, content, "Add A")

    def test_a_new_file_is_created_with_the_exact_content_as_base64(self):
        result = self.write()
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.data["created"])
        self.assertEqual(self.gh.files[("agent/x", "frontend/src/components/A.tsx")], "export const A = 1;\n")
        put = next(c for c in self.gh.calls if c["method"] == "PUT")
        self.assertEqual(put["json"]["branch"], "agent/x")
        self.assertNotIn("sha", put["json"])  # a new file has no version to send

    def test_overwriting_an_existing_file_sends_its_current_version(self):
        self.write(content="old\n")
        self.gh.calls.clear()
        result = self.write(content="new\n")
        self.assertTrue(result.ok, result.message)
        self.assertFalse(result.data["created"])
        self.assertEqual(self.gh.files[("agent/x", "frontend/src/components/A.tsx")], "new\n")
        self.assertEqual(next(c for c in self.gh.calls if c["method"] == "PUT")["json"]["sha"], "f" * 40)

    def test_unicode_and_newlines_survive_the_round_trip(self):
        text = "// naïve café 魚\nconst x = `line1\nline2`;\n"
        self.assertTrue(self.write(content=text).ok)
        self.assertEqual(self.gh.files[("agent/x", "frontend/src/components/A.tsx")], text)

    def test_protected_files_are_refused_before_any_request(self):
        protected = [".github/workflows/pr-check.yml", ".github/workflows/evil.yml", ".env", "frontend/.env.local", "backend/.env",
                     "backend/app/agents/tools.py", "backend/app/agents/execution_plan.py", "backend/app/api/authorization.py",
                     "backend/app/api/security.py", "backend/app/governance/audit.py", "backend/app/agents/base_constitution.py",
                     "frontend/src/proxy.ts", "frontend/src/lib/mc-auth.ts", "render.yaml", "vercel.json", "secrets/server.pem",
                     "frontend/id_rsa", "BACKEND/APP/AGENTS/TOOLS.PY", "frontend/src/lib/credentials.ts"]
        for path in protected:
            result = self.write(path=path)
            self.assertFalse(result.ok, path)
            self.assertIn("protected", result.message, path)
        self.assertEqual(self.gh.calls, [])

    def test_unsafe_paths_are_refused(self):
        for path in ("/etc/passwd", "../x.ts", "frontend/../../x.ts", "frontend//a.ts", "C:/x.ts", "a\\b.ts", "frontend/./a.ts", "", "  ", None, "a\x00b.ts"):
            self.assertFalse(self.write(path=path).ok, repr(path))
        self.assertEqual(self.gh.calls, [])

    def test_secrets_binary_and_oversized_content_are_refused(self):
        for content in ("token = 'github_pat_" + "B" * 30 + "'", "ghp_" + "C" * 30, "sk-ant-" + "D" * 20, "-----BEGIN RSA PRIVATE KEY-----\nx",
                        "AKIA" + "E" * 16, "a\x00b", "x" * (ep.MAX_FILE_BYTES + 1), None, 5):
            self.assertFalse(self.write(content=content).ok, repr(content)[:40])
        self.assertEqual(self.gh.calls, [])

    def test_a_failed_put_is_reported_with_its_status(self):
        self.gh.fail = {"PUT contents": 409}
        result = self.write()
        self.assertFalse(result.ok)
        self.assertIn("409", result.message)


class OpenPrTests(GuardedTestCase):
    def setUp(self):
        super().setUp()
        tools.github_create_branch(OWNER, REPO, "agent/x")
        self.gh.calls.clear()

    def test_it_opens_a_pull_request_into_the_default_branch_and_returns_the_url(self):
        result = tools.github_open_pr(OWNER, REPO, "agent/x", "Add hero", "Body text")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.data["pr_url"], f"https://github.com/{OWNER}/{REPO}/pull/1")
        self.assertEqual(result.data["pr_number"], 1)
        self.assertEqual(self.gh.prs, [{"title": "Add hero", "head": "agent/x", "base": "main", "body": "Body text"}])

    def test_a_blank_title_is_refused_and_a_long_title_is_cut(self):
        self.assertFalse(tools.github_open_pr(OWNER, REPO, "agent/x", "   ", "b").ok)
        tools.github_open_pr(OWNER, REPO, "agent/x", "T" * 500, "b")
        self.assertLessEqual(len(self.gh.prs[0]["title"]), ep.MAX_TITLE_CHARS)

    def test_github_refusing_the_pull_request_is_a_failed_result(self):
        for status in (403, 422, 500):
            self.gh.fail = {"POST /pulls": status}
            result = tools.github_open_pr(OWNER, REPO, "agent/x", "t", "b")
            self.assertFalse(result.ok, status)
            self.assertIn(str(status), result.message)

    def test_an_unreadable_success_reply_is_reported_not_raised(self):
        original = self.gh.__call__

        def odd(method, url, **kw):
            if method == "POST" and url.endswith("/pulls"):
                return FakeResponse(201, {"unexpected": True})
            return original(method, url, **kw)

        with mock.patch.object(requests, "request", odd):
            result = tools.github_open_pr(OWNER, REPO, "agent/x", "t", "b")
        self.assertFalse(result.ok)
        self.assertIn("unreadable", result.message)

    def test_there_is_no_merge_call_at_all(self):
        tools.github_open_pr(OWNER, REPO, "agent/x", "t", "b")
        for call in self.gh.calls:
            self.assertNotIn("merge", call["path"])
        self.assertEqual(set(self.gh.methods()), {"GET", "POST"})



class RunExecutionPlanTests(GuardedTestCase):
    def test_a_good_plan_runs_branch_then_every_file_then_the_pull_request_in_that_order(self):
        outcome = tools.run_execution_plan(good_plan(), pr_footer="Opened after joe approved #7.")
        self.assertEqual(outcome["status"], "succeeded", outcome)
        self.assertEqual(outcome["pr_url"], f"https://github.com/{OWNER}/{REPO}/pull/1")
        self.assertEqual(outcome["branch"], "agent/add-hero-banner")
        self.assertEqual([s["tool"] for s in outcome["steps"]],
                         ["github_create_branch", "github_write_file", "github_write_file", "github_open_pr"])
        self.assertTrue(all(s["ok"] for s in outcome["steps"]))
        self.assertEqual(set(f for (_b, f) in self.gh.files), {"frontend/src/components/Hero.tsx", "frontend/src/app/about/page.tsx"})
        self.assertIn("Opened after joe approved #7.", self.gh.prs[0]["body"])
        self.assertIn("Adds a hero banner.", self.gh.prs[0]["body"])
        self.assertEqual(self.gh.prs[0]["base"], "main")
        self.assertEqual(self.gh.prs[0]["head"], "agent/add-hero-banner")

    def test_nothing_is_ever_written_to_the_default_branch(self):
        tools.run_execution_plan(good_plan())
        self.assertTrue(all(branch == "agent/add-hero-banner" for (branch, _f) in self.gh.files))
        self.assertEqual(self.gh.branches["main"], self.gh.sha)

    def test_it_stops_at_the_first_failure_and_runs_nothing_after_it(self):
        self.gh.fail = {"PUT contents/frontend/src/components/Hero.tsx": 403}
        outcome = tools.run_execution_plan(good_plan())
        self.assertEqual(outcome["status"], "failed")
        self.assertIsNone(outcome["pr_url"])
        self.assertIn("Hero.tsx", outcome["error"])
        self.assertEqual([s["tool"] for s in outcome["steps"]], ["github_create_branch", "github_write_file"])
        self.assertFalse(outcome["steps"][-1]["ok"])
        self.assertEqual(self.gh.prs, [])  # no pull request after a failed file
        self.assertEqual(self.gh.files, {})  # and the second file was never attempted

    def test_a_failed_second_file_reports_the_first_as_done_so_a_human_can_see_what_exists(self):
        self.gh.fail = {"PUT contents/frontend/src/app/about/page.tsx": 500}
        outcome = tools.run_execution_plan(good_plan())
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual([s["ok"] for s in outcome["steps"]], [True, True, False])
        self.assertIn(("agent/add-hero-banner", "frontend/src/components/Hero.tsx"), self.gh.files)
        self.assertEqual(self.gh.prs, [])

    def test_a_failed_branch_creation_stops_everything(self):
        self.gh.fail = {"POST /git/refs": 422}
        outcome = tools.run_execution_plan(good_plan())
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(len(outcome["steps"]), 1)
        self.assertEqual(self.gh.files, {})

    def test_a_failure_to_open_the_pull_request_is_reported_and_the_branch_and_files_are_left_for_a_human(self):
        self.gh.fail = {"POST /pulls": 422}
        outcome = tools.run_execution_plan(good_plan())
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["steps"][-1]["tool"], "github_open_pr")
        self.assertEqual(len(self.gh.files), 2)

    def test_no_step_is_ever_repeated(self):
        self.gh.fail = {"POST /pulls": 500}
        tools.run_execution_plan(good_plan())
        puts = [c for c in self.gh.calls if c["method"] == "PUT"]
        posts = [c for c in self.gh.calls if c["method"] == "POST"]
        self.assertEqual(len(puts), 2)
        self.assertEqual(len([c for c in posts if c["path"].endswith("/git/refs")]), 1)
        self.assertEqual(len([c for c in posts if c["path"].endswith("/pulls")]), 1)

    def test_an_invalid_plan_is_refused_with_no_network_call_at_all(self):
        protected = [{"path": ".github/workflows/x.yml", "content": "x", "commit_message": "m"}]
        for bad in (None, {}, "x", [], good_plan(files_to_update=[]), good_plan(pr_title=""), good_plan(files_to_update=protected)):
            outcome = tools.run_execution_plan(bad)
            self.assertEqual(outcome["status"], "refused", bad)
            self.assertEqual(outcome["steps"], [])
        self.assertEqual(self.gh.calls, [])

    def test_a_plan_edited_in_the_database_to_target_main_is_repaired_to_agent_and_never_reaches_main(self):
        outcome = tools.run_execution_plan(good_plan(branch_name="main"))
        self.assertEqual(outcome["branch"], "agent/main")
        self.assertEqual(self.gh.branches["main"], self.gh.sha)
        self.assertTrue(all(b.startswith("agent/") for (b, _f) in self.gh.files))

    def test_without_a_token_nothing_runs_and_the_status_says_so(self):
        with mock.patch.dict(os.environ, {"GITHUB_PAT": ""}):
            outcome = tools.run_execution_plan(good_plan())
        self.assertEqual(outcome["status"], "unconfigured")
        self.assertEqual(self.gh.calls, [])

    def test_it_never_raises_even_when_the_network_explodes(self):
        self.gh.fail = {"GET .*": requests.ConnectionError("boom")}
        outcome = tools.run_execution_plan(good_plan())
        self.assertEqual(outcome["status"], "failed")
        self.assertNotIn(TOKEN, repr(outcome))



class PlanValidatorTests(unittest.TestCase):
    def test_a_good_plan_is_normalised_and_the_branch_is_put_under_agent(self):
        plan, findings = ep.normalize_execution_plan(good_plan(branch_name="Add Hero Banner!"))
        self.assertEqual(findings, [])
        self.assertEqual(plan["branch_name"], "agent/add-hero-banner")
        self.assertEqual(len(plan["files_to_update"]), 2)

    def test_no_plan_is_not_an_error(self):
        self.assertEqual(ep.normalize_execution_plan(None), (None, []))

    def test_any_bad_part_drops_the_whole_plan_never_part_of_it(self):
        bad_file = {"path": ".env", "content": "x", "commit_message": "m"}
        plan = good_plan(files_to_update=good_plan()["files_to_update"] + [bad_file])
        result, findings = ep.normalize_execution_plan(plan)
        self.assertIsNone(result)
        self.assertTrue(findings and "refused and dropped" in findings[0])

    def test_limits_are_enforced(self):
        many = [{"path": f"frontend/src/f{i}.ts", "content": "x", "commit_message": "m"} for i in range(ep.MAX_FILES + 1)]
        self.assertIsNone(ep.normalize_execution_plan(good_plan(files_to_update=many))[0])
        big = [{"path": f"frontend/src/f{i}.ts", "content": "x" * (ep.MAX_FILE_BYTES - 10), "commit_message": "m"} for i in range(4)]
        self.assertIsNone(ep.normalize_execution_plan(good_plan(files_to_update=big))[0])  # over the total
        dup = [{"path": "frontend/src/a.ts", "content": "x", "commit_message": "m"},
               {"path": "FRONTEND/src/A.ts", "content": "y", "commit_message": "m"}]
        self.assertIsNone(ep.normalize_execution_plan(good_plan(files_to_update=dup))[0])

    def test_branch_names_are_made_safe(self):
        for given, expected in (("add-hero", "agent/add-hero"), ("agent/add-hero", "agent/add-hero"), ("main", "agent/main"),
                                ("../../main", "agent/main"), ("My Feature/Part 2", "agent/my-feature/part-2")):
            self.assertEqual(ep.clean_branch(given), expected, given)
        for bad in ("", "   ", None, 5, "!!!", "x" * 200):
            with self.assertRaises(ep.PlanError, msg=bad):
                ep.clean_branch(bad)

    def test_it_never_raises_on_garbage(self):
        for raw in (5, True, [], "x", {"branch_name": 5}, {"files_to_update": "x"},
                    {"branch_name": "a", "pr_title": "t", "files_to_update": [5]},
                    {"branch_name": "a", "pr_title": "t", "files_to_update": [{"path": 5}]}):
            result, findings = ep.normalize_execution_plan(raw)
            self.assertIsNone(result, raw)
            self.assertTrue(findings, raw)

    def test_risk_is_at_least_medium_and_at_least_high_for_backend_dependencies_schema_or_build_config(self):
        ui = ep.normalize_execution_plan(good_plan())[0]
        self.assertEqual(ep.plan_risk_floor(ui), "medium")
        for path in ("backend/app/main.py", "frontend/package.json", "frontend/package-lock.json", "frontend/prisma/schema.prisma",
                     "frontend/next.config.ts", "frontend/tsconfig.json", "backend/requirements.txt"):
            plan = ep.normalize_execution_plan(good_plan(files_to_update=[{"path": path, "content": "x", "commit_message": "m"}]))[0]
            self.assertEqual(ep.plan_risk_floor(plan), "high", path)
        self.assertEqual(ep.plan_risk_floor(None), "low")
        self.assertEqual(ep.higher_risk("medium", "high"), "high")
        self.assertEqual(ep.higher_risk("critical", "low"), "critical")


if __name__ == "__main__":
    unittest.main()

