"""
COMMUNICATION PARITY: the CAO, COO and CMO reach the CTO's standard. This is a parity change, not a capability change.

For ALL FOUR executives this file proves, in one place:
  * a structured chat-reply prompt of their own (headings, numbered steps, bold, the why first, 1 or 2 options with a recommendation,
    partner wording), tuned to the role: the CAO leads with the biological risk and the evidence, the COO with the constraint, the CMO with the
    audience and the message; 5000 characters, not 1200;
  * a communication protocol and a zero-friction block (the concrete artefact first, no "I can draft that if you like");
  * the language guard on the reply (relabelling, deflections to a role-appropriate person removed, offers to do the work later removed);
  * the markdown renderer (frontend/scripts/mission_control_markdown.test.mjs);
THE LINE THAT MATTERS, tested on BOTH sides: "do this yourself, here is how" is made to stand, but a genuine legal or professional requirement
(a notifiable disease, a statutory report, a prescription-only medicine, a binding sign-off, a legal review) and a safety escalation SURVIVE the
guard, because hiding them could hurt someone.

A scripted model stands in for Anthropic, so these prove what CODE does and what the PROMPTS say. They cannot prove the real model follows them:
scripts/try_executive_parity.py does that against the real model. No database, no network.

    python -m unittest tests.test_executive_parity   (from backend/)
"""

import asyncio
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cao_agent, cao_reasoning, cmo_agent, coo_agent, cto_agent, guard_rules, language_guard as g  # noqa: E402
from app.prompts import chat_style, persona, role_protocols  # noqa: E402

AGENTS = {"cao": cao_agent, "coo": coo_agent, "cto": cto_agent, "cmo": cmo_agent}
SOLO = ("cao", "coo", "cmo")
LEADS = {"cao": "LEAD WITH THE BIOLOGICAL RISK AND THE OBSERVABLE EVIDENCE", "coo": "LEAD WITH THE OPERATIONAL CONSTRAINT",
         "cmo": "LEAD WITH THE AUDIENCE AND THE MESSAGE"}


