"""
THE COO AS SOLE APPROVER: review_executive_task and get_all_audit_logs (app/agents/coo_tools.py). The notify and report tools are in test_simplified_authorization.py.

Proven here, with no database and no network (an in-memory audit log, and the shared approval path replaced by a recorder):
  1. review_cto_task 'approve' goes through the shared approval path AS THE COO's delegate, logs the decision and reasoning, and reports the
     pull request it opened (the execution is simulated);
  2. 'escalate' logs the decision, runs nothing, and leaves the task Pending for the CEO with the COO's reasoning;
  3. get_audit_logs returns only the asked-for agent's entries, honours action_type and limit, and builds its SQL with parameters;
  4. the CAO, CMO and CTO can neither see nor call these tools.
Also: the code floor (a task on the CEO's list is escalated whatever the COO chose), the off switch, which tasks may be decided, and the prompt.

    python -m unittest tests.test_coo_approval   (from backend/)
"""

import asyncio
import contextlib
import io
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import coo_escalation, coo_tools, cto_agent, exec_tools, operational_approval as oa  # noqa: E402
from app.api import authorization  # noqa: E402
from app.governance import ceo_notifications, db, operations_log, task_events  # noqa: E402
from app.prompts import coo_prompt, exec_tools_prompt  # noqa: E402
from tests.test_cto_reads_before_writing import ScriptedClient, answer, text_reply, tool_reply  # noqa: E402
from tests.test_read_file_tool import GuardedTestCase  # noqa: E402

UI = "frontend/src/components/mc/Badge.tsx"
PR_URL = "https://github.com/o/r/pull/3"
WHEN = datetime(2026, 11, 17, 10, 0, 0)
CTO_ASKS = {"review_executive_task": {"task_id": "1", "decision": "approve", "reasoning": "x"}, "get_all_audit_logs": {"agent_id": "all"},
            "notify_ceo_of_critical_decision": {"task_id": "1", "explanation": "x", "recommendation": "y"}, "generate_audit_report": {"period": "week"}}


def cto_request(path=UI, content="export default function Badge() { return null; }\n", **extra):
    """A routine, fully reported CTO task with an execution plan: the kind the COO may approve."""
    base = {"action_summary": "Add a small badge", "risk_level": "low", "impact": "One new visible badge.", "rollback_plan": "Close the pull request.",
            "proposed_commands": [], "executive_summary": {"purpose": "Show activity.", "problem": "Nothing shows it.", "risk": "Very small."},
            "execution_plan": {"branch_name": "agent/badge", "pr_title": "Add a badge", "pr_body": "Adds a badge.",
                               "files_to_update": [{"path": path, "content": content, "commit_message": "Add badge"}]}}
    return {**base, **extra}


def run(coro):
    return asyncio.run(coro)


class FakeAuditStore:
    """The audit log in memory, with the same functions coo_tools reaches the database through, and the shared approval path as a recorder."""

    def __init__(self):
        self.rows, self.applied, self.reviews, self.notes, self.audit_args = {}, [], [], [], None
        self.events, self.notifications = [], []

    def add(self, audit_id, agent="cto", human="Pending", request=None, by=None, score=90):
        self.rows[audit_id] = {"id": audit_id, "agentId": agent, "humanAction": human, "humanActionBy": by, "constitutionalScore": score,
                               "timestamp": WHEN, "authorizationRequest": json.dumps(request) if request is not None else None}

    def request(self, audit_id):
        return json.loads(self.rows[audit_id]["authorizationRequest"])

    def read(self, audit_id):
        return self.rows.get(audit_id)

    def append(self, audit_id, entry):
        request = self.request(audit_id)
        request["coo_decisions"] = [*request.get("coo_decisions", []), entry]
        self.rows[audit_id]["authorizationRequest"] = json.dumps(request)
        return True

    def note(self, audit_id, entry):
        self.notes.append((audit_id, entry))

    def audit(self, agent_id, action_type, limit):
        self.audit_args = (agent_id, action_type, limit)
        rows = [r for r in self.rows.values() if agent_id in (None, r["agentId"]) and (action_type != "pending" or r["humanAction"] == "Pending")]
        return rows[:limit]

    def event(self, audit_id, agent_id, body):
        self.events.append((audit_id, agent_id, body))

    def notify(self, audit_id, agent_id, title, explanation, recommendation, risk):
        self.notifications.append({"audit_id": audit_id, "agent": agent_id, "title": title, "explanation": explanation, "recommendation": recommendation, "risk": risk})
        return len(self.notifications), True

    async def record_review(self, audit_id, route, kind, reasons, coo, *, force=False):
        self.reviews.append({"id": audit_id, "route": route, "kind": kind, "reasons": reasons, "coo": coo, "force": force})
        return True

    async def apply(self, audit_id, action, actor, *, remember=True):
        self.applied.append({"id": audit_id, "action": action, "actor": actor, "remember": remember})
        self.rows[audit_id]["humanAction"] = "Approved" if action == "approve" else "Rejected"
        self.rows[audit_id]["humanActionBy"] = actor
        execution = {"execution": {"status": "succeeded", "pr_url": PR_URL}} if action == "approve" else {}
        return {"human_action": self.rows[audit_id]["humanAction"], **execution}


