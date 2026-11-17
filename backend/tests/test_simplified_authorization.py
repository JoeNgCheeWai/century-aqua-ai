"""
THE SIMPLIFIED AUTHORIZATION WORKFLOW: the COO is the sole approver, the CEO hears only about critical decisions, everything is logged automatically.

Proven here, with no database and no network (the in-memory store from test_coo_approval, and fake connections for the SQL):
  1. the COO can approve a task from EACH of the CTO, CAO and CMO, and never its own;
  2. a CRITICAL escalation notifies the CEO, by code, whatever the COO chose; a non-critical one does not; an incomplete one is sent back, not up;
  3. audit events are written AUTOMATICALLY to the executive's own log AND the COO's (plan_submitted, coo_decision, ceo_notified, ceo_decision, execution);
  4. the COO can generate an audit report, and its checks catch an unapproved execution, a nobody-decided approval and a stale pending task;
  5. the notification store and its endpoints, and who may use the new tools.

    python -m unittest tests.test_simplified_authorization   (from backend/)
"""

import asyncio
import contextlib
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import coo_escalation, coo_tools, exec_tools, operational_approval as oa  # noqa: E402
from app.api import authorization, coo_decisions  # noqa: E402
from app.governance import audit, audit_report, ceo_notifications, db, task_events  # noqa: E402
from tests.test_coo_approval import CooToolCase, FakeAuditStore, WHEN, cto_request, run  # noqa: E402


def tool_plan(**extra):
    """A plan the way submit_plan_for_approval stores it: a summary and file paths, no file contents, no execution_plan."""
    base = {"action_summary": "Add a Clear all button", "risk_level": "low", "impact": "Files the plan would change: frontend/src/components/x.tsx.",
            "rollback_plan": "Nothing is changed by approving this plan.", "plan_summary": "Add a button that clears the list.",
            "files_to_change": ["frontend/src/components/x.tsx"], "submitted_via": "submit_plan_for_approval", "execution_plan": None, "proposed_commands": []}
    return {**base, **extra}


class CriticalVersusIncompleteTests(unittest.TestCase):
    def test_a_tool_submitted_plan_naming_the_schema_is_critical_from_its_paths(self):
        reasons = coo_escalation.critical_reasons(tool_plan(files_to_change=["frontend/prisma/schema.prisma"]))
        self.assertTrue(any("database schema" in r for r in reasons), reasons)

    def test_a_routine_tool_submitted_plan_is_neither_critical_nor_incomplete(self):
        self.assertEqual((coo_escalation.critical_reasons(tool_plan()), coo_escalation.completeness_reasons(tool_plan())), ([], []))

    def test_a_missing_rollback_or_summary_is_incomplete_and_not_critical(self):
        request = tool_plan(rollback_plan="", plan_summary="")
        self.assertEqual(coo_escalation.critical_reasons(request), [])
        problems = coo_escalation.completeness_reasons(request)
        self.assertTrue(any("rollback" in p for p in problems) and any("plan summary" in p for p in problems), problems)

    def test_critical_risk_commands_and_a_cmo_deployment_command_are_critical(self):
        for request in (tool_plan(risk_level="critical"), tool_plan(proposed_commands=["npm i x"]), tool_plan(deployment_commands=["vercel deploy"])):
            self.assertTrue(coo_escalation.critical_reasons(request), request)

    def test_a_recommendation_about_animals_or_above_medium_risk_is_reserved_for_the_ceo_by_the_rule_apply_decision_enforces(self):
        rec = {"kind": "recommendation", "action_summary": "Change the feeding rate for the tilapia ponds", "risk_level": "low", "impact": "x"}
        self.assertTrue(coo_tools._reserved_for_ceo(rec))
        self.assertTrue(coo_tools._reserved_for_ceo({**rec, "action_summary": "Reword a heading", "risk_level": "high"}))
        self.assertEqual(coo_tools._reserved_for_ceo({**rec, "action_summary": "Reword a heading"}), [])
        self.assertEqual(coo_tools._reserved_for_ceo(tool_plan()), [])  # not a recommendation: the critical rule above covers a plan

