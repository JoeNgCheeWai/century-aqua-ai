"""
The CTO's language guard (app/agents/language_guard.py): robotic labels are relabelled exactly, sentences that hand routine work to "a technical
person" are removed, and nothing useful is ever lost. The edge cases matter most here, because a filter that deletes text can delete the one
command the CEO needed. No database, no network.

    python -m unittest tests.test_language_guard   (from backend/)
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import language_guard as g  # noqa: E402


class RelabelTests(unittest.TestCase):
    def test_the_two_robotic_labels_are_replaced_exactly_in_any_case(self):
        self.assertEqual(g.relabel("ESCALATE TO HUMAN: take care."), "Recommendation for CEO review: take care.")
        self.assertEqual(g.relabel("Escalate to human: x"), "Recommendation for CEO review: x")
        self.assertEqual(g.relabel("Waiting for your decision."), "Ready for your approval.")
        self.assertEqual(g.relabel("it is waiting for your decision now"), "it is ready for your approval now")
        self.assertEqual(g.relabel("**Waiting for your decision:** x"), "**Ready for your approval:** x")

    def test_text_without_them_is_returned_unchanged_and_empty_input_is_safe(self):
        self.assertEqual(g.relabel("Run `SELECT 1;` now."), "Run `SELECT 1;` now.")
        self.assertEqual(g.relabel(""), "")
        self.assertEqual(g.relabel(None), "")

    def test_the_closing_line_is_not_touched(self):
        line = "Recommendation for human review: approve the change."
        self.assertEqual(g.relabel(line), line)


class DeflectionTests(unittest.TestCase):
    def removed(self, text):
        return g.strip_deflections(text)[1]

    def test_the_ceos_own_example_is_removed_and_the_rest_of_the_reply_survives(self):
        text, removed, _ = g.strip_deflections("This is a change to your live production database. I recommend a technical person does this. Here is the plan.")
        self.assertEqual(len(removed), 1)
        self.assertNotIn("technical person", text)
        self.assertIn("Here is the plan.", text)

    def test_each_way_of_handing_the_task_to_someone_else_is_caught(self):
        for sentence in ("You should get a developer to do this.", "Ask your engineer to run it.", "Hire a DBA for the migration.",
                         "Please have your IT team handle the migration.", "Involve someone technical before you continue.",
                         "Let a developer take care of it.", "Hand this off to someone more experienced.", "You may want a qualified technical expert.",
                         "I recommend a qualified person reviews this.", "Contact an engineer about the table.", "Your developer should run it.",
                         "Bring in a technical specialist.", "Consult a database administrator first."):
            self.assertTrue(self.removed("Intro.\n" + sentence), sentence)

    def test_things_that_only_mention_those_words_are_left_alone(self):
        for sentence in ("I am the technical expert here.", "The developer notes are in the repo.", "The engineer who wrote it left comments.",
                         "You do not need a developer for this.", "No need to ask an engineer: I have the exact steps.",
                         "This is routine, without any help from a technical person.", "Developers love this pattern.",
                         "Technical details are behind the button."):
            text, removed, _ = g.strip_deflections(sentence)
            self.assertEqual((text, removed), (sentence, []), sentence)

    def test_a_deflecting_sentence_that_carries_a_command_is_kept_and_reported(self):
        sentence = "Ask a developer to run `npx prisma migrate deploy` for you."
        text, removed, kept = g.strip_deflections(sentence)
        self.assertEqual((text, removed), (sentence, []))
        self.assertEqual(kept, [sentence])  # reported, so it can be logged as a finding


class StructureIsPreservedTests(unittest.TestCase):
    def test_a_list_item_that_was_only_a_deflection_goes_with_its_number_and_the_other_steps_stay(self):
        text, removed, _ = g.strip_deflections("1. Get a developer to check the table.\n2. Run `SELECT 1;`\nThen tell me what you see.")
        self.assertEqual(len(removed), 1)
        self.assertEqual(text, "2. Run `SELECT 1;`\nThen tell me what you see.")
        text, _, _ = g.strip_deflections("### Step 1\n- Ask your engineer to do it.\n- Run `x`")
        self.assertEqual(text, "### Step 1\n- Run `x`")

    def test_only_the_deflecting_sentence_goes_from_a_mixed_line(self):
        text, _, _ = g.strip_deflections("Please have your IT team handle the migration. Then we are done.")
        self.assertEqual(text, "Then we are done.")

    def test_headings_code_fences_and_blank_lines_are_preserved(self):
        original = "### My Recommendation\n\n```\nnpx prisma migrate deploy\n```\n\nThis is ready for your approval."
        self.assertEqual(g.strip_deflections(original)[0], original)

    def test_a_reply_that_is_only_a_deflection_becomes_empty_rather_than_misleading(self):
        text, removed, _ = g.strip_deflections("I recommend a technical person does this.")
        self.assertEqual((text, len(removed)), ("", 1))

    def test_bad_input_never_raises(self):
        for bad in (None, "", "   ", "\n\n"):
            self.assertEqual(g.strip_deflections(bad)[1:], ([], []))


class CleanAndNoteTests(unittest.TestCase):
    def test_clean_relabels_then_removes_and_passes_empty_text_through(self):
        out, removed, _ = g.clean("ESCALATE TO HUMAN: a change. Get a developer to run it. Run `ls`.")
        self.assertEqual(out, "Recommendation for CEO review: a change. Run `ls`.")
        self.assertEqual(len(removed), 1)
        self.assertEqual(g.clean(None), (None, [], []))
        self.assertEqual(g.clean("  "), ("  ", [], []))

    def test_the_ready_note_is_collaborative_and_states_what_approving_does(self):
        with_plan, without = g.ready_note(True), g.ready_note(False)
        for note in (with_plan, without):
            self.assertTrue(note.startswith("**Ready for your approval:**"))
            self.assertIn("Nothing is applied until you approve it", note)
            for robotic in ("waiting for your decision", "escalate", "pending human approval", "a human must"):
                self.assertNotIn(robotic, note.lower())
        self.assertIn("not live until it is merged", with_plan)
        self.assertNotIn("pull request", without)


if __name__ == "__main__":
    unittest.main()