class CooToolCase(unittest.TestCase):
    """Env, database and approval path replaced for every test. COO_AUTO_APPROVAL is on unless a test class turns it off."""

    switch = "on"

    def setUp(self):
        self.store = FakeAuditStore()
        patches = [mock.patch.dict(os.environ, {oa.ENV_SWITCH: self.switch}), mock.patch.object(db, "is_configured", return_value=True),
                   mock.patch.object(coo_tools, "_read_task_sync", self.store.read), mock.patch.object(coo_tools, "_append_decision_sync", self.store.append),
                   mock.patch.object(coo_tools, "_note_sync", self.store.note), mock.patch.object(coo_tools, "_audit_sync", self.store.audit),
                   mock.patch.object(oa, "_record", self.store.record_review), mock.patch.object(authorization, "apply_decision", self.store.apply),
                   mock.patch.object(task_events, "_record_sync", self.store.event), mock.patch.object(ceo_notifications, "_create_sync", self.store.notify),
                   mock.patch("builtins.print")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def review(self, task_id, decision, reasoning="Routine and well reported."):
        return run(coo_tools.review_executive_task(task_id, decision, reasoning))


class ApproveTests(CooToolCase):
    """Test 1: approve logs the decision and simulates the task's execution."""

    def test_approve_logs_the_decision_and_simulates_the_execution_through_the_shared_path_as_the_coo(self):
        self.store.add(42, request=cto_request())
        text, ok, target, rows = self.review("42", "approve", "A small, additive badge component with a rollback plan.")
        self.assertTrue(ok, text)
        self.assertEqual((target, rows), ("task:42", 1))
        # The execution: the SAME path a person's click uses, as the COO under delegation, and not the CEO's learning loop.
        self.assertEqual(self.store.applied, [{"id": 42, "action": "approve", "actor": oa.ACTOR, "remember": False}])
        self.assertEqual(self.store.rows[42]["humanAction"], "Approved")
        self.assertIn(PR_URL, text)
        self.assertIn("NOT merged", text)  # a pull request is not a live change
        # The log: the reasoning is on the task's own record, as history, and the Operations Summary can count it.
        entry = self.store.request(42)["coo_decisions"][0]
        self.assertEqual((entry["decision"], entry["outcome"], entry["by"]), ("approve", "approved", oa.ACTOR))
        self.assertEqual(entry["reasoning"], "A small, additive badge component with a rollback plan.")
        self.assertEqual([(r["route"], r["force"]) for r in self.store.reviews], [(operations_log.ROUTE_APPROVED, True)])
        self.assertEqual(self.store.notes[0][1], {"decision": "approve", "outcome": "approved"})

    def test_an_approval_without_a_plan_records_the_decision_and_says_nothing_was_run(self):
        request = cto_request(proposed_code_diff="+ a note")
        request["execution_plan"] = None
        self.store.add(7, request=request)
        with mock.patch.object(authorization, "apply_decision", mock.AsyncMock(return_value={"human_action": "Approved"})):
            text, ok, _, _ = self.review(7, "approve")
        self.assertTrue(ok, text)
        self.assertIn("no execution plan", text)
        self.assertNotIn("opened a pull request", text)

    def test_a_failed_pull_request_is_reported_as_it_is_not_as_a_success(self):
        self.store.add(8, request=cto_request())
        failed = {"human_action": "Approved", "execution": {"status": "failed", "pr_url": None, "error": "GitHub 403"}}
        with mock.patch.object(authorization, "apply_decision", mock.AsyncMock(return_value=failed)):
            text, ok, _, _ = self.review(8, "approve")
        self.assertTrue(ok)  # the decision stands
        self.assertIn("could not be completed", text)
        self.assertIn("GitHub 403", text)
        self.assertNotIn("opened a pull request", text)

    def test_reject_cancels_the_task_runs_nothing_and_logs_the_reason(self):
        self.store.add(9, request=cto_request())
        text, ok, _, _ = self.review(9, "reject", "The change is not needed.")
        self.assertTrue(ok, text)
        self.assertEqual(self.store.applied, [{"id": 9, "action": "reject", "actor": oa.ACTOR, "remember": False}])
        self.assertEqual(self.store.rows[9]["humanAction"], "Rejected")
        self.assertIn("Nothing was run", text)
        self.assertEqual(self.store.request(9)["coo_decisions"][0]["reasoning"], "The change is not needed.")
        self.assertEqual(self.store.reviews, [])  # a rejection is not counted as an approved or a sent-to-CEO review


class EscalateTests(CooToolCase):
    """Test 2: escalate logs the decision and flags the task for the CEO."""

    def test_escalate_logs_the_decision_flags_it_for_the_ceo_and_runs_nothing(self):
        self.store.add(42, request=cto_request())
        text, ok, _, _ = self.review(42, "escalate", "It changes how the CEO's approval box behaves; the CEO should decide.")
        self.assertTrue(ok, text)
        self.assertEqual(self.store.applied, [])  # nothing approved, nothing rejected, nothing run
        self.assertEqual(self.store.rows[42]["humanAction"], "Pending")  # it stays Pending, in the CEO's decision list
        self.assertIn("escalated to the CEO", text)
        self.assertIn("not critical, so no notification was sent", text)
        self.assertEqual(self.store.notifications, [])
        entry = self.store.request(42)["coo_decisions"][0]
        self.assertEqual((entry["decision"], entry["outcome"]), ("escalate", "escalated"))
        self.assertIn("the CEO should decide", entry["reasoning"])
        # Flagged for the CEO through the same record the CEO's dashboard and the Operations Summary read, written only if no review exists yet.
        self.assertEqual([(r["route"], r["force"]) for r in self.store.reviews], [(operations_log.ROUTE_DECLINED, False)])
        self.assertEqual(self.store.reviews[0]["reasons"], ["It changes how the CEO's approval box behaves; the CEO should decide."])

    def test_escalating_needs_no_switch_because_it_only_hands_the_task_to_the_ceo(self):
        self.store.add(42, request=cto_request())
        with mock.patch.dict(os.environ, {oa.ENV_SWITCH: "off"}):
            text, ok, _, _ = self.review(42, "escalate")
        self.assertTrue(ok, text)
        self.assertEqual(self.store.applied, [])

    def test_a_second_escalation_adds_to_the_history_and_never_overwrites_the_first(self):
        self.store.add(42, request=cto_request())
        self.review(42, "escalate", "First concern.")
        self.review(42, "escalate", "Second concern.")
        self.assertEqual([d["reasoning"] for d in self.store.request(42)["coo_decisions"]], ["First concern.", "Second concern."])


class TheCodeFloorTests(CooToolCase):
    """The COO is a model. Whatever it decides, code escalates a task on the CEO's list instead of approving it."""

    def approve_expecting_escalation(self, request):
        self.store.rows.clear()
        self.store.notifications.clear()
        self.store.add(5, request=request)
        text, ok, _, _ = self.review(5, "approve", "Looks fine to me.")
        self.assertFalse(ok, text)
        self.assertIn("NOT APPROVED: code escalated task #5 to the CEO", text)
        self.assertEqual(self.store.applied, [])  # nothing was approved or run
        self.assertEqual(self.store.rows[5]["humanAction"], "Pending")
        self.assertEqual([n["audit_id"] for n in self.store.notifications], [5])  # and the CEO was notified, by code
        entry = self.store.request(5)["coo_decisions"][0]
        self.assertEqual((entry["decision"], entry["outcome"]), ("approve", "escalated"))  # on record: the COO said approve, code escalated it
        self.assertIn("Code escalated it instead", entry["reasoning"])
        return text

    def test_a_database_schema_change_is_escalated_even_when_the_coo_approves(self):
        text = self.approve_expecting_escalation(cto_request(path="frontend/prisma/schema.prisma", content="model A {\n  id Int @id\n}\n"))
        self.assertIn("database schema or migration file", text)

    def test_a_migration_file_is_escalated(self):
        self.approve_expecting_escalation(cto_request(path="frontend/prisma/migrations/20261117000000_x/migration.sql", content="ALTER TABLE a ADD COLUMN b TEXT;\n"))

    def test_an_authentication_change_is_escalated(self):
        text = self.approve_expecting_escalation(cto_request(path="frontend/src/lib/mc-session.ts", content="export const x = 1;\n"))
        self.assertIn("security or authentication file", text)

    def test_a_governance_change_is_escalated(self):
        text = self.approve_expecting_escalation(cto_request(path="backend/app/agents/operational_approval.py", content="X = 1\n"))
        self.assertIn("governance or approval-rule file", text)

    def test_a_new_dependency_is_escalated(self):
        text = self.approve_expecting_escalation(cto_request(path="backend/requirements.txt", content="fastapi==0.162.0\n"))
        self.assertIn("dependency, build or deployment file", text)

    def test_a_billing_file_is_escalated(self):
        self.approve_expecting_escalation(cto_request(path="frontend/src/lib/billing.ts", content="export const x = 1;\n"))

    def test_a_third_party_integration_named_in_the_summary_is_escalated(self):
        text = self.approve_expecting_escalation(cto_request(action_summary="Add a new third-party API integration for weather data"))
        self.assertIn("third-party service or dependency", text)

    def test_commands_critical_risk_and_a_missing_rollback_plan_are_escalated(self):
        self.approve_expecting_escalation(cto_request(proposed_commands=["npm run build"]))
        self.approve_expecting_escalation(cto_request(risk_level="critical"))

    def test_a_plan_that_fails_the_self_check_or_has_no_rollback_is_sent_back_not_up_and_the_ceo_is_not_told(self):
        for request, why in ((cto_request(content="// ... rest of the file\n"), "failed the code self-check"), (cto_request(rollback_plan=""), "no rollback plan")):
            self.store.rows.clear()
            self.store.add(5, request=request)
            text, ok, _, _ = self.review(5, "approve")
            self.assertFalse(ok, text)
            self.assertIn("is not ready", text)
            self.assertIn(why, text)
            self.assertIn("Reject it with these reasons", text)
            self.assertEqual((self.store.applied, self.store.notifications), ([], []))

    def test_the_floor_does_not_apply_to_a_reject_because_rejecting_is_always_safe(self):
        self.store.add(5, request=cto_request(path="frontend/prisma/schema.prisma", content="model A {\n}\n"))
        text, ok, _, _ = self.review(5, "reject", "Not needed.")
        self.assertTrue(ok, text)
        self.assertEqual(self.store.applied[0]["action"], "reject")

    def test_routine_tasks_on_the_coos_can_approve_list_have_no_reason_to_escalate(self):
        routine = {
            "a UI improvement": cto_request(),
            "a test addition": cto_request(path="backend/tests/test_new_thing.py", content="import unittest\n\n\nclass T(unittest.TestCase):\n    pass\n"),
            "documentation": cto_request(path="docs/NOTES.md", content="# Notes\n\nA plain note.\n"),
            "a prompt optimization": cto_request(path="backend/app/prompts/cto_prompt.py", content='PROMPT = "Be brief."\n'),
            "a bug fix with no migration": cto_request(action_summary="Fix a typo", impact="One label. No migration needed."),
        }
        for name, request in routine.items():
            self.assertEqual(coo_escalation.escalation_reasons(request), [], name)

    def test_a_request_the_checker_cannot_read_is_itself_a_reason_to_escalate(self):
        self.assertTrue(coo_escalation.escalation_reasons({"execution_plan": {"files_to_update": [{"path": None}]}, "risk_level": ["x"]}))
        self.assertTrue(coo_escalation.escalation_reasons({}))  # not fully reported


class TheSwitchTests(CooToolCase):
    switch = "off"

    def test_with_the_switch_off_approve_and_reject_decide_nothing_and_say_so(self):
        for decision in ("approve", "reject"):
            self.store.add(3, request=cto_request())
            text, ok, _, _ = self.review(3, decision)
            self.assertFalse(ok)
            self.assertIn("switched OFF", text)
            self.assertIn(oa.ENV_SWITCH, text)
            self.assertEqual((self.store.applied, self.store.rows[3]["humanAction"]), ([], "Pending"))


class WhichTaskMayBeDecidedTests(CooToolCase):
    def test_the_ctos_caos_and_cmos_tasks_may_be_decided_but_never_the_coos_own(self):
        for agent in ("cto", "cao", "cmo"):
            self.store.rows.clear()
            self.store.add(10, agent=agent, request=cto_request())
            text, ok, _, _ = self.review(10, "approve")
            self.assertTrue(ok, (agent, text))
        self.assertEqual([a["id"] for a in self.store.applied], [10, 10, 10])
        self.store.rows.clear()
        self.store.applied.clear()
        self.store.add(10, agent="coo", request=cto_request())
        text, ok, _, _ = self.review(10, "approve")
        self.assertFalse(ok)
        self.assertIn("never your own", text)
        self.assertEqual(self.store.applied, [])

    def test_a_task_someone_already_decided_is_refused_because_a_decision_is_final(self):
        self.store.add(11, human="Approved", by="joe", request=cto_request())
        text, ok, _, _ = self.review(11, "reject")
        self.assertFalse(ok)
        self.assertIn("already Approved by joe", text)
        self.assertEqual(self.store.applied, [])

    def test_a_missing_task_or_one_without_a_request_is_refused(self):
        self.assertIn("there is no audit entry #99", self.review(99, "approve")[0])
        self.store.add(12, request=None)
        self.assertIn("no authorization request", self.review(12, "approve")[0])

    def test_a_bad_id_decision_or_empty_reasoning_is_refused_before_anything_is_read(self):
        self.store.add(13, request=cto_request())
        for args in (("abc", "approve", "x"), (True, "approve", "x"), (-1, "approve", "x"), (0, "approve", "x"), (None, "approve", "x"),
                     (13, "delete", "x"), (13, None, "x"), (13, "approve", ""), (13, "approve", "   "), (13, "approve", None), (13, "approve", 5)):
            text, ok, _, _ = run(coo_tools.review_executive_task(*args))
            self.assertFalse(ok, args)
            self.assertTrue(text.startswith("ERROR:"), args)
        self.assertEqual((self.store.applied, self.store.rows[13]["humanAction"]), ([], "Pending"))

    def test_the_task_id_may_be_a_number_a_string_or_hash_prefixed(self):
        self.assertEqual([coo_tools.parse_task_id(v) for v in (42, "42", " #42 ")], [42, 42, 42])

    def test_a_failure_in_the_shared_approval_path_decides_nothing_and_is_reported(self):
        self.store.add(14, request=cto_request())
        with mock.patch.object(authorization, "apply_decision", mock.AsyncMock(side_effect=RuntimeError("boom"))):
            text, ok, _, _ = self.review(14, "approve")
        self.assertFalse(ok)
        self.assertIn("NOT DECIDED", text)
        self.assertNotIn("coo_decisions", self.store.request(14))  # nothing was recorded as decided

    def test_a_secret_pasted_into_the_reasoning_is_masked_before_it_is_stored(self):
        self.store.add(15, request=cto_request())
        secret = "sk-ant-" + "A" * 30
        self.review(15, "escalate", f"The CTO pasted {secret} in the summary.")
        self.assertNotIn(secret, json.dumps(self.store.request(15)))

    def test_a_long_reasoning_is_capped(self):
        self.store.add(16, request=cto_request())
        self.review(16, "escalate", "word " * 1000)
        self.assertLessEqual(len(self.store.request(16)["coo_decisions"][0]["reasoning"]), coo_tools.MAX_REASONING_CHARS)


class GetAuditLogsTests(CooToolCase):
    """Test 3: get_audit_logs retrieves the filtered logs of a specific agent."""

    def fill(self):
        self.store.add(1, agent="cto", request=cto_request())
        self.store.add(2, agent="cao", request={"action_summary": "A feeding protocol", "risk_level": "high"})
        self.store.add(3, agent="cto", human="Approved", by=oa.ACTOR, request=cto_request(action_summary="Fix a label"))
        self.store.add(4, agent="cmo", request=None)

    def test_it_retrieves_the_logs_of_one_agent_only(self):
        self.fill()
        text, ok, target, rows = run(coo_tools.get_all_audit_logs("cto"))
        self.assertTrue(ok, text)
        self.assertEqual((target, rows), ("audit_log", 2))
        self.assertEqual(self.store.audit_args, ("cto", None, coo_tools.DEFAULT_LIMIT))
        self.assertIn("[1]", text)
        self.assertIn("[3]", text)
        self.assertNotIn("[2]", text)  # the CAO's entry
        self.assertNotIn("[4]", text)  # the CMO's entry
        self.assertIn("metadata only", text)

    def test_it_filters_by_action_type_and_limit_and_reads_across_every_executive_with_all(self):
        self.fill()
        text, ok, _, rows = run(coo_tools.get_all_audit_logs("cto", "pending", 1))
        self.assertEqual((ok, rows, self.store.audit_args), (True, 1, ("cto", "pending", 1)))
        self.assertIn("[1]", text)
        self.assertNotIn("[3]", text)  # approved, not pending
        _, _, _, everyone = run(coo_tools.get_all_audit_logs("all"))
        self.assertEqual((everyone, self.store.audit_args[0]), (4, None))

    def test_a_pending_cto_task_shows_what_the_coo_needs_to_decide_it(self):
        self.store.add(20, request=cto_request(path="frontend/prisma/schema.prisma", content="model A {\n}\n"))
        text, _, _, _ = run(coo_tools.get_all_audit_logs("cto", "pending"))
        self.assertIn("changes: frontend/prisma/schema.prisma", text)
        self.assertIn("impact: One new visible badge.", text)
        self.assertIn("code check: CRITICAL, so it is the CEO's:", text)
        self.store.rows.clear()
        self.store.add(21, request=cto_request())
        self.assertIn("code check: no critical reason found; the COO may decide it", run(coo_tools.get_all_audit_logs("cto", "pending"))[0])

    def test_it_never_returns_the_text_of_a_question_or_an_answer(self):
        self.store.add(30, request=cto_request())
        self.store.rows[30].update({"query": "SECRET QUESTION TEXT", "response": "SECRET ANSWER TEXT"})
        text, _, _, _ = run(coo_tools.get_all_audit_logs("cto"))
        self.assertNotIn("SECRET", text)

    def test_the_limit_is_clamped_and_a_bad_agent_or_action_type_is_refused(self):
        run(coo_tools.get_all_audit_logs("cto", None, 9999))
        self.assertEqual(self.store.audit_args[2], coo_tools.MAX_LIMIT)
        run(coo_tools.get_all_audit_logs("cto", None, 0))
        self.assertEqual(self.store.audit_args[2], 1)
        run(coo_tools.get_all_audit_logs("cto", None, True))  # a bool is not a number
        self.assertEqual(self.store.audit_args[2], coo_tools.DEFAULT_LIMIT)
        for args in (("intern",), (None,), ("cto; DROP TABLE x",), ("cto", "everything")):
            text, ok, _, _ = run(coo_tools.get_all_audit_logs(*args))
            self.assertFalse(ok, args)
            self.assertTrue(text.startswith("ERROR:"), args)

    def test_no_matching_entries_is_said_plainly(self):
        text, ok, _, rows = run(coo_tools.get_all_audit_logs("cto"))
        self.assertEqual((ok, rows, text), (True, 0, "The audit log has no matching entries."))


class FakeCursor:
    description = [("id",), ("agentId",)]

    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=()):
        self.log.append((sql, params))

    def fetchall(self):
        return [(1, "cto")]