class TheCooDecidesForEveryExecutiveTests(CooToolCase):
    def test_it_can_approve_a_task_from_each_of_the_cto_the_cao_and_the_cmo(self):
        for agent in ("cto", "cao", "cmo"):
            self.store.rows.clear()
            self.store.applied.clear()
            self.store.add(10, agent=agent, request=cto_request())
            text, ok, _, _ = self.review(10, "approve", "Routine and complete.")
            self.assertTrue(ok, (agent, text))
            self.assertEqual(self.store.applied, [{"id": 10, "action": "approve", "actor": oa.ACTOR, "remember": False}], agent)
            self.assertIn("approved by the COO", text)
            self.assertIn("proceed", text)

    def test_it_can_reject_for_each_and_never_decides_its_own(self):
        for agent in ("cto", "cao", "cmo"):
            self.store.rows.clear()
            self.store.add(11, agent=agent, request=cto_request())
            self.assertTrue(self.review(11, "reject", "Not needed.")[1], agent)
        self.store.rows.clear()
        self.store.add(12, agent="coo", request=cto_request())
        text, ok, _, _ = self.review(12, "approve")
        self.assertFalse(ok)
        self.assertIn("never your own", text)

    def test_a_tool_submitted_plan_from_the_cto_is_approved_when_routine(self):
        self.store.add(13, request=tool_plan())
        # The real shared path opens no pull request for a plan with execution_plan None, so its result carries no "execution".
        with mock.patch.object(authorization, "apply_decision", mock.AsyncMock(return_value={"human_action": "Approved"})):
            text, ok, _, _ = self.review(13, "approve", "A small interface change.")
        self.assertTrue(ok, text)
        self.assertIn("no execution plan", text)  # approving a plan records the decision; no code runs
        self.assertNotIn("pull request:", text)


class CriticalEscalationNotifiesTheCeoTests(CooToolCase):
    def critical(self, agent="cto"):
        self.store.rows.clear()
        self.store.notifications.clear()
        self.store.add(20, agent=agent, request=tool_plan(files_to_change=["frontend/prisma/schema.prisma"], action_summary="Add a column to the schema"))

    def test_escalating_a_critical_task_notifies_the_ceo_by_code(self):
        self.critical()
        text, ok, _, _ = self.review(20, "escalate", "It changes the database.")
        self.assertTrue(ok, text)
        self.assertEqual(len(self.store.notifications), 1)
        note = self.store.notifications[0]
        self.assertEqual((note["audit_id"], note["agent"], note["risk"]), (20, "cto", "critical"))
        self.assertIn("database schema", note["explanation"])
        self.assertIn("The CEO was notified", text)
        self.assertEqual(self.store.rows[20]["humanAction"], "Pending")  # the CEO decides it

    def test_an_approve_of_a_critical_task_is_escalated_AND_notified_never_approved(self):
        for agent in ("cto", "cao", "cmo"):
            self.critical(agent)
            text, ok, _, _ = self.review(20, "approve", "Looks fine.")
            self.assertFalse(ok, agent)
            self.assertIn("NOT APPROVED: code escalated task #20 to the CEO", text)
            self.assertEqual((self.store.applied, len(self.store.notifications)), ([], 1), agent)

    def test_a_critical_recommendation_from_the_cao_about_animals_notifies_the_ceo(self):
        self.store.add(21, agent="cao", request={"kind": "recommendation", "action_summary": "Raise the feeding rate in pond 3", "risk_level": "low",
                                                 "impact": "Fish eat more.", "rollback_plan": "Revert.", "proposed_content": "Feed 3 percent."})
        text, ok, _, _ = self.review(21, "approve", "Fine.")
        self.assertFalse(ok, text)
        self.assertEqual(len(self.store.notifications), 1)
        self.assertIn("animals", self.store.notifications[0]["explanation"])

    def test_notify_ceo_gives_the_plain_language_why_and_the_recommendation_and_refreshes_not_duplicates(self):
        self.critical()
        text, ok, target, _ = run(coo_tools.notify_ceo_of_critical_decision(20, "It changes how we store data, so a mistake could lose records.", "Approve, after a backup."))
        self.assertTrue(ok, text)
        self.assertEqual(target, "task:20")
        note = self.store.notifications[-1]
        self.assertEqual((note["explanation"], note["recommendation"]), ("It changes how we store data, so a mistake could lose records.", "Approve, after a backup."))
        self.assertIn("in-app only", text)  # it never claims an email or a push

    def test_notify_refuses_a_missing_explanation_a_missing_recommendation_a_bad_id_and_a_decided_task(self):
        self.critical()
        for args in ((20, "", "advice"), (20, "why", ""), (20, None, None), ("abc", "why", "advice"), (999, "why", "advice")):
            text, ok, _, _ = run(coo_tools.notify_ceo_of_critical_decision(*args))
            self.assertFalse(ok, args)
            self.assertTrue(text.startswith("ERROR:"), (args, text))
        self.store.rows[20]["humanAction"] = "Approved"
        self.assertIn("already Approved", run(coo_tools.notify_ceo_of_critical_decision(20, "why", "advice"))[0])
        self.assertEqual(self.store.notifications, [])

    def test_a_failed_notification_is_reported_as_not_sent_never_as_sent(self):
        self.critical()
        with mock.patch.object(ceo_notifications, "create", mock.AsyncMock(return_value=(None, "the database is down"))):
            text, ok, _, _ = run(coo_tools.notify_ceo_of_critical_decision(20, "why", "advice"))
            self.assertFalse(ok)
            self.assertIn("NOT NOTIFIED", text)
            esc, _, _, _ = self.review(20, "escalate", "Critical.")
            self.assertIn("could NOT be notified", esc)

    def test_a_notification_needs_no_approval_switch_because_it_only_tells_the_ceo(self):
        self.critical()
        with mock.patch.dict(os.environ, {oa.ENV_SWITCH: "off"}):
            self.assertTrue(run(coo_tools.notify_ceo_of_critical_decision(20, "why", "advice"))[1])
            self.assertTrue(self.review(20, "escalate", "Critical.")[1])

