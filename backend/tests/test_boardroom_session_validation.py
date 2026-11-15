"""
Regression tests for the production failure "only 0 usable proposals (need 2, including COO who chairs)" after the Boardroom
deep-thinking upgrade (4 seconds, 3 model calls).

THE ROOT CAUSE WAS NOT THE PARSER, THE SCHEMA OR A TOKEN LIMIT. Run against the real model, every call came back with
stop_reason="refusal", 1 output token and no text, in about a second. The model declined the prompt. A bisect over the real model found
the trigger: the new turn contract told the agent to write its reasoning as its "private working (it sits behind a toggle ...)". That
wording, a request to hide its thinking from the reader, is what the model refused. The reasoning field itself, the parser, the
contract's schema and the token caps were all fine (the same contract with that one phrase removed returned valid JSON with reasoning).

Why no test caught it: the test model is scripted, so it can never refuse. These tests therefore (a) pin the wording that must not
return, (b) simulate the refusal exactly as the real API reported it and check it is diagnosed instead of reported as a bare count,
(c) run a whole session with the new prompt structure (reasoning first, cross-response, merge) and check every proposal is accepted
and the chair is validated, and (d) feed the parser the replies a real model produced.

scripts/try_boardroom_turn.py and scripts/try_boardroom_session.py run the same checks against the real model.
"""

import asyncio
import json
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import council  # noqa: E402
from app.prompts import council_prompts as prompts  # noqa: E402
from tests.test_council_protocol import ALL, FakeModel, QUERY, run, turn  # noqa: E402

# What the real API returned for the refused calls (captured live): no text, one output token, stop_reason "refusal".
REFUSAL = dict(content=[], stop_reason="refusal", usage=SimpleNamespace(input_tokens=3200, output_tokens=1, output_tokens_details=SimpleNamespace(thinking_tokens=0)))

# The text a real claude-sonnet-5-5 produced for the COO against the FIXED contract (shortened only by trimming the reasoning text).
REAL_COO_REPLY = (
    '{"reasoning": "The real question is whether a portal this quarter fits a hard USD 150,000 pilot cap in Vietnam, and I have no scope, '
    'build-versus-buy cost, staffing or timeline data. Options I weighed: (1) a full portal launch this quarter, (2) a narrow pilot with a '
    'small group of Vietnamese customers using a bought platform and a fixed budget, (3) delay a quarter to scope properly. The narrow pilot '
    'fits the cap and tests demand, so I recommend it.", "stance": "conditional", "position": "I support a narrow pilot this quarter only if '
    'it fits inside the USD 150,000 cap with a reserve held back.", "hard_constraints": [{"constraint": "USD 150,000 pilot cap", "source": '
    '"CEO brief"}], "confidence": 45, "sources": ["general knowledge"], "objections": [{"to": "cmo", "point": "No demand evidence yet."}], '
    '"missing_critical_data": ["What does the portal need to do in the pilot?"], "proposed_action": null}'
)


class RefusingModel(FakeModel):
    """A model that refuses EVERY call, exactly as the production model did."""

    async def _create(self, **kw):
        self.calls.append({"kind": self._kind(kw["messages"][0]["content"]), "agent": "?", "max_tokens": kw["max_tokens"], "system": kw["system"],
                           "thinking": kw.get("thinking", "<not sent>")})
        return SimpleNamespace(**REFUSAL)


class RefusesOnlyTheNewWording(FakeModel):
    """Behaves like the real model did: it refuses a prompt that asks for hidden thinking, and answers every other prompt."""

    BANNED = ("private working", "behind a toggle")

    async def _create(self, **kw):
        if any(phrase in kw["messages"][0]["content"] or phrase in kw["system"] for phrase in self.BANNED):
            return SimpleNamespace(**REFUSAL)
        return await super()._create(**kw)