class TheRealQueryTests(unittest.TestCase):
    """The SQL coo_tools builds, captured from a fake connection: every filter is a parameter, never text from the model."""

    def query(self, agent, action, limit=20):
        log = []

        @contextlib.contextmanager
        def transaction():
            yield SimpleNamespace(cursor=lambda: FakeCursor(log))

        with mock.patch.object(db, "transaction", transaction):
            rows = coo_tools._audit_sync(agent, action, limit)
        return log[0], rows

    def test_the_agent_filter_is_a_parameter_and_the_rows_come_back_as_dicts(self):
        (sql, params), rows = self.query("cto", None)
        self.assertIn('"agentId" = %s', sql)
        self.assertEqual(params, ("cto", 20))
        self.assertEqual(rows, [{"id": 1, "agentId": "cto"}])

    def test_every_action_type_becomes_one_fixed_condition_with_its_values_as_parameters(self):
        for action, (condition, extra) in coo_tools.ACTION_TYPES.items():
            (sql, params), _ = self.query("cto", action, 5)
            self.assertIn(condition, sql, action)
            self.assertEqual(params, ("cto", *extra, 5), action)
            self.assertEqual(sql.count("%s"), len(params), action)

    def test_no_filter_reads_everyone_newest_first(self):
        (sql, params), _ = self.query(None, None, 3)
        self.assertNotIn("WHERE", sql)
        self.assertIn('ORDER BY "timestamp" DESC', sql)
        self.assertEqual(params, (3,))