class FakeCursor:
    """Captures SQL. ``found`` is what the next SELECT returns as one column named id."""

    description = [("id",)]

    def __init__(self, log, found):
        self.log, self.found = log, found

    def execute(self, sql, params=()):
        self.log.append((sql, params))

    def fetchall(self):
        return [(n,) for n in self.found]


def fake_transaction(log, found=()):
    @contextlib.contextmanager
    def transaction():
        yield SimpleNamespace(cursor=lambda: FakeCursor(log, list(found)))

    return transaction


class AutomaticAuditLoggingTests(unittest.TestCase):
    def setUp(self):
        task_events.reset_breaker()
        self.addCleanup(task_events.reset_breaker)

    def test_one_event_writes_two_rows_the_executives_own_and_the_coos(self):
        log = []
        with mock.patch.object(db, "transaction", fake_transaction(log)):
            task_events._record_sync(7, "cmo", task_events.payload(7, "cmo", "plan_submitted", {"risk": "low"}))
        self.assertEqual(len(log), 2)
        self.assertEqual([(p[0], p[1]) for _, p in log], [("ai_cmo", "task_event"), ("ai_coo", "coo_oversight")])
        self.assertEqual({p[3] for _, p in log}, {"7"})
        self.assertEqual(log[0][1][4], log[1][1][4])  # the same details on both
        self.assertEqual(json.loads(log[0][1][4])["event"], "plan_submitted")

    def test_the_payload_masks_secrets_and_caps_long_text(self):
        body = task_events.payload(1, "cto", "coo_decision", {"reasoning": "key sk-ant-" + "A" * 30, "explanation": "word " * 500})
        self.assertNotIn("sk-ant-", json.dumps(body))
        self.assertLessEqual(len(body["explanation"]), task_events.MAX_TEXT)

    def test_record_never_raises_trips_a_breaker_and_skips_until_the_cooldown(self):
        calls = []

        def boom(*args):
            calls.append(args)
            raise RuntimeError("database down")

        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(task_events, "_record_sync", boom), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(run(task_events.record(1, "cto", "plan_submitted")))
            self.assertFalse(run(task_events.record(2, "cto", "plan_submitted")))
        self.assertEqual(len(calls), 1)  # the second was skipped by the breaker: a slow database costs one write, not one per event

    def test_an_unknown_event_agent_or_a_missing_id_writes_nothing(self):
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(task_events, "_record_sync") as write, contextlib.redirect_stdout(io.StringIO()):
            for args in ((None, "cto", "plan_submitted"), (1, "", "plan_submitted"), (1, "cto", "made_up"), (1, "cto; DROP", "plan_submitted")):
                self.assertFalse(run(task_events.record(*args)), args)
        write.assert_not_called()

    def test_a_submitted_plan_is_logged_automatically_when_the_answer_is_recorded(self):
        events = []
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(audit, "_record_sync", return_value=77), \
                mock.patch.object(task_events, "_record_sync", lambda i, a, b: events.append((i, a, b))), contextlib.redirect_stdout(io.StringIO()):
            audit_id, _ = run(audit.record_response("cto", "u", "q", "r", [], authorization_request=tool_plan()))
            none_id, _ = run(audit.record_response("cto", "u", "q", "r", []))
        self.assertEqual((audit_id, none_id), (77, 77))
        self.assertEqual([(i, a, b["event"]) for i, a, b in events], [(77, "cto", "plan_submitted")])  # an answer with no request logs no plan event
        self.assertEqual(events[0][2]["via"], "submit_plan_for_approval")

    def test_the_coos_decision_and_the_ceo_notification_are_logged_automatically(self):
        class Case(CooToolCase):
            def runTest(self):
                pass

        case = Case()
        case.setUp()
        try:
            case.store.add(30, request=tool_plan(files_to_change=["frontend/prisma/schema.prisma"]))
            case.review(30, "escalate", "It changes the database.")
            events = [(i, a, b["event"]) for i, a, b in case.store.events]
            self.assertEqual(events, [(30, "cto", "coo_decision"), (30, "cto", "ceo_notified")])
            self.assertEqual(case.store.events[0][2]["outcome"], "escalated")
        finally:
            case.doCleanups()

    def test_a_persons_decision_and_an_execution_are_logged_and_the_notification_is_settled(self):
        events, settled = [], []
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(task_events, "_record_sync", lambda i, a, b: events.append(b)), \
                mock.patch.object(ceo_notifications, "_resolve_for_audit_sync", lambda i, who: settled.append((i, who)) or 1), contextlib.redirect_stdout(io.StringIO()):
            run(authorization._log_task_events(5, "cto", "approve", "joe", {"status": "succeeded", "pr_url": "https://github.com/o/r/pull/9"}))
            run(authorization._log_task_events(6, "cto", "approve", oa.ACTOR, None))
        self.assertEqual([(b["event"], b["audit_id"]) for b in events], [("ceo_decision", 5), ("execution", 5)])  # the COO's own decision is logged by its tool, not twice
        self.assertEqual(settled, [(5, "joe"), (6, oa.ACTOR)])

    def test_bookkeeping_that_fails_never_undoes_or_hides_a_decision(self):
        with mock.patch.object(task_events, "record", mock.AsyncMock(side_effect=RuntimeError("boom"))):
            run(authorization._log_task_events(5, "cto", "approve", "joe", None))  # must not raise

