"""
Tests for the tolerant JSON loader (cao_reasoning._load_json_object / fix_closers), added in Phase 6.6 after a live check found
that the model closed a deeply nested execution plan with a wrong or extra bracket in 3 of 7 samples, which threw the whole
answer (and the plan) away.

The loader must repair ONLY wrong or extra closing brackets. It must never complete a truncated reply, never change a string,
and never change text that already parses.

    python -m unittest tests.test_json_repair   (from backend/)
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cao_reasoning as cr  # noqa: E402

GOOD = {"response": "ok", "authorization_request": {"execution_plan": {"files_to_update": [{"path": "a.ts", "content": "x"}], "pr_title": "t"}}}


class RepairTests(unittest.TestCase):
    def test_text_that_already_parses_is_returned_unchanged(self):
        text = json.dumps(GOOD)
        self.assertEqual(cr.fix_closers(text), text)
        self.assertEqual(cr._load_json_object(text), GOOD)

    def test_one_extra_closing_brace_at_the_end_is_tolerated(self):
        self.assertEqual(cr._load_json_object(json.dumps(GOOD) + "\n}"), GOOD)
        self.assertEqual(cr._load_json_object(json.dumps(GOOD) + "}}"), GOOD)

    def test_a_square_bracket_where_a_brace_belongs_is_corrected(self):
        broken = json.dumps(GOOD).replace('"pr_title": "t"}}}', '"pr_title": "t"}]}')
        self.assertNotEqual(broken, json.dumps(GOOD))
        self.assertEqual(cr._load_json_object(broken), GOOD)

    def test_a_brace_where_a_bracket_belongs_is_corrected(self):
        broken = '{"a": [1, 2}, "b": 3}'
        self.assertEqual(cr._load_json_object(broken), {"a": [1, 2], "b": 3})

    def test_trailing_junk_after_a_complete_object_is_ignored(self):
        self.assertEqual(cr._load_json_object(json.dumps(GOOD) + '\n\n"chat_reply": "stray"}'), GOOD)

    def test_a_code_fence_and_leading_prose_still_work(self):
        self.assertEqual(cr._load_json_object("Here you go:\n```json\n" + json.dumps(GOOD) + "\n```"), GOOD)

    def test_brackets_inside_strings_are_never_touched(self):
        data = {"code": 'if (a[0]) { return "}]"; }', "path": "a.ts"}
        text = json.dumps(data)
        self.assertEqual(cr.fix_closers(text), text)
        self.assertEqual(cr._load_json_object(text + "}"), data)
        escaped = '{"s": "a \\" } ] b", "n": 1}'
        self.assertEqual(cr.fix_closers(escaped), escaped)
        self.assertEqual(cr._load_json_object(escaped), {"s": 'a " } ] b', "n": 1})

    def test_a_truncated_reply_is_never_completed(self):
        cut = json.dumps(GOOD)[:-6]  # the end is missing
        self.assertIsNone(cr._load_json_object(cut))
        self.assertEqual(cr.fix_closers(cut), cut)  # no closer was added

    def test_a_truncated_plan_gives_no_plan_and_no_request_exactly_as_before(self):
        cut = '{"response": "r", "authorization_request": {"action_summary": "s", "execution_plan": {"branch_name": "agent/x", "files_to_update": [{"path": "a.ts", "content": "const a'
        parsed = cr.parse_cao_output(cut)
        self.assertFalse(parsed.structured)
        self.assertIsNone(cr._load_json_object(cut))

    def test_genuinely_broken_json_is_still_refused(self):
        for bad in ('{"a": }', '{"a" 1}', "not json at all", "{", "}", '{"a": [1, 2', ""):
            self.assertIsNone(cr._load_json_object(bad), bad)

    def test_a_non_object_is_still_refused(self):
        self.assertIsNone(cr._load_json_object("[1, 2]"))

    def test_it_never_raises_on_garbage(self):
        for raw in (None, "", "{" * 500, "}" * 500, "{[" * 200 + "]}" * 200, '{"a": "' + "\\" * 99, "\x00{}\x00"):
            cr._load_json_object(raw)
            cr.fix_closers(raw or "")

    def test_the_repair_changes_no_value_only_punctuation(self):
        broken = json.dumps(GOOD) + "\n}"
        self.assertEqual(cr._load_json_object(broken)["authorization_request"]["execution_plan"]["files_to_update"][0]["content"], "x")

    def test_parse_cao_output_keeps_the_whole_answer_and_the_plan_when_a_closer_is_wrong(self):
        full = {"response": "Bottom line.", "reasoning_chain": ["Step 1", "Step 2", "Step 3", "Data and sources: x", "Assumptions: x", "Limitations: x"],
                "scenarios": None, "pre_consultation_requests": {"coo_query": None, "cto_query": None}, "chat_reply": "Short.",
                "authorization_request": GOOD["authorization_request"]}
        parsed = cr.parse_cao_output(json.dumps(full) + "\n}")
        self.assertTrue(parsed.structured)
        self.assertEqual(parsed.response, "Bottom line.")
        self.assertEqual(parsed.chat_reply, "Short.")
        self.assertEqual(parsed.findings, [])
        self.assertEqual(cr.load_json_object(json.dumps(full) + "\n}")["authorization_request"], GOOD["authorization_request"])


if __name__ == "__main__":
    unittest.main()