class EveryExecutiveHasTheStructuredChatPromptTests(unittest.TestCase):
    def test_all_four_have_their_own_structured_prompt_exactly_once(self):
        for agent, module in AGENTS.items():
            prompt = module.BASE_SYSTEM_PROMPT
            self.assertIn(chat_style.CHAT_REPLY_PROMPTS[agent].strip(), prompt, agent)
            self.assertEqual(prompt.count("CONVERSATIONAL REPLY (MANDATORY"), 1, agent)
            self.assertNotIn(chat_style.CHAT_REPLY_PROMPT.strip(), prompt, agent)  # the shared plain prompt is gone for all four
            self.assertNotIn("No bullet points, no headings", prompt, agent)

    def test_no_executive_carries_another_executives_chat_prompt(self):
        for agent, module in AGENTS.items():
            for other, text in chat_style.CHAT_REPLY_PROMPTS.items():
                if other != agent:
                    self.assertNotIn(text.strip(), module.BASE_SYSTEM_PROMPT, f"{agent} has {other}'s")

    def test_the_three_new_prompts_ask_for_the_qwen_style_rules(self):
        for agent in SOLO:
            for needle in ("### headings", "numbered", "**bold**", "Explain the WHY before you ask", "1 or 2 options",
                           "### My Recommendation", "up to about 5000 characters", "add nothing new", "Never say or imply that you have done"):
                self.assertIn(needle, chat_style.CHAT_REPLY_PROMPTS[agent], f"{agent}: {needle}")

    def test_each_one_leads_with_the_thing_its_role_cares_about(self):
        for agent, lead in LEADS.items():
            self.assertIn(lead, chat_style.CHAT_REPLY_PROMPTS[agent], agent)
            for other, other_lead in LEADS.items():
                if other != agent:
                    self.assertNotIn(other_lead, chat_style.CHAT_REPLY_PROMPTS[agent], f"{agent} leads like {other}")
        self.assertIn("the signs the CEO or the farm team can actually SEE or MEASURE", chat_style.CAO_CHAT_REPLY_PROMPT)
        self.assertIn("with the numbers (dose, timing, threshold)", chat_style.CAO_CHAT_REPLY_PROMPT)
        self.assertIn("the cost, the staff available, the timing, the cash position, the supplier lead time", chat_style.COO_CHAT_REPLY_PROMPT)
        self.assertIn("the actual copy, the actual hook, the actual subject line", chat_style.CMO_CHAT_REPLY_PROMPT)

    def test_every_safeguard_of_the_old_shared_prompt_is_kept(self):
        for agent in SOLO:
            text = chat_style.CHAT_REPLY_PROMPTS[agent]
            for needle in ('"chat_reply"', "must stand on its own", "below 60%", 'the question itself is the "chat_reply"',
                           'do not add "chat_reply"', "no HTML, no emojis, no links"):
                self.assertIn(needle, text, f"{agent}: {needle}")

    def test_the_new_prompts_are_safe_to_format_and_free_of_the_wording_the_real_model_refused(self):
        from app.agents.plan_checks import REFUSED_WORDING

        for agent, text in chat_style.CHAT_REPLY_PROMPTS.items():
            self.assertNotIn("{", text, agent)
            self.assertNotIn("}", text, agent)
            for banned in REFUSED_WORDING:
                self.assertNotIn(banned, text.lower(), f"{agent}: {banned}")

    def test_the_line_break_escape_matches_the_ctos_exactly_so_the_json_stays_valid(self):
        def escape(text):
            i = text.index("write line breaks as ") + len("write line breaks as ")
            return text[i:i + 3]

        self.assertEqual(escape(chat_style.CTO_CHAT_REPLY_PROMPT), "\\n.")
        for agent, text in chat_style.CHAT_REPLY_PROMPTS.items():
            self.assertEqual(escape(text), "\\n.", agent)

    def test_the_reply_room_is_5000_for_all_four_and_the_parser_default_is_unchanged(self):
        self.assertEqual(cao_reasoning.STRUCTURED_CHAT_REPLY_CHARS, 5000)
        self.assertEqual(cto_agent.CTO_MAX_CHAT_REPLY_CHARS, cao_reasoning.STRUCTURED_CHAT_REPLY_CHARS)
        self.assertEqual(cao_reasoning.MAX_CHAT_REPLY_CHARS, 1200)  # a caller that passes no cap (a verdict, an old test) keeps the old limit

    def test_the_other_blocks_each_agent_had_are_untouched(self):
        self.assertIn(chat_style.AUTHORIZATION_SUMMARY_PROMPT.strip(), cmo_agent.BASE_SYSTEM_PROMPT)
        self.assertIn(chat_style.AUTHORIZATION_SUMMARY_PROMPT.strip(), cto_agent.BASE_SYSTEM_PROMPT)
        for agent in ("cao", "coo"):
            self.assertNotIn("executive_summary", AGENTS[agent].BASE_SYSTEM_PROMPT, agent)
            self.assertNotIn("execution_plan", AGENTS[agent].BASE_SYSTEM_PROMPT, agent)
        for agent, module in AGENTS.items():
            self.assertTrue(module.BASE_SYSTEM_PROMPT.startswith("EXECUTIVE CONSTITUTION"), agent)
            self.assertLess(len(module.BASE_SYSTEM_PROMPT) // 4, 16_000, f"{agent} prompt is over ~16k tokens")


class EveryExecutiveHasTheProtocolAndZeroFrictionTests(unittest.TestCase):
    ZERO = {"cao": "ZERO FRICTION: YOU ARE THE AQUACULTURE EXPERT", "coo": "ZERO FRICTION: YOU ARE THE OPERATIONS EXPERT",
            "cmo": "ZERO FRICTION: YOU ARE THE CREATIVE EXPERT"}

    def test_all_four_carry_a_protocol_and_a_zero_friction_block(self):
        self.assertIn("COMMUNICATION PROTOCOL", cto_agent.BASE_SYSTEM_PROMPT)
        self.assertIn("ZERO FRICTION", cto_agent.BASE_SYSTEM_PROMPT)
        for agent in SOLO:
            prompt = AGENTS[agent].BASE_SYSTEM_PROMPT
            self.assertIn(role_protocols.role_protocol(agent), prompt, agent)
            self.assertEqual(prompt.count("COMMUNICATION PROTOCOL"), 1, agent)
            self.assertIn(self.ZERO[agent], prompt, agent)

    def test_each_role_gets_only_its_own_zero_friction_block(self):
        for agent in SOLO:
            for other, marker in self.ZERO.items():
                if other != agent:
                    self.assertNotIn(marker, AGENTS[agent].BASE_SYSTEM_PROMPT, f"{agent} has {other}'s")
        self.assertNotIn("ZERO FRICTION: YOU ARE THE TECHNICAL PERSON", cao_agent.BASE_SYSTEM_PROMPT)

    def test_the_five_qwen_style_rules_are_in_each_protocol(self):
        for agent in SOLO:
            text = role_protocols.role_protocol(agent)
            for needle in ("SPEAK TO A VISIONARY CEO, NOT A SPECIALIST", "ALWAYS USE STRUCTURE", "EXPLAIN THE WHY BEFORE YOU ASK",
                           "BRING SOLUTIONS, NOT JUST PROBLEMS", "KEEP THE SAFEGUARDS, SAY THEM LIKE A PARTNER", "Friendly never means vague",
                           "short, accurate everyday analogy", "one action per step"):
                self.assertIn(needle, text, f"{agent}: {needle}")

    def test_each_one_gives_the_concrete_artefact_first_and_never_offers_to_do_it_later(self):
        expect = {"cao": ("Give the actual protocol in the first reply", "the doses and the timings", 'Never answer with only "I can draft a protocol if you like"'),
                  "coo": ("Give the actual schedule or the costed plan in the first reply", "who does each step, when, what it costs",
                          'Never answer with only "I can build that schedule if you like"'),
                  "cmo": ("Write the actual copy in the first reply", "the hook, the body, the call to action, the subject line",
                          'Never answer with only "I can draft that if you like"')}
        for agent, needles in expect.items():
            for needle in needles:
                self.assertIn(needle, role_protocols.role_protocol(agent), f"{agent}: {needle}")

    def test_each_one_is_told_never_to_hand_routine_work_to_its_role_specific_person(self):
        expect = {"cao": ('"consult a fish vet"', '"ask a biologist"', '"get an aquaculture consultant"'),
                  "coo": ('"ask your operations manager"', '"check with finance"', '"get an accountant"'),
                  "cmo": ('"hire an agency"', '"ask a copywriter"', '"get a designer"')}
        for agent, needles in expect.items():
            for needle in needles:
                self.assertIn(needle, role_protocols.role_protocol(agent), f"{agent}: {needle}")

    def test_the_prompt_says_the_law_is_not_a_hand_off_and_to_keep_it(self):
        self.assertIn("THE LAW IS NOT A HAND-OFF", role_protocols.CAO_PROTOCOL)
        self.assertIn("notifiable or reportable disease, a statutory report, a prescription-only medicine or a licensed person the law requires",
                      role_protocols.CAO_PROTOCOL)
        self.assertIn("Mass mortality, a suspected outbreak or a risk to public health is urgent", role_protocols.CAO_PROTOCOL)
        self.assertIn("A BINDING SIGN-OFF IS NOT A HAND-OFF", role_protocols.COO_PROTOCOL)
        self.assertIn("A REVIEW THE LAW REQUIRES IS NOT A HAND-OFF", role_protocols.CMO_PROTOCOL)
        for agent in SOLO:
            self.assertIn("Tell the two apart", role_protocols.role_protocol(agent), agent)

    def test_the_old_gatekeeper_phrases_are_only_ever_forbidden_never_instructed(self):
        for agent in SOLO:
            text = role_protocols.role_protocol(agent)
            for phrase in ("a human must decide", "a human should confirm", "pending human approval"):
                self.assertEqual(text.count(phrase), 1 if phrase in text else 0, phrase)
                if phrase in text:
                    line = next(line for line in text.splitlines() if phrase in line)
                    self.assertTrue("Do not say" in line, line[:100])

    def test_the_protocols_change_no_safety_rule_and_ask_for_nothing_the_real_model_refused(self):
        from app.agents.plan_checks import REFUSED_WORDING

        for agent in SOLO:
            text = role_protocols.role_protocol(agent)
            self.assertIn("No safety rule changes", text, agent)
            self.assertNotIn("{", text)
            self.assertNotIn("}", text)
            for banned in REFUSED_WORDING:
                self.assertNotIn(banned, text.lower(), f"{agent}: {banned}")
            for tool_word in ("github_read_file", "diagnose_boardroom_session", "execution_plan", "authorization_request"):
                self.assertNotIn(tool_word, text, f"{agent} was told about a tool it does not have")

    def test_the_protocol_sits_after_the_constitution_and_before_the_agents_own_role_text(self):
        for agent in SOLO:
            prompt = AGENTS[agent].BASE_SYSTEM_PROMPT
            self.assertLess(prompt.index("EXECUTIVE CONSTITUTION"), prompt.index("COMMUNICATION PROTOCOL"), agent)
            self.assertLess(prompt.index("COMMUNICATION PROTOCOL"), prompt.index("STRATEGIC THINKING PROCESS"), agent)

    def test_there_is_no_protocol_for_an_agent_that_is_not_one_of_the_three(self):
        with self.assertRaises(KeyError):
            role_protocols.role_protocol("cto")


class TheGuardRemovesTheRightHandOffsForEachRoleTests(unittest.TestCase):
    """Routine work must never be handed to someone else, and each role has its OWN list of who it must not hand it to."""

    HAND_OFFS = {
        "cao": ("Consult a fish vet before you choose the dose.", "You should ask a veterinarian to pick the treatment.",
                "Get an aquaculture consultant to review the pond.", "Have your hatchery manager run the water test.", "Speak to a biologist about it."),
        "coo": ("Ask your operations manager to build the schedule.", "Get your finance team to cost it.", "Have an accountant prepare the forecast.",
                "Hire a management consultant for this.", "Check with your procurement team."),
        "cmo": ("You should hire an agency for the campaign.", "Ask a copywriter to draft the post.", "Get a designer to make the banner.",
                "Have your marketing team write it.", "Outsource this to a freelancer."),
    }

    def test_each_role_loses_its_own_hand_offs_and_keeps_the_rest_of_the_reply(self):
        for role, sentences in self.HAND_OFFS.items():
            for sentence in sentences:
                result = g.guard(f"{sentence} The answer follows.", role)
                self.assertEqual(result.text, "The answer follows.", (role, sentence))
                self.assertEqual(len(result.removed), 1, (role, sentence))

    def test_a_role_is_not_held_to_another_roles_list(self):
        # "ask your operations manager" is a hand-off for the COO, but the CAO's list is about animals and the CMO's about creative work.
        self.assertEqual(g.guard("Ask your operations manager.", "coo").text, "")
        self.assertEqual(g.guard("Ask your operations manager.", "cmo").text, "Ask your operations manager.")
        self.assertEqual(g.guard("Hire an agency.", "cao").text, "Hire an agency.")
        self.assertEqual(g.guard("Consult a fish vet first.", "coo").text, "Consult a fish vet first.")

    def test_sentences_that_only_mention_those_words_are_left_alone(self):
        calm = {"cao": ("I am the aquaculture expert here, so here is the protocol.", "The vet notes are in the file.", "Biologists call this nitrification."),
                "coo": ("The operations team meets on Monday.", "Your accountant's year end is June.", "I am the operations expert, so here is the schedule."),
                "cmo": ("The agency's brief is attached.", "Designers love this contrast.", "I will write the copy below.")}
        for role, sentences in calm.items():
            for sentence in sentences:
                result = g.guard(sentence, role)
                self.assertEqual((result.text, result.removed), (sentence, []), (role, sentence))

    def test_a_list_item_that_was_only_a_hand_off_goes_with_its_number(self):
        text = g.guard("1. Ask a veterinarian to pick the dose.\n2. Run the aerators at dawn.\n3. Re-measure oxygen.", "cao").text
        self.assertEqual(text, "2. Run the aerators at dawn.\n3. Re-measure oxygen.")

    def test_the_robotic_labels_are_relabelled_for_all_four(self):
        for role in g.ROLES:
            result = g.guard("ESCALATE TO HUMAN: take care. It is Waiting for your decision.", role)
            self.assertEqual(result.text, "Recommendation for CEO review: take care. It is Ready for your approval.", role)

    def test_a_concrete_instruction_inside_a_hand_off_is_kept_and_reported(self):
        cases = {"cao": "Ask a vet to confirm, and dose at 5 mg/L for 3 days.", "coo": "Ask your operations manager to book 2 staff for 6 hours.",
                 "cmo": "Ask a designer to use `#0B3C5D` for the banner."}
        for role, sentence in cases.items():
            result = g.guard(sentence, role)
            self.assertEqual((result.text, result.removed), (sentence, []), role)
            self.assertEqual(result.kept, [sentence], role)  # kept, and reported so a person can see what survived


class OffersToDoTheWorkLaterAreRemovedTests(unittest.TestCase):
    OFFERS = ("I can draft that if you like.", "Would you like me to write the copy?", "I could put together a schedule for you.",
              "Happy to calculate the cost.", "Shall I outline the protocol?")

    def test_an_offer_instead_of_the_artefact_is_removed_for_the_cao_coo_and_cmo(self):
        for role in SOLO:
            for offer in self.OFFERS:
                result = g.guard(f"Here is the plan. {offer}", role)
                self.assertEqual(result.text, "Here is the plan.", (role, offer))
                self.assertEqual(len(result.offers), 1, (role, offer))

    def test_a_conditional_that_asks_for_something_the_expert_truly_needs_is_kept(self):
        for role, sentence in (("coo", "Once you send the headcount I can build the schedule."), ("cmo", "When you share the audience size I can tune the offer."),
                               ("cao", "If you tell me the pond volume I can work out the exact dose.")):
            result = g.guard(sentence, role)
            self.assertEqual((result.text, result.offers), (sentence, []), role)

    def test_an_offer_that_already_contains_the_work_is_kept(self):
        sentence = "I can draft it right here: Day 1 dose 5 mg/L, Day 2 recheck."
        for role in SOLO:
            self.assertEqual(g.guard(sentence, role).text, sentence, role)

    def test_the_ctos_behaviour_is_exactly_what_it_was(self):
        self.assertEqual(g.guard("I can draft that if you like.", "cto").text, "I can draft that if you like.")  # CTO offers are the prompt's job
        self.assertEqual(g.clean("ESCALATE TO HUMAN: a change. Get a developer to run it. Run `ls`.")[0], "Recommendation for CEO review: a change. Run `ls`.")


class TheLegalAndSafetyLineSurvivesTheGuardTests(unittest.TestCase):
    """
    THE LINE THAT MATTERS. "Do this yourself, here is how" must stand. "The law requires a licensed person" must SURVIVE, because it is a real
    legal requirement and hiding it could hurt a farm, an animal or the company. Both sides are tested here with realistic sentences.
    """

    # Real legal or professional requirements and emergencies. EVERY one of these must come out of the guard exactly as it went in.
    MUST_SURVIVE = {
        "cao": (
            "A notifiable disease must be reported by law, and a licensed vet has to confirm it.",
            "This looks like a reportable disease, so you must notify the competent authority within 24 hours.",
            "Antibiotics are prescription-only, so a licensed veterinarian must prescribe and supervise them.",
            "Under the regulations, a statutory report to the national fisheries authority is required for this outbreak.",
            "If you see mass mortality, call a fish vet immediately and report it to the authority.",
            "A suspected outbreak is an emergency: contact a veterinarian today and isolate the pond.",
            "Because this is a food safety risk, a registered vet is required to sign the health certificate.",
            "Only a licensed professional may legally issue a withdrawal period for a medicated feed.",
        ),
        "coo": (
            "A statutory tax return must be signed by a licensed accountant, as the law requires.",
            "An audit sign-off is mandatory, so a registered auditor has to approve the accounts.",
            "A binding legal commitment like this contract must be reviewed by a qualified lawyer before you sign.",
            "The regulator requires an authorised signatory for this licence application.",
        ),
        "cmo": (
            "An investor document needs legal review before it is published, because securities rules require it.",
            "A veterinary-product claim is regulated: a licensed professional must substantiate it before the advertisement runs.",
            "Advertising rules require documented substantiation for this claim, so legal must approve it.",
        ),
    }

    # The SAME people mentioned as a lazy hand-off of ROUTINE work. Every one of these must be removed.
    MUST_GO = {
        "cao": ("Consult a fish vet before you choose the dose.", "Ask a veterinarian to pick the feeding rate.", "Get a biologist to design the aeration plan."),
        "coo": ("Ask your operations manager to build the schedule.", "Have an accountant prepare the forecast.", "Check with your finance team about the cost."),
        "cmo": ("Hire an agency to write the post.", "Ask a copywriter to draft the subject line.", "Get a designer to choose the colours."),
    }

    def test_every_genuine_legal_professional_or_safety_requirement_survives_untouched(self):
        for role, sentences in self.MUST_SURVIVE.items():
            for sentence in sentences:
                reply = f"Here is what to do first. {sentence} Then recheck in the morning."
                result = g.guard(reply, role)
                self.assertEqual(result.text, reply, f"{role} LOST: {sentence}")
                self.assertEqual(result.removed, [], f"{role} removed: {sentence}")

    def test_the_lazy_hand_off_of_routine_work_is_removed_in_the_same_replies(self):
        for role, sentences in self.MUST_GO.items():
            for sentence in sentences:
                result = g.guard(f"Here is what to do first. {sentence} Then recheck in the morning.", role)
                self.assertEqual(result.text, "Here is what to do first. Then recheck in the morning.", f"{role} kept: {sentence}")
                self.assertEqual(len(result.removed), 1, f"{role}: {sentence}")

    def test_both_sides_in_one_reply_the_hand_off_goes_and_the_legal_requirement_stays(self):
        legal = "Call a licensed vet to confirm it, because a notifiable disease must be reported by law."
        reply = f"Consult a fish vet before you choose the dose. Dose at 5 mg/L for 3 days. {legal}"
        result = g.guard(reply, "cao")
        self.assertNotIn("choose the dose", result.text)
        self.assertIn("Dose at 5 mg/L for 3 days.", result.text)
        self.assertIn(legal, result.text)
        self.assertEqual(len(result.removed), 1)
        # This sentence BOTH names a vet and states a legal duty, so it is exactly the kind the guard weighed and kept on purpose. It is
        # reported, so a person can see what survived and why. (A legal sentence that names nobody to hand work to is never even weighed.)
        self.assertEqual(result.kept_required, [legal])

    def test_what_survived_for_a_legal_reason_is_logged_so_a_person_can_see_it(self):
        result = g.guard("Call a licensed vet: a notifiable disease must be reported by law.", "cao")
        report = g.findings(result, "cao")
        self.assertTrue(any("legal, professional or safety escalation" in line for line in report), report)
        self.assertTrue(any("(CAO)" in line for line in report))

    def test_a_removal_is_always_logged_with_the_role_and_the_sentence(self):
        for role in SOLO:
            result = g.guard("Ask a copywriter. Ask a vet. Ask your operations manager. I can draft that if you like.", role)
            report = " ".join(g.findings(result, role))
            self.assertIn(f"({role.upper()})", report)
            self.assertTrue(result.removed or result.offers, role)

    def test_a_vet_named_as_the_legal_step_and_a_lazy_hand_off_are_judged_one_sentence_at_a_time(self):
        reply = "Ask a vet to pick the dose. A licensed vet is required by law to prescribe the antibiotic."
        self.assertEqual(g.guard(reply, "cao").text, "A licensed vet is required by law to prescribe the antibiotic.")

    def test_the_licence_word_alone_is_not_a_free_pass_for_a_lazy_hand_off(self):
        # "licensed" only protects a sentence that also says it is an obligation. A casual "ask a licensed vet" is still a hand-off.
        result = g.guard("Ask a licensed vet to choose the feed for you.", "cao")
        self.assertEqual(result.text, "")
        self.assertEqual(len(result.removed), 1)

    def test_when_in_doubt_the_guard_keeps_the_sentence(self):
        for sentence in ("Antibiotic use is regulated in most countries, so check the label and local rules first.",
                         "Withdrawal periods are set by the regulator, and a vet can confirm yours."):
            self.assertEqual(g.guard(sentence, "cao").text, sentence, sentence)

    def test_an_emergency_is_kept_even_without_a_legal_word(self):
        for sentence in ("If fish are dying suddenly, call a fish vet immediately.", "In an emergency like this, get a veterinarian on the phone today.",
                         "A mass mortality event needs a fish vet the same day."):
            result = g.guard(sentence, "cao")
            self.assertEqual(result.text, sentence, sentence)  # the point: it survives. (It is only LOGGED as kept-for-safety when it also looked like a
            self.assertEqual(result.removed, [], sentence)  # hand-off; a sentence that never matched a hand-off pattern is simply never weighed.)

    def test_realistic_farm_emergencies_are_kept_however_they_are_worded(self):
        # Found by stress-testing the first version of the safety list: real emergencies that did not use the words it listed were being cut.
        for sentence in ("If your fish start dying in large numbers, contact a veterinarian today.", "If you see unusual deaths, get a veterinarian involved straight away.",
                         "A sudden spike in mortality means you should call a fish vet.", "If more than 2% of the stock die in a day, call a vet.",
                         "Fish gasping with red sores could be a serious disease, so call a fish vet at once.",
                         "If the fish show signs of a contagious disease, isolate the pond and phone a veterinarian."):
            self.assertEqual(g.guard(sentence, "cao").text, sentence, sentence)

    def test_a_warning_and_its_call_to_action_are_never_separated(self):
        # "Lots of dead fish at the surface? Call a fish vet right now." The second sentence alone looks like a lazy hand-off.
        reply = "Lots of dead fish at the surface? Call a fish vet right now."
        self.assertEqual(g.guard(reply, "cao").text, reply)
        legal_then_action = "A notifiable disease must be reported by law. Call a fish vet to confirm it."
        self.assertEqual(g.guard(legal_then_action, "cao").text, legal_then_action)

    def test_but_a_routine_hand_off_after_an_ordinary_sentence_is_still_removed(self):
        self.assertEqual(g.guard("The pond looks healthy today. Ask a veterinarian to pick the feeding rate.", "cao").text, "The pond looks healthy today.")

    def test_more_lazy_hand_offs_with_decision_verbs_are_caught(self):
        for sentence in ("Have a vet decide how much feed to give.", "Get a vet to review the stocking density.", "Ask a vet whether to add more aeration."):
            self.assertEqual(g.guard(sentence, "cao").text, "", sentence)

    def test_the_same_emergency_wording_does_not_shield_a_routine_hand_off(self):
        # "urgent" and "immediately" are NOT on the safety list: they are everyday words, and a lazy hand-off must not hide behind them.
        result = g.guard("Immediately ask a veterinarian to pick the feeding rate.", "cao")
        self.assertEqual(result.text, "")
        self.assertEqual(len(result.removed), 1)


class TheSentenceSplitterNeverDamagesCopyTests(unittest.TestCase):
    """The guard removes whole sentences, so how it finds them matters: a wrong cut would delete the copy, the dose or the figure the reader needed."""

    def test_the_pieces_always_join_back_into_exactly_the_line(self):
        for line in ("One. Two! Three? Four.", "**Bold. Still bold.** After it. And more.", "Dose `5.5 mg/L. now` then pH 7.5. Next.", "No stop at the end",
                     "  leading space. Then more.  ", "Ellipsis... then text. Done."):
            self.assertEqual("".join(g.split_sentences(line)), line, line)

    def test_a_decimal_a_version_and_a_unit_are_not_sentence_ends(self):
        self.assertEqual(len(g.split_sentences("Hold the pH at 7.5 and the ammonia under 0.5 mg/L today.")), 1)
        self.assertEqual(len(g.split_sentences("Use version 2.1.3 of the guide.")), 1)

    def test_a_full_stop_inside_bold_or_code_never_ends_a_sentence(self):
        self.assertEqual(len(g.split_sentences("**Fewer water changes. Calmer ponds.** Try it.")), 2)
        self.assertEqual(len(g.split_sentences("Use `a. b. c.` as the pattern. Then stop.")), 2)

    def test_a_bold_line_of_copy_ending_in_a_full_stop_is_its_own_sentence(self):
        pieces = g.split_sentences("Here is the hook: **Fewer water changes.** I can draft the full post if you like.")
        self.assertEqual(len(pieces), 2)
        self.assertTrue(pieces[0].rstrip().endswith("**"))

    def test_removing_an_offer_after_bold_copy_leaves_the_copy_whole_and_its_bold_balanced(self):
        for role in SOLO:
            result = g.guard("Here is the hook: **Fewer water changes.** I can draft the full post if you like.", role)
            self.assertEqual(result.text, "Here is the hook: **Fewer water changes.**", role)
            self.assertEqual(result.text.count("**") % 2, 0, role)

    def test_a_reply_with_no_offending_sentence_comes_back_byte_for_byte_whatever_it_contains(self):
        tricky = "**Fewer water changes. Calmer ponds.** Then `a. b.` ok.\n\n### The Plan\n1. Hold pH at 7.5.\n2. Re-test in `30 min`.\n\n```\nSubject: Less. More.\n```"
        for role in g.ROLES:
            self.assertEqual(g.guard(tricky, role).text, tricky, role)


class TheClosingLineAndVerdictsAreProtectedTests(unittest.TestCase):
    def test_the_required_closing_line_survives_even_if_its_only_sentence_was_a_hand_off(self):
        for role in SOLO:
            result = g.guard("Here is the plan.\n\nRecommendation for human review: hire an agency to write it.", role)
            self.assertIn("Recommendation for human review:", result.text, role)

    def test_a_cross_check_verdict_is_never_touched_for_any_role(self):
        verdict = json.dumps({"valid": False, "reasoning": "Consult a fish vet and ask your operations manager. Hire an agency.",
                              "confidence": 0.7, "constitutional_alignment": "aligned"})
        for role in g.ROLES:
            result = g.guard(verdict, role)
            self.assertEqual(result.text, verdict, role)
            self.assertEqual(json.loads(result.text)["reasoning"], json.loads(verdict)["reasoning"])
            self.assertEqual((result.removed, result.offers), ([], []), role)

    def test_empty_none_and_unknown_role_pass_through_and_nothing_raises(self):
        for role in g.ROLES:
            self.assertEqual(g.guard(None, role).text, "")
            self.assertEqual(g.guard("   ", role).text, "   ")
        self.assertEqual(g.guard("Ask a vet.", "nobody").text, "Ask a vet.")


class EveryAgentsRealReplyPathRunsTheGuardTests(unittest.TestCase):
    """
    A WHOLE CALL for each executive with a scripted model that behaves badly in the CEO's own kind of words. These prove the guard is wired into
    every agent's real reply path (not just that the guard works in isolation), that the chat reply keeps its structure and its 5000-character room,
    and that the constitution's scoring and the closing line are unaffected.
    """

    BAD = {
        "cao": ("ESCALATE TO HUMAN: my confidence is low here. Consult a fish vet before you choose the dose. Dose at `5 mg/L` for 3 days. "
                "I can draft a fuller protocol if you like. A notifiable disease must be reported by law."),
        "coo": "Ask your operations manager to build the schedule. Move the delivery to **Tuesday**. I can build that schedule if you like.",
        "cmo": "You should hire an agency for this. Here is the hook: **Fewer water changes.** I can draft the full post if you like.",
        "cto": "ESCALATE TO HUMAN: this touches the database. Get a developer to run it. Run `npx prisma migrate deploy`.",
    }
    GONE = {"cao": ("consult a fish vet", "draft a fuller protocol", "escalate to human"), "coo": ("operations manager", "build that schedule if you like"),
            "cmo": ("hire an agency", "draft the full post"), "cto": ("get a developer", "escalate to human")}
    KEPT = {"cao": ("`5 mg/L`", "notifiable disease must be reported by law", "Recommendation for CEO review"), "coo": ("**Tuesday**",),
            "cmo": ("**Fewer water changes.**",), "cto": ("`npx prisma migrate deploy`", "Recommendation for CEO review")}

    @staticmethod
    def payload(chat):
        # The scorer wants the closing line on a line of its own, and some reasoning and a source, exactly as a real answer has them.
        return {"response": chat + "\n\nBecause the evidence supports it (general knowledge), my confidence is 85%.\n\nRecommendation for human review: approve the plan.",
                "reasoning_chain": ["Step 1: the real problem.", "Data and sources: general knowledge.", "Assumptions: none.", "Limitations: not run."],
                "scenarios": None, "pre_consultation_requests": {"coo_query": None, "cto_query": None}, "chat_reply": chat}

    @staticmethod
    def client_that_says(text):
        async def create(**kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    @staticmethod
    def clean_env():
        return {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX", "COO_AUTO_APPROVAL")}

    def run_agent(self, agent, chat):
        module = AGENTS[agent]
        call = getattr(module, f"get_{agent}_result")
        extra = {"images": False} if agent == "cmo" else ({"reader": False} if agent == "cto" else {})
        client = self.client_that_says(json.dumps(self.payload(chat)))
        with mock.patch.object(module, "_client", client), mock.patch.dict(os.environ, self.clean_env(), clear=True), contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(call("Please help.", None, use_rag=False, audit=False, memory=False, **extra))

    def test_every_agent_loses_its_bad_phrases_and_keeps_its_substance(self):
        for agent in AGENTS:
            result = self.run_agent(agent, self.BAD[agent])
            for text in (result.reply, result.chat_reply):
                for gone in self.GONE[agent]:
                    self.assertNotIn(gone, text.lower(), f"{agent}: {gone}")
                for kept in self.KEPT[agent]:
                    self.assertIn(kept, text, f"{agent}: {kept}")

    def test_the_legal_sentence_survives_the_whole_call_for_the_cao(self):
        legal = "Call a licensed vet to confirm it, because a notifiable disease must be reported by law."
        result = self.run_agent("cao", f"Consult a fish vet before you choose the dose. {legal}")
        for text in (result.reply, result.chat_reply):
            self.assertIn(legal, text)
            self.assertNotIn("choose the dose", text)

    def test_what_the_guard_did_is_in_the_findings_so_a_person_can_see_it(self):
        for agent in SOLO:
            result = self.run_agent(agent, self.BAD[agent])
            self.assertTrue(any("language guard" in f and f"({agent.upper()})" in f for f in result.format_findings), (agent, result.format_findings))

    def test_a_long_structured_reply_is_kept_whole_up_to_5000_characters_for_every_agent(self):
        steps = "### The Plan\n" + "1. **Do** the thing `now`.\n" * 120  # about 3,400 characters: over the old 1200 cap, under the new 5000
        self.assertTrue(1200 < len(steps) < 5000)
        for agent in AGENTS:
            result = self.run_agent(agent, steps)
            self.assertTrue(result.chat_reply.startswith(steps.rstrip()), agent)
            self.assertGreater(len(result.chat_reply), 3000, agent)
        for agent in AGENTS:
            self.assertLessEqual(len(self.run_agent(agent, "word " * 3000).chat_reply), 5000, agent)

    def test_scoring_and_the_closing_line_are_unaffected_by_the_guard(self):
        for agent in AGENTS:
            result = self.run_agent(agent, self.BAD[agent])
            self.assertIn("recommendation for human review:", result.reply.lower(), agent)
            self.assertGreaterEqual(result.constitutional_score, 70, agent)
            self.assertFalse(result.needs_review, agent)

    def test_a_well_behaved_reply_is_left_exactly_as_the_model_wrote_it(self):
        good = {"cao": "### What I Am Seeing\nFish at the surface at dawn.\n\n1. Measure oxygen at **05:30**.",
                "coo": "### The Constraint\nTwo staff are on shift.\n\n1. Move the delivery to **Tuesday**.",
                "cmo": "### Who It Is For\nFarm owners.\n\n**Fewer water changes. Calmer ponds.**"}
        for agent, chat in good.items():
            self.assertTrue(self.run_agent(agent, chat).chat_reply.startswith(chat), agent)

    def test_a_cross_check_review_of_each_agent_still_returns_a_bare_parsable_verdict(self):
        verdict = json.dumps({"valid": False, "reasoning": "Consult a fish vet and ask your operations manager. Hire an agency.",
                              "confidence": 0.7, "constitutional_alignment": "aligned"})
        for agent, module in AGENTS.items():
            answer_fn = getattr(module, f"get_{agent}_answer")
            with mock.patch.object(module, "_client", self.client_that_says(verdict)), mock.patch.dict(os.environ, self.clean_env(), clear=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                out = asyncio.run(answer_fn("review this", use_rag=False, audit=False))
            parsed = json.loads(out[out.index("{"): out.rindex("}") + 1])
            self.assertEqual(parsed["reasoning"], json.loads(verdict)["reasoning"], f"{agent} damaged the verdict")


# __CLASS5__