def task_row(task_id, agent="cto", action="Pending", by=None, request=None, age_days=0):
    return {"id": task_id, "agentId": agent, "humanAction": action, "humanActionBy": by, "timestamp": NOW - timedelta(days=age_days),
            "authorizationRequest": json.dumps(request if request is not None else tool_plan())}


def event_row(task_id, event="plan_submitted"):
    return {"targetId": str(task_id), "details": json.dumps({"event": event, "audit_id": task_id})}


NOW = datetime(2026, 11, 17, 12, 0, 0)


class AuditReportTests(unittest.TestCase):
    def analyse(self, rows, events=None):
        return audit_report.analyse(rows, [event_row(r["id"]) for r in rows] if events is None else events, now=NOW)

    def checks(self, report):
        return sorted((f["check"], f["task_id"]) for f in report["findings"])

    def test_it_counts_tasks_by_executive_and_decision_and_what_the_coo_decided(self):
        report = self.analyse([task_row(1), task_row(2, "cao", "Approved", oa.ACTOR), task_row(3, "cmo", "Rejected", oa.ACTOR), task_row(4, "cto", "Approved", "joe")])
        self.assertEqual(report["tasks"], 4)
        self.assertEqual(report["by_agent"], {"cto": 2, "cao": 1, "cmo": 1})
        self.assertEqual(report["decisions"], {"Pending": 1, "Approved": 2, "Rejected": 1})
        self.assertEqual(report["decided_by_coo"], {"Approved": 1, "Rejected": 1})

    def test_a_clean_record_has_no_findings(self):
        self.assertEqual(self.analyse([task_row(1), task_row(2, action="Approved", by=oa.ACTOR)])["findings"], [])

    def test_it_catches_an_execution_with_no_approval(self):
        request = tool_plan(execution={"status": "succeeded", "pr_url": "https://github.com/o/r/pull/1"})
        report = self.analyse([task_row(5, action="Pending", request=request), task_row(6, action="Rejected", by="joe", request=request),
                               task_row(7, action="Approved", by="joe", request=request)])
        self.assertEqual(self.checks(report), [("executed_without_approval", 5), ("executed_without_approval", 6)])
        self.assertTrue(all(f["severity"] == "high" for f in report["findings"]))

    def test_it_catches_an_approval_nobody_is_recorded_for(self):
        self.assertEqual(self.checks(self.analyse([task_row(8, action="Approved", by=None)])), [("approved_without_actor", 8)])

    def test_it_flags_a_coo_approval_that_todays_floor_calls_critical_but_not_a_ceo_approval(self):
        critical = tool_plan(files_to_change=["frontend/prisma/schema.prisma"])
        report = self.analyse([task_row(9, action="Approved", by=oa.ACTOR, request=critical), task_row(10, action="Approved", by="joe", request=critical)])
        self.assertEqual(self.checks(report), [("coo_approved_a_critical", 9)])

    def test_it_flags_a_task_pending_for_too_long_and_notes_a_missing_event(self):
        report = self.analyse([task_row(11, age_days=audit_report.WAITING_DAYS + 1), task_row(12, age_days=1)], events=[event_row(12)])
        self.assertEqual(self.checks(report), [("missing_events", 11), ("waiting_too_long", 11)])

    def test_an_approved_plan_with_no_pull_request_is_a_note_not_a_finding_to_look_at(self):
        plan = {"execution_plan": {"files_to_update": [{"path": "a", "content": "b"}]}}
        report = self.analyse([task_row(13, action="Approved", by=oa.ACTOR, request=tool_plan(**plan))])
        self.assertEqual([(f["check"], f["severity"]) for f in report["findings"]], [("approved_but_not_acted_on", "info")])

    def test_the_text_is_written_from_the_counts_leads_with_the_serious_findings_and_states_what_it_cannot_see(self):
        report = self.analyse([task_row(1, action="Pending", request=tool_plan(execution={"status": "failed"})), task_row(2)])
        text = audit_report.render(report, "week", 7)
        self.assertIn("2 executive task(s)", text)
        self.assertIn("FINDINGS TO LOOK AT (1)", text)
        self.assertIn("task #1 (cto): executed_without_approval", text)
        self.assertIn("WHAT THIS CANNOT SEE", text)
        self.assertIn("not that nothing happened", text)
        self.assertIn("No findings to look at", audit_report.render(self.analyse([task_row(3)]), "day", 1))

    def test_the_period_is_one_of_day_week_or_month(self):
        self.assertEqual([audit_report.period_days(p) for p in ("day", "Week", "MONTHLY", "7d")], [1, 7, 30, 7])
        self.assertEqual([audit_report.period_days(p) for p in ("year", "", None, 7, "week; DROP")], [None] * 5)

    def test_the_tool_returns_the_report_for_the_coo_and_a_plain_error_for_a_bad_period_or_a_dead_database(self):
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(audit_report, "_tasks_sync", lambda since: [task_row(1)]), \
                mock.patch.object(audit_report, "_events_sync", lambda since: [event_row(1)]):
            text, ok, target, _ = run(coo_tools.generate_audit_report("week"))
            bad = run(coo_tools.generate_audit_report("decade"))
        self.assertEqual((ok, target), (True, "audit_report"))
        self.assertIn("AUDIT REPORT: last 7 day(s)", text)
        self.assertEqual((bad[1], bad[0].startswith("ERROR:")), (False, True))

        def boom(since):
            raise RuntimeError("down")

        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(audit_report, "_tasks_sync", boom):
            text, ok, _, _ = run(coo_tools.generate_audit_report("month"))
        self.assertFalse(ok)
        self.assertIn("no report was made", text)

    def test_the_report_tool_goes_through_the_router_for_the_coo_and_is_refused_to_everyone_else(self):
        block = SimpleNamespace(type="tool_use", id="t1", name="generate_audit_report", input={"period": "week"})
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(audit_report, "_tasks_sync", lambda since: []), \
                mock.patch.object(audit_report, "_events_sync", lambda since: []), mock.patch.object(exec_tools, "_log", mock.AsyncMock()):
            ok = run(exec_tools.handle_block("coo", block, exec_tools.ExecOutcome("coo")))
            refused = [run(exec_tools.handle_block(role, block, exec_tools.ExecOutcome(role))) for role in ("cao", "cmo")]
        self.assertFalse(ok["is_error"])
        self.assertTrue(all(r["is_error"] and "not one of your tools" in r["content"] for r in refused))