class OnlyTheCooHoldsTheToolsTests(GuardedTestCase):
    """Test 4: the CAO, the CMO and the CTO can neither see nor call any of the COO's four authority tools."""

    names = coo_tools.TOOL_NAMES

    def test_the_tools_are_in_the_coos_list_and_in_nobody_elses(self):
        for name in self.names:
            self.assertIn(name, exec_tools.tool_names("coo"))
            for role in ("cao", "cmo", "cto", "ceo", "intern", ""):
                self.assertNotIn(name, exec_tools.tool_names(role), role)
        self.assertEqual({s["name"] for s in coo_tools.SPECS}, set(self.names))

    def test_the_other_tool_lists_are_unchanged_and_the_new_tools_sit_before_memory_in_the_coos(self):
        memory = ["save_memory", "search_memory"]
        self.assertEqual(exec_tools.tool_names("coo"), ["read_farm_records", "read_audit_log", "read_operations_summary", "diagnose_operational_routing",
                                                        "manage_fetch_queue", "review_executive_task", "notify_ceo_of_critical_decision", "get_all_audit_logs",
                                                        "generate_audit_report", *memory])
        self.assertEqual(exec_tools.tool_names("cao"), ["read_farm_records", "read_knowledge_base", "diagnose_recommendation", "submit_plan_for_approval", *memory])
        self.assertEqual(exec_tools.tool_names("cmo"), ["read_brand_file", "diagnose_asset_request", "submit_plan_for_approval", *memory])

    def test_every_other_role_is_refused_by_the_handler_even_if_it_somehow_names_the_tool(self):
        for role in ("cao", "cmo", "cto", "ceo", "x"):
            for name, args in CTO_ASKS.items():
                text, ok, _, rows = run(coo_tools.handle(role, name, args, exec_tools.ExecOutcome(role)))
                self.assertEqual((ok, rows, text), (False, None, f"{name} is not one of your tools"), (role, name))

    def test_the_executive_tool_router_refuses_the_cao_and_the_cmo_before_any_database_is_touched(self):
        for role in ("cao", "cmo"):
            for name, args in CTO_ASKS.items():
                block = SimpleNamespace(type="tool_use", id="tu_1", name=name, input=args)
                with mock.patch.object(coo_tools, "review_executive_task", side_effect=AssertionError("reached the tool")), \
                        mock.patch.object(coo_tools, "get_all_audit_logs", side_effect=AssertionError("reached the tool")):
                    result = run(exec_tools.handle_block(role, block, exec_tools.ExecOutcome(role)))
                self.assertTrue(result["is_error"], (role, name))
                self.assertIn("is not one of your tools", result["content"])

    def test_the_coo_through_the_router_reaches_the_tool_and_an_audit_read_does_not_spend_the_read_budget(self):
        store = FakeAuditStore()
        store.add(1, request=cto_request())
        outcome = exec_tools.ExecOutcome("coo")
        block = SimpleNamespace(type="tool_use", id="tu_9", name="get_all_audit_logs", input={"agent_id": "cto", "action_type": "pending"})
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(coo_tools, "_audit_sync", store.audit), \
                mock.patch.object(exec_tools, "_log", mock.AsyncMock()), mock.patch("builtins.print"):
            result = run(exec_tools.handle_block("coo", block, outcome))
        self.assertEqual((result["is_error"], result["tool_use_id"]), (False, "tu_9"))
        self.assertIn("[1]", result["content"])
        self.assertEqual((outcome.audit_queries, outcome.attempted()), (1, 0))

    def test_the_cto_is_not_offered_the_tools_and_is_told_so_if_it_asks_for_one_anyway(self):
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        for name in self.names:
            client = ScriptedClient([tool_reply("x", name=name), text_reply(answer({}))])
            with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(coo_tools, "review_executive_task", side_effect=AssertionError("the CTO reached the tool")), \
                    mock.patch.object(coo_tools, "get_all_audit_logs", side_effect=AssertionError("the CTO reached the tool")):
                run(cto_agent.get_cto_result("approve my own task", use_rag=False, audit=False))
            self.assertNotIn(name, [t["name"] for t in client.calls[0]["tools"]], name)
            tool_result = client.calls[1]["messages"][-1]["content"][0]
            self.assertTrue(tool_result["is_error"], name)
            self.assertIn("Unknown tool", tool_result["content"])

    def test_the_model_is_offered_the_new_tools_only_when_it_is_the_coo(self):
        messages = [{"role": "user", "content": "hi"}]
        coo = ScriptedClient([text_reply("{}")])
        with mock.patch("builtins.print"):
            run(exec_tools.tool_loop(coo, "coo", model="m", max_tokens=10, system="s", messages=messages))
        self.assertEqual([t["name"] for t in coo.calls[0]["tools"]], exec_tools.tool_names("coo"))
        for role in ("cao", "cmo"):
            other = ScriptedClient([text_reply("{}")])
            with mock.patch("builtins.print"):
                run(exec_tools.tool_loop(other, role, model="m", max_tokens=10, system="s", messages=messages))
            self.assertFalse(set(self.names) & {t["name"] for t in other.calls[0]["tools"]}, role)