class TheRootCauseWordingTests(unittest.TestCase):
    """The phrases the real model refused must never come back, in ANY prompt a council turn is built from."""

    BANNED = ("private working", "behind a toggle", "sits behind", "your private", "hidden from", "keep this secret")

    def all_council_text(self):
        texts = [prompts.TURN_CONTRACT, prompts.CROSS_RESPONSE_CONTRACT, prompts.COHESION_RULES, prompts.DELIBERATION_CARD,
                 prompts.MODERATION_CONTRACT, prompts.SYNTHESIS_CONTRACT, prompts.RED_TEAM_CONTRACT]
        texts += [council.system_prompt_for(agent) for agent in council.ORDER]
        return texts

    def test_no_council_prompt_asks_the_agent_to_hide_its_reasoning(self):
        for text in self.all_council_text():
            for phrase in self.BANNED:
                self.assertNotIn(phrase, text.lower(), phrase)

    def test_the_reasoning_field_is_still_asked_for_first_and_described_as_analysis(self):
        for contract in (prompts.TURN_CONTRACT, prompts.CROSS_RESPONSE_CONTRACT):
            self.assertIn('Fill "reasoning" FIRST with your analysis', contract)
            self.assertLess(contract.index('"reasoning"'), contract.index('"stance"'))
        self.assertIn("the real problem behind the question", prompts.TURN_CONTRACT)
        self.assertIn("2 or 3 options", prompts.TURN_CONTRACT)

    def test_the_hiding_is_done_by_the_screen_not_by_asking_the_model(self):
        # The reasoning stays out of the main view because the UI puts it behind a toggle, not because the model was told to hide it.
        ui = (BACKEND_DIR.parent / "frontend" / "src" / "components" / "mc" / "DebateFlow.tsx").read_text(encoding="utf-8")
        self.assertIn("Show internal reasoning", ui)
        self.assertIn("<details", ui)


class RefusalIsDiagnosedTests(unittest.IsolatedAsyncioTestCase):
    """A refusal looks exactly like an empty reply. It must be named, not reported as a bare count."""

    async def test_a_refusal_is_logged_as_a_refusal_and_not_as_a_parsing_or_token_problem(self):
        with mock.patch.object(council, "_client", RefusingModel()), self.assertLogs("app.agents.council", level="ERROR") as logs:
            reply = await council.call_model("sys", "user", max_tokens=2048, budget=council.Budget())
        self.assertEqual((reply.text, reply.stop_reason, reply.truncated), ("", "refusal", False))
        joined = " ".join(logs.output)
        self.assertIn("REFUSED", joined)
        self.assertIn("not a JSON or token-limit problem", joined)

    async def test_a_session_where_every_call_is_refused_fails_with_the_reason_in_the_message(self):
        model = RefusingModel()
        with mock.patch.object(council, "_client", model), self.assertLogs("app.agents.council", level="ERROR"):
            result = await council.run_council_session(QUERY, ["cao", "coo", "cmo"])
        self.assertEqual(result["status"], "FAILED")
        reason = result["escalation_reason"]
        self.assertIn("0 usable proposals", reason)
        self.assertIn("3 reply(ies) refused by the model", reason)  # the new part: WHY
        self.assertNotIn("not valid JSON", reason)
        self.assertEqual(len(model.calls), 3)  # the production failure: 3 model calls, then stop

    async def test_the_reason_tells_a_cut_off_reply_from_a_refusal_from_a_crash_from_garbage(self):
        class Mixed(FakeModel):
            async def _create(self, **kw):
                agent = next(a for a in ALL if f"You are AI_{a.upper()}" in kw["system"])
                if agent == "cao":
                    return SimpleNamespace(**REFUSAL)
                if agent == "cto":
                    raise RuntimeError("upstream exploded")
                if agent == "cmo":
                    return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"reasoning": "cut')], stop_reason="max_tokens",
                                           usage=SimpleNamespace(input_tokens=10, output_tokens=2048))
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="not json at all")], stop_reason="end_turn",
                                       usage=SimpleNamespace(input_tokens=10, output_tokens=5))

        with mock.patch.object(council, "_client", Mixed()), self.assertLogs("app.agents.council", level="ERROR"):
            result = await council.run_council_session(QUERY, ALL)
        reason = result["escalation_reason"]
        for expected in ("1 call(s) failed or timed out", "1 reply(ies) refused by the model", "1 reply(ies) cut off at the token cap",
                         "1 reply(ies) were not valid JSON proposals"):
            self.assertIn(expected, reason)

    async def test_the_reason_never_contains_model_text(self):
        secret = "SECRET-MODEL-TEXT-" + "x" * 20
        with mock.patch.object(council, "_client", FakeModel(text_override=secret)), self.assertLogs("app.agents.council", level="ERROR"):
            result = await council.run_council_session(QUERY, ["cao", "coo"])
        self.assertNotIn("SECRET-MODEL-TEXT", result["escalation_reason"])

    async def test_a_session_that_has_enough_usable_proposals_carries_no_diagnosis(self):
        result = await run(FakeModel())
        self.assertEqual(result["status"], "RESOLVED")
        self.assertIsNone(result["escalation_reason"])

    def test_the_stop_reason_is_never_stored_or_returned(self):
        session = council.Session("s", QUERY, ["cao", "coo"], "r")
        reply = council.TurnReply("{}", 1, 1, False, "end_turn", 0)
        turn_ = council.normalize_turn("cao", turn())
        turn_["stop_reason"] = "end_turn"
        council._log_turn(session, 1, turn_, reply)
        self.assertNotIn("stop_reason", session.turns[0])

    async def test_a_prompt_that_the_model_refuses_is_what_this_fake_reproduces_and_the_fixed_prompt_passes(self):
        # The same refusing model with the FIXED prompts: nothing banned is in the prompt, so it answers and the session resolves.
        result = await run(RefusesOnlyTheNewWording())
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        # And with the OLD wording put back, it fails exactly as production did: this is the regression the fake guards.
        broken = prompts.TURN_CONTRACT.replace("with your analysis:", "as your private working (it sits behind a toggle):")
        with mock.patch.object(prompts, "TURN_CONTRACT", broken), self.assertLogs("app.agents.council", level="ERROR"):
            failed = await run(RefusesOnlyTheNewWording())
        self.assertEqual(failed["status"], "FAILED")
        self.assertIn("0 usable proposals", failed["escalation_reason"])