class NotificationStoreTests(unittest.TestCase):
    def test_create_refuses_empty_text_and_reports_no_database_plainly(self):
        with mock.patch.object(db, "is_configured", return_value=True):
            self.assertIsNone(run(ceo_notifications.create(1, "cto", "", "why"))[0])
            self.assertIsNone(run(ceo_notifications.create(1, "cto", "title", "   "))[0])
        with mock.patch.object(db, "is_configured", return_value=False):
            new_id, why = run(ceo_notifications.create(1, "cto", "title", "why"))
        self.assertEqual(new_id, None)
        self.assertIn("could not be notified", why)

    def test_create_never_raises_and_says_it_was_not_saved(self):
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(ceo_notifications, "_create_sync", side_effect=RuntimeError("down")):
            new_id, why = run(ceo_notifications.create(1, "cto", "title", "why"))
        self.assertEqual(new_id, None)
        self.assertIn("could not be saved", why)

    def test_text_is_capped_and_secrets_are_masked_and_risk_is_validated(self):
        seen = []
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(ceo_notifications, "_create_sync", lambda *a: seen.append(a) or (1, True)):
            run(ceo_notifications.create(1, "cto", "t" * 999, "why sk-ant-" + "A" * 30 + " " + "x " * 3000, "r" * 5000, "extreme"))
        audit_id, agent, title, why, advice, risk = seen[0]
        self.assertLessEqual((len(title), len(why), len(advice)), (ceo_notifications.MAX_TITLE, ceo_notifications.MAX_EXPLANATION, ceo_notifications.MAX_RECOMMENDATION))
        self.assertNotIn("sk-ant-", why)
        self.assertIsNone(risk)

    def test_one_open_notification_per_task_is_refreshed_not_duplicated(self):
        log = []
        with mock.patch.object(db, "transaction", fake_transaction(log, found=[41])):
            new_id, created = ceo_notifications._create_sync(5, "cto", "t", "why", "advice", "critical")
        self.assertEqual((new_id, created), (41, False))
        self.assertTrue(any(sql.lstrip().startswith('UPDATE "CeoNotification"') for sql, _ in log))
        self.assertFalse(any(sql.lstrip().startswith("INSERT") for sql, _ in log))

    def test_with_no_open_notification_for_the_task_a_new_one_is_inserted_unread(self):
        log = []

        class InsertCursor(FakeCursor):
            def fetchall(self):
                return [(99,)] if self.log and self.log[-1][0].lstrip().startswith("INSERT") else []

        @contextlib.contextmanager
        def transaction():
            yield SimpleNamespace(cursor=lambda: InsertCursor(log, []))

        with mock.patch.object(db, "transaction", transaction):
            self.assertEqual(ceo_notifications._create_sync(5, "cto", "t", "why", "advice", "critical"), (99, True))
        insert = [p for sql, p in log if sql.lstrip().startswith("INSERT")][0]
        self.assertIn("unread", insert)
        self.assertEqual(insert[0], 5)

    def test_every_value_is_a_parameter_and_mark_accepts_only_read_or_resolved(self):
        log = []
        with mock.patch.object(db, "transaction", fake_transaction(log, found=[1])), mock.patch.object(db, "is_configured", return_value=True):
            self.assertTrue(run(ceo_notifications.mark(3, "read")))
            self.assertTrue(run(ceo_notifications.mark(3, "resolved", "joe")))
            for bad in ((True, "read"), ("3", "read"), (3, "deleted"), (3, "unread")):
                self.assertFalse(run(ceo_notifications.mark(*bad)), bad)
        for sql, params in log:
            self.assertEqual(sql.count("%s"), len(params), sql)

    def test_present_uses_snake_case_and_utc_times(self):
        row = {"id": 1, "auditId": 9, "agentId": "cto", "title": "t", "explanation": "e", "recommendation": None, "riskLevel": "critical", "status": "unread",
               "createdAt": WHEN, "readAt": None, "resolvedAt": None, "resolvedBy": None}
        item = ceo_notifications.present(row)
        self.assertEqual((item["audit_id"], item["risk_level"], item["read_at"]), (9, "critical", None))
        self.assertTrue(item["created_at"].endswith("Z"))