class BoundsTests(CooToolCase):
    def test_decisions_and_audit_reads_per_answer_are_bounded(self):
        outcome = exec_tools.ExecOutcome("coo")
        extra = coo_tools.MAX_DECISIONS_PER_TURN + 2
        for n in range(extra):
            self.store.add(100 + n, request=cto_request())
        results = [run(coo_tools.handle("coo", "review_executive_task", {"task_id": str(100 + n), "decision": "escalate", "reasoning": "r"}, outcome))
                   for n in range(extra)]
        self.assertEqual([r[1] for r in results], [True] * coo_tools.MAX_DECISIONS_PER_TURN + [False, False])
        self.assertIn("Decision limit reached", results[-1][0])
        reads = [run(coo_tools.handle("coo", "get_all_audit_logs", {"agent_id": "all"}, outcome)) for _ in range(coo_tools.MAX_AUDIT_QUERIES_PER_TURN + 1)]
        self.assertEqual([r[1] for r in reads], [True] * coo_tools.MAX_AUDIT_QUERIES_PER_TURN + [False])
        self.assertIn("Audit-read limit reached", reads[-1][0])

    def test_a_crash_inside_a_tool_never_escapes_the_handler(self):
        with mock.patch.object(coo_tools, "review_executive_task", side_effect=RuntimeError("boom")):
            text, ok, _, _ = run(coo_tools.handle("coo", "review_executive_task", {}, exec_tools.ExecOutcome("coo")))
        self.assertFalse(ok)
        self.assertIn("Nothing was changed", text)