class RealReplyParsingTests(unittest.TestCase):
    """What a real model produced for the fixed contract must parse as a usable proposal, however it is wrapped."""

    def test_the_real_model_reply_is_accepted_as_a_usable_proposal(self):
        parsed = council.normalize_turn("coo", REAL_COO_REPLY)
        self.assertTrue(parsed["valid"])
        self.assertEqual((parsed["stance"], parsed["confidence"]), ("conditional", 45))
        self.assertTrue(parsed["reasoning"].startswith("The real question is whether a portal"))
        self.assertEqual([c["constraint"] for c in parsed["hard_constraints"]], ["USD 150,000 pilot cap"])
        self.assertEqual(parsed["missing_critical_data"], ["What does the portal need to do in the pilot?"])

    def test_a_reply_wrapped_in_a_code_fence_or_prose_is_still_usable(self):
        for wrapped in (f"```json\n{REAL_COO_REPLY}\n```", f"Here is my proposal:\n{REAL_COO_REPLY}\nHope that helps.", f"\n\n  {REAL_COO_REPLY}  \n"):
            self.assertTrue(council.normalize_turn("coo", wrapped)["valid"], wrapped[:30])

    def test_a_proposal_is_still_usable_when_the_new_reasoning_field_is_missing_or_malformed(self):
        base = json.loads(REAL_COO_REPLY)
        for label, change in (("missing", lambda d: d.pop("reasoning")), ("a list", lambda d: d.update(reasoning=["a", "b"])),
                              ("null", lambda d: d.update(reasoning=None)), ("a number", lambda d: d.update(reasoning=7))):
            data = json.loads(json.dumps(base))
            change(data)
            parsed = council.normalize_turn("coo", json.dumps(data))
            self.assertTrue(parsed["valid"], label)
            self.assertEqual(parsed["reasoning"], "", label)

    def test_an_overlong_reasoning_is_clipped_not_rejected(self):
        data = json.loads(REAL_COO_REPLY)
        data["reasoning"] = "word " * 2000
        parsed = council.normalize_turn("coo", json.dumps(data))
        self.assertTrue(parsed["valid"])
        self.assertLessEqual(len(parsed["reasoning"]), council.MAX_REASONING_CHARS)