class NotificationEndpointTests(unittest.TestCase):
    KEY = {"X-Internal-Council-Key": "council-key"}

    def setUp(self):
        from app.main import app

        self.client = TestClient(app)
        patch = mock.patch.dict(os.environ, {"INTERNAL_COUNCIL_KEY": "council-key"})
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_list_needs_the_council_key_and_no_agent_key_opens_it(self):
        self.assertEqual(self.client.get("/api/ceo-notifications").status_code, 401)
        self.assertEqual(self.client.get("/api/ceo-notifications", headers={"X-Internal-CTO-Key": "council-key"}).status_code, 401)
        self.assertEqual(self.client.post("/api/ceo-notifications/1/read", headers={"X-Admin-Id": "joe"}).status_code, 401)

    def test_a_bad_status_is_422_and_a_dead_database_is_503_never_an_empty_list(self):
        self.assertEqual(self.client.get("/api/ceo-notifications?status=bogus", headers=self.KEY).status_code, 422)
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(ceo_notifications, "list_notifications", mock.AsyncMock(side_effect=RuntimeError("x"))):
            self.assertEqual(self.client.get("/api/ceo-notifications", headers=self.KEY).status_code, 503)
        with mock.patch.object(db, "is_configured", return_value=False):
            self.assertEqual(self.client.get("/api/ceo-notifications", headers=self.KEY).status_code, 503)

    def test_the_list_returns_items_the_total_and_the_unread_badge(self):
        items = [{"id": 1, "audit_id": 5, "title": "t", "status": "unread"}]
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(ceo_notifications, "list_notifications", mock.AsyncMock(return_value=(items, 1, 1))) as listing:
            body = self.client.get("/api/ceo-notifications?status=all&limit=5", headers=self.KEY).json()
        self.assertEqual((body["items"], body["total"], body["unread"], body["limit"]), (items, 1, 1, 5))
        listing.assert_awaited_once_with(None, 5, 0)  # "all" means no status filter

    def test_marking_read_needs_an_admin_id_and_a_valid_number_and_changes_nothing_else(self):
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(ceo_notifications, "mark", mock.AsyncMock(return_value=True)) as mark:
            self.assertEqual(self.client.post("/api/ceo-notifications/1/read", headers=self.KEY).status_code, 400)
            self.assertEqual(self.client.post("/api/ceo-notifications/0/read", headers={**self.KEY, "X-Admin-Id": "joe"}).status_code, 422)
            ok = self.client.post("/api/ceo-notifications/3/read", headers={**self.KEY, "X-Admin-Id": "joe"})
        self.assertEqual((ok.status_code, ok.json()), (200, {"id": 3, "marked_read": True}))
        mark.assert_awaited_once_with(3, "read")  # read, never resolved: only deciding the task settles it