class ThePromptTests(unittest.TestCase):
    def test_the_coo_prompt_makes_it_the_sole_authorization_point_with_both_lists(self):
        text = coo_prompt.COO_SYSTEM_PROMPT
        self.assertIn("APPROVAL AUTHORITY (you are the sole authorization point for all executives)", text)
        for needle in ("You are the sole authorization point for the CTO, the CAO and the CMO", "Approve low, medium and high risk tasks",
                       "For critical risk, escalate to the CEO with a plain-language explanation via notify_ceo_of_critical_decision",
                       "Conduct periodic audits using get_all_audit_logs and generate_audit_report", "APPROVE what is low, medium or high risk and not critical",
                       "CRITICAL goes to the CEO:", "database schema changes", "security or authentication", "governance or approval rules", "billing or financial logic",
                       "new third-party services", "NOT READY", "Never log anything by hand"):
            self.assertIn(needle, text, needle)

    def test_the_prompt_says_code_backs_the_coo_up_and_that_an_approval_is_not_live(self):
        text = coo_prompt.COO_SYSTEM_PROMPT
        self.assertIn("escalates a critical task whatever you chose, and notifies the CEO itself", text)
        self.assertIn("a person still reviews and merges", text)
        self.assertIn('never as "I have approved"', text)

    def test_the_existing_operational_approval_rules_are_untouched(self):
        text = coo_prompt.COO_SYSTEM_PROMPT
        self.assertIn("(1) Does this maintain system integrity? (2) Is it safe? (3) Is it fully reported? If all three are yes, you approve", text)
        self.assertIn("You cannot pay, schedule, order, approve", text)
        self.assertIn("The system, not you, decides which tasks are even eligible", text)

    def test_the_prompt_never_asks_the_model_to_hide_its_reasoning(self):
        from app.agents.plan_checks import REFUSED_WORDING

        for text in (coo_prompt.COO_SYSTEM_PROMPT, exec_tools_prompt.COO_TOOLS_PROMPT):
            for wording in REFUSED_WORDING:
                self.assertNotIn(wording, text.lower(), wording)

    def test_only_the_coo_tool_prompt_names_the_new_tools(self):
        for name in coo_tools.TOOL_NAMES:
            self.assertIn(name, exec_tools_prompt.COO_TOOLS_PROMPT + coo_prompt.COO_SYSTEM_PROMPT, name)
            for text in (exec_tools_prompt.CAO_TOOLS_PROMPT, exec_tools_prompt.CMO_TOOLS_PROMPT):
                self.assertNotIn(name, text, name)
        self.assertLess(len(coo_prompt.COO_SYSTEM_PROMPT + exec_tools_prompt.COO_TOOLS_PROMPT) // 4, 16_500)


if __name__ == "__main__":
    unittest.main()
