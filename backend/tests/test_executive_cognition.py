"""
The cognitive upgrade's prompt side: the proactive expert roles (CAO, COO, CMO), the Strategic Thinking Process every executive
follows (the CTO included), and the guarantee that none of it touched the JSON schemas or the authorization workflow.

These are checks on the TEXT the model is given. They prove the instructions are present, in the right place and consistent with
the rest of the prompt. They cannot prove the model follows them: no real-model run has checked that yet.
No database, no network.
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import cao_agent, cmo_agent, coo_agent, cto_agent, council  # noqa: E402
from app.agents.cao_reasoning import MAX_CHAIN_ENTRIES  # noqa: E402
from app.prompts import cao_prompt, chat_style, cmo_prompt, coo_prompt, cto_prompt, persona  # noqa: E402

AGENTS = {"cao": cao_agent, "coo": coo_agent, "cmo": cmo_agent, "cto": cto_agent}
ROLE_TITLE = {"cao": "PROACTIVE AQUACULTURE CONSULTANT", "coo": "TOP OPERATIONS EXPERT", "cmo": "CREATIVE GURU", "cto": "SOFTWARE ENGINEERING EXPERT"}
SCHEMA_PROMPTS = {"cao": cao_prompt.CAO_REASONING_PROMPT, "coo": coo_prompt.COO_REASONING_PROMPT, "cmo": cmo_prompt.CMO_REASONING_PROMPT,
                  "cto": cto_prompt.CTO_REASONING_PROMPT}


class ExpertRoleTests(unittest.TestCase):
    def test_every_executive_prompt_carries_its_own_role_once_and_not_the_others(self):
        for agent, module in AGENTS.items():
            prompt = module.BASE_SYSTEM_PROMPT
            self.assertEqual(prompt.count(ROLE_TITLE[agent]), 1, agent)
            for other, title in ROLE_TITLE.items():
                if other != agent:
                    self.assertNotIn(title, prompt, f"{agent} prompt contains the {other} role")

    def test_the_cao_is_a_proactive_aquaculture_consultant(self):
        role = persona.expert_role("cao")
        for needed in ("ANTICIPATE", "biological and environmental risks", "water-quality optimisations", "feed alternatives", "disease-prevention"):
            self.assertIn(needed, role)

    def test_the_coo_is_a_top_operations_expert(self):
        role = persona.expert_role("coo")
        for needed in ("ANTICIPATE bottlenecks", "efficiency", "supply chain", "allocated", "PROPOSE operational solutions"):
            self.assertIn(needed, role)

    def test_the_cmo_is_a_creative_guru(self):
        role = persona.expert_role("cmo")
        for needed in ("market trends", "consumer behaviour", "novel campaigns", "A/B tests", "brand-positioning"):
            self.assertIn(needed, role)

    def test_the_cto_keeps_its_engineer_role_and_gets_the_same_thinking_process(self):
        self.assertIn("SOFTWARE ENGINEERING EXPERT", cto_agent.BASE_SYSTEM_PROMPT)
        self.assertIn("STRATEGIC THINKING PROCESS", cto_agent.BASE_SYSTEM_PROMPT)

    def test_there_is_no_expert_role_for_an_agent_that_is_not_one_of_the_three(self):
        for agent in ("cto", "dr_aqua", "nobody"):
            with self.assertRaises(KeyError):
                persona.expert_role(agent)

    def test_no_role_lets_the_ai_decide_or_act_on_its_own(self):
        for agent in ("cao", "coo", "cmo"):
            role = persona.expert_role(agent).lower()
            self.assertNotIn("you decide", role)
            self.assertNotIn("without approval", role)
            self.assertNotIn("execute", role)
        self.assertIn("A human decides", persona.THINKING_PROCESS_PROMPT)

    def test_each_role_says_the_existing_rules_below_it_still_bind(self):
        self.assertIn("SND PROBIOTIC rules below still binds you", persona.expert_role("cao"))
        self.assertIn("rules below still binds you", persona.expert_role("coo"))
        self.assertIn("claims discipline below still binds you", persona.expert_role("cmo"))


class ThinkingProcessTests(unittest.TestCase):
    def test_all_four_executives_are_given_the_process_exactly_once(self):
        for agent, module in AGENTS.items():
            self.assertEqual(module.BASE_SYSTEM_PROMPT.count("STRATEGIC THINKING PROCESS"), 1, agent)

    def test_it_asks_for_the_real_problem_two_or_three_options_and_a_recommended_path(self):
        text = persona.THINKING_PROCESS_PROMPT
        self.assertIn("Step 1: The real problem", text)
        self.assertIn("You asked for X, but the underlying issue looks like Y", text)
        self.assertIn("Step 2: The options", text)
        self.assertIn("2 or 3 genuinely different ways", text)
        self.assertIn("Step 3: The best path", text)
        self.assertIn("which option you recommend and why", text)
        self.assertIn("risks and the constraints", text)
        self.assertLess(text.index("Step 1"), text.index("Step 2"))
        self.assertLess(text.index("Step 2"), text.index("Step 3"))

    def test_the_thinking_goes_in_the_existing_reasoning_chain_the_ui_already_hides(self):
        text = persona.THINKING_PROCESS_PROMPT
        self.assertIn('"reasoning_chain"', text)
        self.assertIn("internal reasoning", text)  # the toggle's name
        self.assertIn("do not copy the three steps into the response", text)  # so the main chat is not cluttered

    def test_it_happens_before_the_recommendation_and_before_any_proposed_action(self):
        self.assertIn("Before you write your recommendation or any action you propose", persona.THINKING_PROCESS_PROMPT)

    def test_the_cmo_and_cto_are_told_it_applies_before_their_authorization_request_and_the_cto_before_its_plan(self):
        self.assertIn("before you write an authorization_request", persona.thinking_process("cmo"))
        self.assertNotIn("execution_plan", persona.thinking_process("cmo"))
        self.assertIn("authorization_request or an execution_plan", persona.thinking_process("cto"))

    def test_the_cao_and_coo_are_never_told_about_plans_or_image_tools_they_do_not_have(self):
        # tests/test_phase_6_6_prompts.py pins this for the real prompts; this checks the new text on its own.
        for agent in ("cao", "coo"):
            text = persona.thinking_process(agent) + persona.expert_role(agent)
            for word in ("execution_plan", "generate_image", "authorization_request"):
                self.assertNotIn(word, text, f"{agent} was told about {word}")

    def test_the_voice_blocks_redirect_sentences_still_have_their_real_em_dashes(self):
        # A byte-level rewrite once turned these into garbage characters; tests/test_persona.py pins the exact sentences.
        for agent in ("cto", "cmo", "cao", "coo"):
            self.assertIn("\u2014", persona.REDIRECTS[agent])
            self.assertNotIn("\u00e2\u20ac", persona.REDIRECTS[agent])

    def test_it_comes_after_the_constitution_and_voice_and_before_the_role_specific_format(self):
        for agent, module in AGENTS.items():
            prompt = module.BASE_SYSTEM_PROMPT
            marks = [prompt.index("END OF EXECUTIVE CONSTITUTION"), prompt.index("VOICE, PLAIN LANGUAGE AND SCOPE"),
                     prompt.index("STRATEGIC THINKING PROCESS"), prompt.index(f"You are AI_{agent.upper()}")]
            self.assertEqual(marks, sorted(marks), agent)

    def test_the_steps_fit_inside_the_chain_length_the_parser_accepts(self):
        # Up to 5 Step entries plus Data and sources, Assumptions and Limitations = 8, and the parser keeps up to MAX_CHAIN_ENTRIES.
        self.assertGreaterEqual(MAX_CHAIN_ENTRIES, 5 + 3)
        self.assertIn("up to 5 in all", persona.THINKING_PROCESS_PROMPT)
        for agent, schema in SCHEMA_PROMPTS.items():
            self.assertIn("3 to 5 entries", schema, agent)  # the existing schema still says the same thing

    def test_a_cross_check_review_may_still_answer_with_only_its_json_verdict(self):
        self.assertIn("answer with only that verdict", persona.THINKING_PROCESS_PROMPT)

    def test_a_trivial_question_does_not_have_to_be_padded(self):
        self.assertIn("do not pad", persona.THINKING_PROCESS_PROMPT)


class SchemasAreUntouchedTests(unittest.TestCase):
    """The upgrade only ADDS prompt text. These pin the contracts that the rest of the platform depends on."""

    def test_the_json_field_names_are_still_demanded_by_each_agent(self):
        for agent, module in AGENTS.items():
            for field in ('"response"', '"reasoning_chain"', '"chat_reply"'):
                self.assertIn(field, module.BASE_SYSTEM_PROMPT, f"{agent} lost {field}")
        for agent in ("cao", "coo", "cmo"):
            self.assertIn('"scenarios"', AGENTS[agent].BASE_SYSTEM_PROMPT)
        for agent in ("cto", "cmo"):
            self.assertIn('"authorization_request"', AGENTS[agent].BASE_SYSTEM_PROMPT)
        self.assertIn('"execution_plan"', cto_agent.BASE_SYSTEM_PROMPT)

    def test_the_constitutions_required_lines_are_still_there(self):
        for agent, module in AGENTS.items():
            self.assertIn("Recommendation for human review:", module.BASE_SYSTEM_PROMPT, agent)
            self.assertIn("Recommendation for CEO review", module.BASE_SYSTEM_PROMPT, agent)

    def test_the_constitution_still_comes_before_everything_this_upgrade_added(self):
        for agent, module in AGENTS.items():
            prompt = module.BASE_SYSTEM_PROMPT
            self.assertLess(prompt.index("EXECUTIVE CONSTITUTION"), prompt.index("STRATEGIC THINKING PROCESS"), agent)
            self.assertLess(prompt.index("EXECUTIVE CONSTITUTION"), prompt.index(ROLE_TITLE[agent]), agent)

    def test_the_new_text_has_no_braces_so_it_cannot_break_the_format_calls_that_fill_in_retrieved_excerpts(self):
        for text in (*persona._EXPERT_ROLES.values(), persona.THINKING_PROCESS_PROMPT):
            self.assertNotIn("{", text)
            self.assertNotIn("}", text)

    def test_the_boardroom_prompts_are_not_changed_by_this_upgrade(self):
        for agent in ("cao", "coo", "cto", "cmo"):
            prompt = council.system_prompt_for(agent)
            self.assertNotIn("STRATEGIC THINKING PROCESS", prompt)
            self.assertNotIn("PROACTIVE AQUACULTURE CONSULTANT", prompt)

    def test_the_prompts_stay_inside_a_sane_size(self):
        for agent, module in AGENTS.items():
            self.assertLess(len(module.BASE_SYSTEM_PROMPT) // 4, 16_000, f"{agent} prompt is over ~16k tokens")


CTO = cto_agent.BASE_SYSTEM_PROMPT
PROTOCOL = persona.CTO_ENGINEER_PROMPT[persona.CTO_ENGINEER_PROMPT.index("COMMUNICATION PROTOCOL: HOW YOU TALK TO THE CEO"):]
# The wording the CEO called gatekeeping. It must not be a phrase the CTO is TOLD to say; the protocol may quote it only to forbid it.
GATEKEEPER_PHRASES = ("a human should confirm", "a human must decide", "Nothing has been changed by me", "pending human approval")


class CtoCommunicationProtocolTests(unittest.TestCase):
    """The CTO talks like a friendly, expert assistant to a visionary CEO (the "Qwen-style" communication protocol)."""

    def test_the_protocol_is_in_the_ctos_real_prompt_once_and_after_the_voice_block_and_before_its_old_role_text(self):
        self.assertEqual(CTO.count("COMMUNICATION PROTOCOL: HOW YOU TALK TO THE CEO"), 1)
        order = [CTO.index("VOICE, PLAIN LANGUAGE AND SCOPE"), CTO.index("SOFTWARE ENGINEERING EXPERT"),
                 CTO.index("COMMUNICATION PROTOCOL: HOW YOU TALK"), CTO.index("STRATEGIC THINKING PROCESS"), CTO.index("You are AI_CTO,")]
        self.assertEqual(order, sorted(order))

    def test_each_executive_has_its_own_tuned_protocol_and_only_the_cto_has_the_coders_one(self):
        # Communication parity: the CAO, COO and CMO now carry the same protocol, tuned to their role (tests/test_executive_parity.py). What stays
        # CTO-only is the CODER wording: the CTO talks to a CEO "who has never opened the code".
        for agent in ("cao", "coo", "cmo"):
            prompt = AGENTS[agent].BASE_SYSTEM_PROMPT
            self.assertEqual(prompt.count("COMMUNICATION PROTOCOL"), 1, agent)
            self.assertNotIn("NOT A CODER", prompt, agent)
            self.assertNotIn("never opened the code", prompt, agent)
            self.assertNotIn("No bullet points, no headings", prompt, agent)  # the shared plain-reply rule is gone for them: it would contradict the protocol

    def test_rule_1_speak_to_a_visionary_ceo_with_analogies_and_step_by_step_guidance(self):
        for needle in ("SPEAK TO A VISIONARY CEO, NOT A CODER", "never opened the code", "Never talk down to them",
                       "short, accurate everyday analogy", "the database is the company's record book",
                       "Never assume they know where anything is", "step-by-step guidance", "one action per step",
                       "what they should see when it worked", "never invent one"):
            self.assertIn(needle, PROTOCOL, needle)

    def test_rule_2_structured_formatting_numbered_lists_bold_and_clear_headings(self):
        for needle in ("ALWAYS USE STRUCTURED FORMATTING", "numbered list (1., 2., 3.)", "one action per number", "**bold**",
                       "key terms, file names and any important warning", "### Step 1", "### What to Look For", "### Why This Matters",
                       "### My Recommendation", "No tables, no HTML, no emojis"):
            self.assertIn(needle, PROTOCOL, needle)

    def test_a_simple_question_gets_a_short_warm_answer_not_a_template(self):
        self.assertIn("A very simple question", PROTOCOL)
        self.assertIn("short, warm answer, not a template", PROTOCOL)

    def test_rule_3_explain_the_why_before_asking_the_ceo_to_do_something(self):
        for needle in ("EXPLAIN THE WHY BEFORE YOU ASK", "Before you ask the CEO to do, check or approve anything",
                       "why it matters, in business terms", "the foundation of the Learning Loop"):
            self.assertIn(needle, PROTOCOL, needle)
        self.assertLess(PROTOCOL.index("EXPLAIN THE WHY"), PROTOCOL.index("BRING SOLUTIONS"))

    def test_rule_4_proactive_solutions_one_or_two_actionable_options_and_a_clear_recommendation(self):
        for needle in ("BRING SOLUTIONS, NOT JUST PROBLEMS", "never stop at describing it", "1 or 2 clear, actionable solutions",
                       "its first step and its trade-off", "say which one you recommend and why", "I recommend option 1, because"):
            self.assertIn(needle, PROTOCOL, needle)

    def test_rule_5_the_safety_guardrail_is_reframed_as_collaborative_and_empowering_language(self):
        for needle in ("KEEP THE SAFEGUARDS, SAY THEM LIKE A PARTNER", "as a partner describing the safest path", "never as a gatekeeper",
                       "I have outlined the safest path forward and the trade-offs",
                       "Once you confirm this fits your vision, I will prepare the precise execution plan for your final approval",
                       "your final approval", "your decision", "your call"):
            self.assertIn(needle, PROTOCOL, needle)


class CtoProtocolSafetyTests(unittest.TestCase):
    """Friendlier wording must not weaken a safeguard, hide a risk, or bring back the gatekeeper phrases."""

    def test_the_gatekeeper_phrases_are_forbidden_not_instructed(self):
        forbidden = next(line for line in PROTOCOL.splitlines() if line.startswith("- Do not say"))
        for phrase in ("a human must decide", "a human should confirm", "Nothing has been changed by me", "pending human approval"):
            self.assertIn(phrase, forbidden, phrase)  # each appears only inside the "do not say" instruction
            self.assertEqual(PROTOCOL.count(phrase), 1, phrase)
        rest = persona.CTO_ENGINEER_PROMPT.replace(forbidden, "")
        for phrase in GATEKEEPER_PHRASES:
            self.assertNotIn(phrase, rest, phrase)

    def test_the_old_gatekeeper_wording_the_cto_was_told_to_say_is_gone_from_its_real_prompt(self):
        self.assertNotIn("A human decides", CTO)  # the CTO's engineer and thinking lines now say "The final call is the CEO's."
        self.assertIn("The final call is the CEO's", CTO)
        self.assertNotIn("nothing is applied until a human approves", CTO)
        self.assertNotIn("that nothing has been executed and that a human must review and approve the request first", CTO)
        self.assertIn("the plan is ready for the CEO's final approval, and that nothing is applied until the CEO approves it", CTO)
        # The old TONE AND STYLE instruction is replaced. (The protocol still NAMES that phrase, once, to say it wins over it.)
        self.assertNotIn("- Precise, practical and direct. Lead with the bottom line", CTO)
        self.assertEqual(CTO.count("Precise, practical and direct"), 1)
        self.assertIn("Clear, practical and friendly, as the COMMUNICATION PROTOCOL above describes", CTO)

    def test_the_other_executives_keep_their_human_decides_wording(self):
        for agent in ("cao", "coo", "cmo"):
            self.assertIn("A human decides", AGENTS[agent].BASE_SYSTEM_PROMPT, agent)

    def test_the_protocol_cannot_be_used_to_claim_an_action_or_soften_a_safety_warning(self):
        for needle in ("It changes no fact you must state, no safety rule, no limit, and not the JSON format",
                       "Never write that you have deployed, applied, fixed, updated, sent or published anything",
                       'Say "prepared" or "drafted" only when the plan is really in this answer',
                       "Friendly never means vague", "still said clearly, in bold"):
            self.assertIn(needle, PROTOCOL, needle)

    def test_the_constitutions_required_lines_keep_their_exact_opening_words(self):
        for needle in ('"Confidence:"', '"Recommendation for human review:"', '"Recommendation for CEO review:"', "Make what follows the colon collaborative"):
            self.assertIn(needle, PROTOCOL, needle)
        self.assertIn("Recommendation for human review:", CTO)  # the constitution is untouched and the scorer still finds the line

    def test_the_protocol_asks_for_nothing_the_real_model_refused_in_the_boardroom(self):
        for banned in ("private working", "behind a toggle", "your private", "keep this secret", "hide"):
            self.assertNotIn(banned, PROTOCOL.lower(), banned)

    def test_the_protocol_wins_over_the_old_plain_text_and_precise_voice_for_the_cto_only(self):
        self.assertIn('it wins over the "Precise, practical and direct" voice', PROTOCOL)
        self.assertIn("plain text without headings, lists or bold", PROTOCOL)

    def test_the_protocol_has_no_braces_so_it_cannot_break_a_format_call(self):
        self.assertNotIn("{", PROTOCOL)
        self.assertNotIn("}", PROTOCOL)


class CtoChatReplyPromptTests(unittest.TestCase):
    """What the CEO reads first is the chat_reply, so its own prompt must allow and ask for the structured, friendly layout."""

    CHAT = chat_style.CTO_CHAT_REPLY_PROMPT

    def test_the_cto_has_its_own_chat_reply_block_and_the_others_keep_the_old_one(self):
        self.assertEqual(CTO.count("CONVERSATIONAL REPLY (MANDATORY"), 1)
        self.assertIn(self.CHAT.strip(), CTO)
        self.assertNotIn(chat_style.CHAT_REPLY_PROMPT.strip(), CTO)
        # Parity: the other three now have their OWN structured prompt too, not the shared plain one and not the CTO's.
        for agent in ("cao", "coo", "cmo"):
            prompt = AGENTS[agent].BASE_SYSTEM_PROMPT
            self.assertIn(chat_style.CHAT_REPLY_PROMPTS[agent].strip(), prompt, agent)
            self.assertNotIn(chat_style.CHAT_REPLY_PROMPT.strip(), prompt, agent)
            self.assertNotIn(self.CHAT.strip(), prompt, agent)

    def test_it_no_longer_forbids_headings_and_lists_and_asks_for_the_protocol_layout(self):
        self.assertNotIn("No bullet points, no headings, no markdown", self.CHAT)
        for needle in ("COMMUNICATION PROTOCOL above", "### headings, numbered steps, **bold**", "asks the CEO to do something",
                       "Lead with the answer or your recommendation, then why it matters, then the steps"):
            self.assertIn(needle, self.CHAT, needle)

    def test_a_simple_question_still_gets_a_short_reply(self):
        self.assertIn("A simple question gets 2 to 4 sentences", self.CHAT)

    def test_every_safeguard_of_the_old_block_is_kept_in_friendlier_words(self):
        for needle in ("add nothing new", 'no fact, number, source or claim that "response" does not contain',
                       "Never say or imply that you have done, sent, approved, published, deployed or changed anything",
                       "the plan is ready for the CEO's final approval and that nothing is applied until the CEO approves it",
                       "below 60%", 'do not add "chat_reply"'):
            self.assertIn(needle, self.CHAT, needle)
        for phrase in GATEKEEPER_PHRASES:
            self.assertNotIn(phrase, self.CHAT, phrase)

    def test_the_length_is_bounded_in_code_and_a_caller_that_passes_no_cap_keeps_the_old_one(self):
        from app.agents import cao_reasoning

        self.assertEqual(cto_agent.CTO_MAX_CHAT_REPLY_CHARS, 5000)
        self.assertEqual(cao_reasoning.MAX_CHAT_REPLY_CHARS, 1200)  # the parser's DEFAULT is unchanged; all four agents now pass 5000 (see test_executive_parity)
        self.assertEqual(cao_reasoning.STRUCTURED_CHAT_REPLY_CHARS, 5000)
        long_reply = json.dumps({"response": "x. Recommendation for human review: y.", "chat_reply": "word " * 5000})
        self.assertEqual(len(cao_reasoning.parse_cao_output(long_reply).chat_reply), 1200)
        longer = cao_reasoning.parse_cao_output(long_reply, chat_reply_chars=5000).chat_reply
        self.assertTrue(1200 < len(longer) <= 5000)

    def test_a_structured_cto_reply_survives_parsing_and_vetting_whole(self):
        from app.agents import cao_reasoning
        from app.agents.chat_reply import vetted_chat_reply

        reply = ("Happy to help. I have prepared the plan for your final approval.\n\n### Step 1\n1. Open the **Authorizations** page.\n"
                 "2. Click **Approve**.\n\n### What to Look For\n- A pull request.\n\nNothing goes live until you approve it and it is merged.")
        raw = json.dumps({"response": "Bottom line. Confidence: 80%. Recommendation for human review: confirm.", "reasoning_chain": ["Step 1: x"],
                          "scenarios": None, "chat_reply": reply})
        parsed = cao_reasoning.parse_cao_output(raw, chat_reply_chars=cto_agent.CTO_MAX_CHAT_REPLY_CHARS)
        self.assertEqual(parsed.chat_reply, reply)
        self.assertEqual(vetted_chat_reply(parsed.chat_reply), reply)  # the partner wording is not mistaken for claiming an action

    def test_a_reply_that_claims_an_action_is_still_dropped(self):
        from app.agents.chat_reply import vetted_chat_reply

        self.assertIsNone(vetted_chat_reply("### Done\nI have deployed the fix and I sent the update."))

    def test_the_json_example_and_the_authorization_schema_are_untouched(self):
        prompt = cto_prompt.CTO_REASONING_PROMPT
        example = json.loads(prompt[prompt.index('{\n  "response"'): prompt.index("\n}\n") + 2])
        self.assertNotIn("chat_reply", example)
        for needle in ("CRITICAL EXECUTION RULE", "files_to_update", "pending_human_approval", "AUTHORIZATION WORKFLOW"):
            self.assertIn(needle, CTO, needle)


class CtoZeroFrictionPromptTests(unittest.TestCase):
    """The instructions: the CTO is the technical person, routine work is not an emergency, and the robotic labels are gone from every prompt."""

    ZERO = persona.CTO_ENGINEER_PROMPT[persona.CTO_ENGINEER_PROMPT.index("ZERO FRICTION"):persona.CTO_ENGINEER_PROMPT.index("COMMUNICATION PROTOCOL: HOW YOU")]

    def test_the_cto_is_told_it_is_the_technical_person_and_never_to_hand_routine_work_back(self):
        for needle in ("You are the technical expert", 'NEVER tell the CEO to get, ask, hire or involve "a technical person"',
                       '"a developer"', '"an engineer"', '"your IT team"', 'never write "I recommend a technical person does this"'):
            self.assertIn(needle, self.ZERO, needle)

    def test_it_is_told_to_give_the_copy_pasteable_solution_in_the_first_reply(self):
        for needle in ("Give the COPY-PASTEABLE solution in the first reply", "`backticks` or a code block", "what the CEO should see when it worked"):
            self.assertIn(needle, self.ZERO, needle)

    def test_a_read_only_query_and_a_standard_prisma_migration_are_routine_not_alarming(self):
        for needle in ("A READ-ONLY QUERY (a SELECT, a count) changes nothing: give it straight away, with no production warning and no approval",
                       "A STANDARD PRISMA MIGRATION IS ROUTINE", "`npx prisma migrate deploy` (from the `frontend` folder", "Never suggest `db push`"):
            self.assertIn(needle, self.ZERO, needle)

    def test_it_must_believe_what_the_ceo_says_they_saw_and_move_to_the_next_step(self):
        for needle in ("BELIEVE WHAT THE CEO SAYS THEY SAW", 'If they got an "already exists" error, the table is already there',
                       "do not ask them to prove it", "give the one next step"):
            self.assertIn(needle, self.ZERO, needle)

    def test_the_emergency_label_is_for_real_emergencies_only_and_the_robotic_phrases_are_only_ever_forbidden(self):
        self.assertIn('"Recommendation for CEO review:" opens an answer only for real emergencies', self.ZERO)
        self.assertIn('Never write "ESCALATE TO HUMAN" or "Waiting for your decision": say "This is ready for your approval"', self.ZERO)
        self.assertEqual(CTO.count("ESCALATE TO HUMAN"), 1)  # only inside that one "never write" instruction
        for agent in ("cao", "coo", "cmo"):
            self.assertNotIn("ESCALATE TO HUMAN", AGENTS[agent].BASE_SYSTEM_PROMPT, agent)
        for line in CTO.splitlines():
            if "a technical person" in line:
                self.assertTrue("NEVER" in line or "never" in line, line[:120])  # every mention is a prohibition

    def test_the_constitutions_opening_line_is_collaborative_and_routine_work_is_not_a_reason_for_it(self):
        from app.agents import base_constitution

        footer = base_constitution.CONSTITUTION_FOOTER
        self.assertIn('"Recommendation for CEO review:"', footer)
        self.assertNotIn("ESCALATE TO HUMAN", footer)
        self.assertIn("a migration, a read-only query or a small interface tweak is routine", footer)
        self.assertIn("never tell the reader to hand it to someone else", footer)
        self.assertIn("CLAUSE 11: HUMAN OVERSIGHT & ESCALATION", base_constitution.EXECUTIVE_CONSTITUTION)  # the clause itself is untouched

    def test_the_new_text_asks_for_nothing_the_real_model_refused_and_has_no_braces(self):
        for banned in ("private working", "behind a toggle", "your private", "keep this secret", "hide"):
            self.assertNotIn(banned, self.ZERO.lower(), banned)
        self.assertNotIn("{", self.ZERO)
        self.assertNotIn("}", self.ZERO)

    def test_the_support_pillar_no_longer_sends_the_user_to_an_engineer(self):
        self.assertNotIn("escalate to an engineer", cto_prompt.CTO_SYSTEM_PROMPT)
        self.assertIn("Never hand it to \"an engineer\" (see ZERO FRICTION)", cto_prompt.CTO_SYSTEM_PROMPT)


class CtoZeroFrictionScenarioTests(unittest.TestCase):
    """
    The two scenarios the CEO reported, run through a whole CTO call with a SCRIPTED model (no network).

    The scripted model BEHAVES BADLY on purpose, in the CEO's own words ("ESCALATE TO HUMAN", "a technical person", "Waiting for your
    decision"). So these prove the CODE stops those words reaching the CEO whatever the model writes. They cannot prove the real model follows
    the new prompt: scripts/try_cto_zero_friction.py does that, against the real model.
    """

    COMMAND = "npx prisma migrate deploy"
    NEW_TOGGLE = "frontend/src/components/mc/ToggleLabel.tsx"
    FORBIDDEN_1 = ("ESCALATE TO HUMAN", "technical person", "get a developer")

    def run_cto(self, payload, message):
        import asyncio
        import contextlib
        import io
        import os
        from unittest import mock

        from tests.test_cto_reads_before_writing import ScriptedClient, text_reply
        from tests.test_read_file_tool import TOKEN

        client = ScriptedClient([text_reply(payload)])
        env = {k: v for k, v in os.environ.items() if k not in ("GOVERNANCE_DATABASE_URL", "DATABASE_URL", "PINECONE_API_KEY", "PINECONE_INDEX")}
        env["GITHUB_PAT"] = TOKEN
        with mock.patch.object(cto_agent, "_client", client), mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("requests.request", side_effect=AssertionError("no network in this test")), \
                mock.patch.object(cto_agent.tools, "github_file_status", lambda path: "missing"):
            result = asyncio.run(cto_agent.get_cto_result(message, None, use_rag=False, audit=False, memory=False))
        return result, client

    def database_payload(self, chat, response=None):
        return {"response": response or chat + "\n\nConfidence: 85%. Recommendation for human review: run the migration.",
                "reasoning_chain": ["Step 1: the table is missing.", "Data and sources: the migration file.", "Assumptions: none.", "Limitations: not run."],
                "scenarios": None, "pre_consultation_requests": {"coo_query": None, "cto_query": None}, "chat_reply": chat,
                "authorization_request": {"action_summary": "Create the ApprovedStrategies table", "risk_level": "high",
                                          "impact": "Adds one table. Nothing existing changes.", "proposed_code_diff": None,
                                          "proposed_commands": ["cd frontend", self.COMMAND], "rollback_plan": "Drop the new table if it is not wanted."}}

    def toggle_payload(self, chat):
        content = 'export default function ToggleLabel() {\n  return <span className="text-lg">Privacy</span>;\n}\n'
        plan = {"branch_name": "agent/bigger-toggle-label", "pr_title": "Make the toggle label larger", "pr_body": "A bigger label.",
                "files_to_update": [{"path": self.NEW_TOGGLE, "content": content, "commit_message": "Bigger label"}]}
        return {"response": chat + "\n\nConfidence: 90%. Recommendation for human review: approve the pull request.",
                "reasoning_chain": ["Step 1: the label is small.", "Data and sources: the component.", "Assumptions: none.", "Limitations: not run."],
                "scenarios": None, "pre_consultation_requests": {"coo_query": None, "cto_query": None}, "chat_reply": chat,
                "authorization_request": {"action_summary": "Make the toggle label larger", "risk_level": "low", "impact": "One label gets bigger.",
                                          "proposed_code_diff": None, "proposed_commands": [], "rollback_plan": "Close the pull request.",
                                          "execution_plan": plan}}

    # ---- Scenario 1: "set up the Learning Loop table"

    def test_scenario_1_missing_table_the_cto_never_escalates_or_hands_it_to_a_technical_person(self):
        chat = ("ESCALATE TO HUMAN: This is a change to your live production database. I recommend a technical person does this. "
                "You could get a developer to run it. Here is the command: `npx prisma migrate deploy`")
        result, _ = self.run_cto(self.database_payload(chat), "The ApprovedStrategies table is missing. Help me set up the Learning Loop table.")
        for text in (result.reply, result.chat_reply):
            for banned in self.FORBIDDEN_1:
                self.assertNotIn(banned.lower(), text.lower(), (banned, text[:200]))

    def test_scenario_1_the_reply_has_collaborative_language_and_the_copy_pasteable_solution(self):
        chat = ("ESCALATE TO HUMAN: this is a change to your live production database. I recommend a technical person does this. "
                "Run `npx prisma migrate deploy` from the frontend folder.")
        result, _ = self.run_cto(self.database_payload(chat), "Help me set up the Learning Loop table.")
        for text in (result.reply, result.chat_reply):
            self.assertIn("Recommendation for CEO review", text)  # the old label, relabelled
            self.assertIn("Ready for your approval", text)  # added by code under the request
            self.assertIn(self.COMMAND, text)  # the exact command is there to copy
        self.assertIn("`npx prisma migrate deploy`", result.chat_reply)  # in a code span, ready to paste
        self.assertEqual(result.authorization_request["proposed_commands"], ["cd frontend", self.COMMAND])  # the request itself is untouched

    def test_scenario_1_a_well_behaved_model_is_left_exactly_as_it_wrote(self):
        chat = "Good news, this is routine. Run `npx prisma migrate deploy` from the **frontend** folder, then you should see the migration applied."
        result, _ = self.run_cto(self.database_payload(chat), "Help me set up the Learning Loop table.")
        self.assertTrue(result.chat_reply.startswith(chat))  # nothing in it was altered
        self.assertIn("Ready for your approval", result.chat_reply)
        self.assertNotIn("Recommendation for CEO review", result.chat_reply)

    def test_scenario_1_the_model_is_given_the_zero_friction_rules_in_its_system_prompt(self):
        _, client = self.run_cto(self.database_payload("Run `npx prisma migrate deploy`."), "Help me set up the table.")
        system = client.calls[0]["system"]
        for needle in ("ZERO FRICTION", "A STANDARD PRISMA MIGRATION IS ROUTINE", "A READ-ONLY QUERY (a SELECT, a count) changes nothing",
                       "BELIEVE WHAT THE CEO SAYS THEY SAW", "Recommendation for CEO review:"):
            self.assertIn(needle, system, needle)
        self.assertNotIn("ESCALATE TO HUMAN:", system)  # it is only ever named inside a prohibition, never as a line to write

    def test_scenario_1_a_safe_read_only_query_in_the_reply_is_never_touched_or_flagged(self):
        chat = "Run this to see what is there: `SELECT * FROM \"ApprovedStrategies\";` and tell me what you see."
        payload = self.database_payload(chat)
        payload["authorization_request"] = None
        result, _ = self.run_cto(payload, "How do I check the table?")
        self.assertIn('`SELECT * FROM "ApprovedStrategies";`', result.chat_reply)
        self.assertNotIn("Ready for your approval", result.chat_reply)  # a query needs no approval, so no approval line is added
        self.assertNotIn("ESCALATE", result.chat_reply.upper())

    def test_scenario_1_a_deflection_that_carries_the_command_is_kept_and_logged_not_lost(self):
        chat = "Ask a developer to run `npx prisma migrate deploy` for you."
        result, _ = self.run_cto(self.database_payload(chat), "Set up the table.")
        self.assertIn("`npx prisma migrate deploy`", result.chat_reply)  # the command survives
        self.assertTrue(any("kept a deflecting sentence" in f for f in result.format_findings), result.format_findings)  # and it is reported

    # ---- Scenario 2: "make a toggle label slightly larger"

    def test_scenario_2_a_ui_change_never_says_waiting_for_your_decision_or_escalates(self):
        chat = ("ESCALATE TO HUMAN: This is a code change, so it needs your final approval. Waiting for your decision. "
                "The change is the class `text-lg` on the label.")
        result, _ = self.run_cto(self.toggle_payload(chat), "Please make the toggle label slightly larger.")
        for text in (result.reply, result.chat_reply):
            self.assertNotIn("waiting for your decision", text.lower())
            self.assertNotIn("ESCALATE TO HUMAN", text)

    def test_scenario_2_the_reply_uses_collaborative_language_and_carries_the_actual_solution(self):
        chat = "ESCALATE TO HUMAN: this is a code change. Waiting for your decision. The change is the class `text-lg` on the label."
        result, _ = self.run_cto(self.toggle_payload(chat), "Please make the toggle label slightly larger.")
        for text in (result.reply, result.chat_reply):
            self.assertIn("Recommendation for CEO review", text)
            self.assertIn("Ready for your approval", text)
        self.assertIn("`text-lg`", result.chat_reply)  # the exact change, in a code span
        plan = result.authorization_request["execution_plan"]
        self.assertIsNotNone(plan)  # the complete new file is in the plan, ready for approval
        self.assertIn('className="text-lg"', plan["files_to_update"][0]["content"])

    def test_scenario_2_the_note_under_the_request_is_written_by_code_in_collaborative_words(self):
        result, _ = self.run_cto(self.toggle_payload("A small change, ready to go."), "Make the label larger.")
        self.assertIn("**Ready for your approval:** this is ready whenever you are.", result.reply)
        self.assertIn("approving only opens a pull request that is not live until it is merged", result.reply)
        self.assertIn("**Ready for your approval:** this is ready whenever you are.", result.chat_reply)

    def test_the_old_label_is_still_scored_as_an_opening_escalation_so_scoring_is_unchanged(self):
        from app.governance.scoring import score_response

        low = "Reasoning: limited evidence, sources: general knowledge. Confidence: 40%.\n\nRecommendation for human review: gather data."
        for opening in ("Recommendation for CEO review: low confidence.", "ESCALATE TO HUMAN: low confidence."):
            self.assertEqual(score_response(opening + "\n" + low).score, 100, opening)
        self.assertLess(score_response(low).score, 100)  # without either opening line the low confidence is still penalised



if __name__ == "__main__":
    unittest.main()

