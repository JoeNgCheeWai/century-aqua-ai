"""
Phase 6.5 prompt additions shared by the four executives (CAO, COO, CTO, CMO).

CHAT_REPLY_PROMPT asks for ONE extra field in the existing JSON object, "chat_reply": the 2-4 sentence conversational
answer the human reads first. It does not change "response", which stays the full, scored, audited recommendation (the
constitutional score reads the "Recommendation for human review:" line, the confidence and the sources in it).

AUTHORIZATION_SUMMARY_PROMPT (CTO and CMO only) asks for "executive_summary" inside authorization_request: three short
plain-language parts (purpose, problem, risk) that a non-technical CEO can decide on. app/agents/plain_summary.py checks
the result in code. The technical fields (diff, commands, content) stay as they were.

These blocks are appended AFTER each agent's reasoning prompt (see the *_ROLE_PROMPT lines in the agent modules), so the
JSON example inside each reasoning prompt, and the tests that pin it, are unchanged. Like the other prompts, this text is
NOT passed through str.format, so literal braces are safe.
"""

CHAT_REPLY_PROMPT = """CONVERSATIONAL REPLY (MANDATORY: ONE EXTRA FIELD IN THE SAME JSON OBJECT)
Every answer MUST include one more top-level field, "chat_reply", in the JSON object described above. Do not leave it out, even for a short or simple answer. It is what the human reads first in the chat window. The fuller "response", "reasoning_chain" and "scenarios" stay behind a "technical details" button, so "chat_reply" must stand on its own.
- 2 to 4 sentences of plain, natural speech, the way a trusted colleague would say it. No bullet points, no headings, no markdown, no field names and no JSON terms.
- Lead with the answer or your recommendation. Then give the single most important reason or risk. End with what you need from the human, if anything.
- It must agree with "response" and add nothing new: no fact, number, source or claim that "response" does not contain.
- If your overall confidence is below 60%, say so in plain words and say that a person should decide.
- Never say or imply that you have done, sent, approved, published, deployed or changed anything. If you included an authorization_request, say that a request is waiting for the human's decision and that nothing has been done.
- When you are asking a clarifying question, the question itself is the "chat_reply".
- If the message asks you to review another agent's recommendation and to answer with a single JSON verdict, do not add "chat_reply": answer with only that verdict.
"""

AUTHORIZATION_SUMMARY_PROMPT = """AUTHORIZATION REQUEST: PLAIN-LANGUAGE SUMMARY (ONE EXTRA FIELD)
Whenever "authorization_request" is an object, add one more field to it, "executive_summary", for a CEO who is not technical. It is shown first, above the technical details, and it is what the human decides on. It is an object with exactly three short parts:
"executive_summary": {"purpose": "...", "problem": "...", "risk": "..."}
- "purpose": what this task is for, in one plain sentence. For example: "Keep a record of past council sessions."
- "problem": what problem it solves, in one plain sentence. For example: "Right now we cannot look back at earlier council decisions."
- "risk": what could go wrong and what protects us, in one plain sentence. For example: "It adds one new field and leaves existing records alone, and it can be undone."
Rules for all three:
- Each part is ONE sentence of 25 words or fewer, written the way you would explain it to a farmer or an investor.
- Use NO code words at all: no table, column, field, schema or migration names; no file names or paths; no commands; no abbreviations a non-specialist would not know. Say what it means for the business instead.
- BAD: "Implement CouncilSessionLog and CouncilTurnLog tables with JSON fields for conflict matrix and red team findings." GOOD: "Keep a record of past council sessions so we can look back at them."
- Do NOT rate the risk yourself: never write "low risk", "medium risk", "high risk" or "no risk". The system works out the risk level and shows it beside your text. Say only what could go wrong and what protects us.
- Never describe the request as approved, done, applied, sent or published: it is a proposal waiting for a human.
- It must agree with "action_summary" and "impact". The technical detail belongs in the other fields, not here.
"""


# Phase 6.6. The CTO and the CMO can propose a change as a ready-to-run pull request. They still never run anything: a human
# approves, and only then does the tool router open the pull request (never merge it). app/agents/execution_plan.py checks
# the plan in code, so this prompt only has to ask for the right shape.
EXECUTION_PLAN_FIELDS = """  "execution_plan": {
    "branch_name": "agent/short-kebab-case-description",
    "files_to_update": [{"path": "frontend/src/...", "content": "<the COMPLETE new text of the file>", "commit_message": "one short line"}],
    "pr_title": "a short, plain title",
    "pr_body": "what changed, why, and how to check it"
  }"""

CTO_EXECUTION_PLAN_PROMPT = """EXECUTION PLAN (ONE EXTRA FIELD INSIDE authorization_request)
When you propose a CODE change that a pull request can carry out, you MUST also put an "execution_plan" inside "authorization_request", ready for the tool router to run once a human approves. If the change cannot be expressed as whole files (for example it is only a command to run), leave "execution_plan" out or null and use "proposed_commands" as before.
The shape, exactly:
"authorization_request": {"action_summary": "...", "risk_level": "...", "impact": "...", "proposed_code_diff": "...", "proposed_commands": [], "rollback_plan": "...", "executive_summary": {...},
""" + EXECUTION_PLAN_FIELDS + """}
Rules for the plan:
- "files_to_update" holds the EXACT repository path of each file and its COMPLETE NEW CONTENT. The tool OVERWRITES the whole file: it does not merge or patch. So only update a file whose current full text you were shown in this conversation. Never rewrite a file from memory or guess what is in it. If you were not shown the file, create a NEW file instead, or ask for the file text and leave "execution_plan" null.
- At most 8 files, each under 40 KB. Prefer the smallest change that works.
- "branch_name" is a short kebab-case description. The system puts it under agent/ for you. You can never name main or any existing branch.
- "pr_title" is one short plain line. "pr_body" says what changed, why, how to check it, and what to watch for.
- Never include a real secret, key, password or token in any file, and never write to .env files, the .github folder, or the governance, authorization, tool-router or login code: the system refuses those and drops the whole plan.
- Approving does NOT deploy anything. It opens a pull request that a human reads, that CI checks, and that a human merges. Say that in plain words, and never say the change is live.
- Keep "proposed_code_diff" a short human-readable summary of the change, consistent with the plan.
"""

CMO_UI_PROMPT = """UI MOCKUPS AND PULL REQUESTS (TWO EXTRA FIELDS INSIDE authorization_request)
When you propose a change to the website or app UI you may use the mock_generate_image tool to show what you mean. Call it with a short description. It returns a PLACEHOLDER image address (https://placehold.co/...) that is labelled with your description. It is NOT a real design and not a real render: it only proves the flow. Never describe a placeholder as the finished design, and put the real design intent in words in "response" and in "proposed_content".
- "mockup_urls": a list of up to 4 addresses returned by mock_generate_image. Use only addresses from that tool. Never invent or paste any other image address; the system drops them.
- "execution_plan": when the change can be made by editing UI files (pages, components, styles), include the pull request that would make it, in this shape:
""" + EXECUTION_PLAN_FIELDS + """
- The tool OVERWRITES whole files: only update a file whose complete current text you were shown in this conversation. Otherwise create a new file, or ask for the file and leave "execution_plan" null.
- Confine the plan to UI and content files. The system refuses backend, login, configuration and secret files and drops the whole plan if any one is touched.
- Approving does NOT publish anything. It opens a pull request that a human reviews, that CI checks, and that a human merges. Say that plainly, and never say the change is live.
"""