class NewPromptStructureSessionTests(unittest.IsolatedAsyncioTestCase):
    """A whole session with the new structure (reasoning first, cross-response, merge): every proposal accepted, the chair validated."""

    async def test_a_full_session_with_the_new_structure_accepts_every_proposal_and_every_cross_response(self):
        model = FakeModel(turns={"coo": REAL_COO_REPLY})
        result = await run(model, agents=["cao", "coo", "cmo"])
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        first = [t for t in result["turns"] if t["round"] == 1]
        self.assertEqual({t["agent_id"] for t in first}, {"cao", "coo", "cmo"})  # all three proposals were usable
        self.assertTrue(all(t["reasoning"] for t in first))
        cross = [t for t in result["turns"] if t["round"] == council.CROSS_ROUND]
        self.assertEqual({t["agent_id"] for t in cross}, {"cao", "coo", "cmo"})
        coo_cross = next(t for t in cross if t["agent_id"] == "coo")
        self.assertEqual({r["to"] for r in coo_cross["responses"]}, {"cao", "cmo"})
        self.assertEqual(model.kinds(), ["turn"] * 3 + ["cross"] * 3 + ["moderation", "synthesis", "red_team"])

    async def test_the_chair_is_validated_the_session_needs_the_coo_among_the_usable_proposals(self):
        class ChairRefused(FakeModel):
            async def _create(self, **kw):
                if "You are AI_COO" in kw["system"] and "COUNCIL TURN" in kw["messages"][0]["content"]:
                    return SimpleNamespace(**REFUSAL)
                return await super()._create(**kw)

        with mock.patch.object(council, "_client", ChairRefused()), self.assertLogs("app.agents.council", level="ERROR"):
            result = await council.run_council_session(QUERY, ["cao", "coo", "cmo"])
        self.assertEqual(result["status"], "FAILED")  # two usable proposals, but the chair is not one of them
        self.assertIn("only 2 usable proposals", result["escalation_reason"])
        self.assertIn("including COO who chairs", result["escalation_reason"])
        self.assertIn("1 reply(ies) refused by the model", result["escalation_reason"])

    async def test_when_the_coo_is_not_seated_the_chair_that_is_seated_is_the_one_validated(self):
        model = FakeModel()
        result = await run(model, agents=["cao", "cmo"])  # no COO: the CMO chairs (see assign_roles)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])
        self.assertEqual(council.assign_roles(["cao", "cmo"])[0], "cmo")
        self.assertEqual({c["agent"] for c in model.calls if c["kind"] in ("moderation", "red_team")}, {"cmo"})

    async def test_a_cross_response_that_is_refused_never_fails_a_session_that_has_good_proposals(self):
        class CrossRefused(FakeModel):
            async def _create(self, **kw):
                if "COUNCIL CROSS-RESPONSE" in kw["messages"][0]["content"]:
                    return SimpleNamespace(**REFUSAL)
                return await super()._create(**kw)

        with mock.patch.object(council, "_client", CrossRefused()), self.assertLogs("app.agents.council", level="ERROR"):
            result = await council.run_council_session(QUERY, ALL)
        self.assertEqual(result["status"], "RESOLVED", result["escalation_reason"])  # it falls back to the initial positions
        self.assertFalse([t for t in result["turns"] if t["round"] == council.CROSS_ROUND])

    def test_the_prompts_a_session_really_sends_are_the_pinned_ones_and_contain_no_banned_wording(self):
        seen = []

        class Spy(FakeModel):
            async def _create(self, **kw):
                seen.append(kw["messages"][0]["content"] + kw["system"])
                return await super()._create(**kw)

        asyncio.run(run(Spy()))
        self.assertTrue(any(prompts.TURN_CONTRACT in text for text in seen))  # round 1 sends exactly the contract the tests check
        self.assertTrue(any(prompts.CROSS_RESPONSE_CONTRACT in text for text in seen))
        for text in seen:
            self.assertNotIn("private working", text.lower())
            self.assertNotIn("behind a toggle", text.lower())


if __name__ == "__main__":
    unittest.main()