class CooDecisionsEndpointTests(unittest.TestCase):
    KEY = {"X-Internal-Council-Key": "council-key"}

    def setUp(self):
        from app.main import app

        self.client = TestClient(app)
        patch = mock.patch.dict(os.environ, {"INTERNAL_COUNCIL_KEY": "council-key"})
        patch.start()
        self.addCleanup(patch.stop)

    def row(self, task_id, history, action="Pending"):
        request = tool_plan(coo_decisions=history)
        return {"id": task_id, "agentId": "cto", "humanAction": action, "humanActionBy": None, "timestamp": WHEN, "authorizationRequest": json.dumps(request)}

    def test_it_needs_the_council_key_first(self):
        self.assertEqual(self.client.get("/api/coo-decisions").status_code, 401)
        self.assertEqual(self.client.get("/api/coo-decisions", headers={"X-Internal-COO-Key": "council-key"}).status_code, 401)

    def test_a_bad_period_is_422_and_a_dead_database_is_503_never_an_empty_list(self):
        self.assertEqual(self.client.get("/api/coo-decisions?period=year", headers=self.KEY).status_code, 422)
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(coo_decisions, "_read_sync", side_effect=RuntimeError("down")):
            self.assertEqual(self.client.get("/api/coo-decisions", headers=self.KEY).status_code, 503)

    def test_it_groups_by_the_coos_last_decision_and_carries_the_reasoning_and_the_notification(self):
        approved = self.row(1, [{"decision": "approve", "outcome": "approved", "reasoning": "Small and complete.", "at": "t1"}], "Approved")
        escalated = self.row(2, [{"decision": "escalate", "outcome": "escalated", "reasoning": "It changes the database.", "at": "t2"}])
        rejected = self.row(3, [{"decision": "approve", "outcome": "escalated", "reasoning": "first", "at": "t3"}, {"decision": "reject", "outcome": "rejected", "reasoning": "Not needed.", "at": "t4"}], "Rejected")
        with mock.patch.object(db, "is_configured", return_value=True), mock.patch.object(coo_decisions, "_read_sync", return_value=([approved, escalated, rejected], {2: {"id": 9, "status": "unread"}})), \
                mock.patch.object(audit_report, "generate", mock.AsyncMock(return_value=("AUDIT REPORT: ok", True))):
            body = self.client.get("/api/coo-decisions", headers=self.KEY).json()
        by_id = {i["audit_id"]: i for i in body["items"]}
        self.assertEqual([by_id[i]["group"] for i in (1, 2, 3)], ["approved", "escalated", "rejected"])
        self.assertEqual(by_id[2]["notification"], {"id": 9, "status": "unread"})
        self.assertIsNone(by_id[1]["notification"])
        self.assertEqual([d["reasoning"] for d in by_id[3]["coo_decisions"]], ["first", "Not needed."])  # the history is whole, oldest first
        self.assertEqual((body["audit_summary"], body["audit_ok"]), ("AUDIT REPORT: ok", True))

    def test_a_task_the_ceo_decided_after_the_coo_escalated_it_still_shows_as_escalated(self):
        item = coo_decisions.present(self.row(4, [{"decision": "escalate", "outcome": "escalated", "reasoning": "r"}], "Approved"), {})
        self.assertEqual((item["group"], item["human_action"]), ("escalated", "Approved"))