/**
 * Tests for the Mission Control chat's safe markdown reader (src/lib/mc-markdown.ts), no network and no browser:
 *     node --test scripts/mission_control_markdown.test.mjs
 *
 * The reader turns a reply into DATA (blocks and pieces). These tests check that it formats what the CTO's communication protocol uses
 * (### headings, numbered steps, **bold**, `code`), that everything else stays plain text, and, most important, that it can never
 * produce HTML, a link or an image, whatever the reply contains.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { MAX_MARKDOWN_CHARS, hasMarkdown, parseInline, parseMarkdown } from "../src/lib/mc-markdown.ts";

const text = (pieces) => pieces.map((p) => p.text).join("");
const kinds = (blocks) => blocks.map((b) => b.kind);

test("a heading, a numbered list and bold are recognised, in the order written", () => {
  const blocks = parseMarkdown("### Step 1\nOpen the **Authorizations** page.\n\n1. Click **Approve**.\n2. Wait for the pull request.\n\n### What to Look For\nA green badge.");
  assert.deepEqual(kinds(blocks), ["heading", "paragraph", "ordered", "heading", "paragraph"]);
  assert.equal(text(blocks[0].text), "Step 1");
  assert.deepEqual(blocks[1].lines[0].map((p) => p.kind), ["text", "bold", "text"]);
  assert.equal(blocks[2].items.length, 2);
  assert.equal(text(blocks[2].items[0]), "Click Approve.");
  assert.equal(blocks[2].items[0][1].kind, "bold");
});

test("heading levels 1 to 4 all become a heading and trailing hashes are dropped", () => {
  for (const line of ["# A", "## A", "### A", "#### A", "### A ###"]) {
    const [block] = parseMarkdown(line);
    assert.equal(block.kind, "heading", line);
    assert.equal(text(block.text), "A", line);
  }
  assert.equal(parseMarkdown("#hashtag")[0].kind, "paragraph"); // no space after the hash: not a heading
  assert.equal(parseMarkdown("####### seven")[0].kind, "paragraph");
});

test("numbered lists keep their start number and accept 1) as well as 1.", () => {
  const [list] = parseMarkdown("3. third\n4. fourth");
  assert.equal(list.start, 3);
  assert.equal(parseMarkdown("1) one\n2) two")[0].kind, "ordered");
});

test("bullets with - and * make a bullet list, and an indented line continues the item above", () => {
  const [bullets] = parseMarkdown("- first\n* second\n  carried on here\n- third");
  assert.equal(bullets.kind, "bullets");
  assert.equal(bullets.items.length, 3);
  assert.equal(text(bullets.items[1]), "second carried on here");
});

test("a list ends at the first line that is not part of it", () => {
  assert.deepEqual(kinds(parseMarkdown("1. a\n2. b\nThen a normal sentence.")), ["ordered", "paragraph"]);
  assert.deepEqual(kinds(parseMarkdown("- a\n1. b")), ["bullets", "ordered"]);
});

test("inline code and fenced code are shown as code, and nothing inside them is interpreted", () => {
  const inline = parseInline("Run `npm run **build**` now");
  assert.deepEqual(inline.map((p) => p.kind), ["text", "code", "text"]);
  assert.equal(inline[1].text, "npm run **build**");
  const [block] = parseMarkdown("```\n### not a heading\n1. not a list\n**not bold**\n```");
  assert.equal(block.kind, "code");
  assert.equal(block.text, "### not a heading\n1. not a list\n**not bold**");
});

test("an unclosed fence is shown as code to the end and nothing is lost", () => {
  const [block] = parseMarkdown("```\nline one\nline two");
  assert.equal(block.kind, "code");
  assert.equal(block.text, "line one\nline two");
});

test("an unclosed ** or ` stays literal text", () => {
  assert.deepEqual(parseInline("price is **not closed"), [{ kind: "text", text: "price is **not closed" }]);
  assert.deepEqual(parseInline("a ` lonely tick"), [{ kind: "text", text: "a ` lonely tick" }]);
  assert.deepEqual(parseInline("empty **** here").map((p) => p.kind), ["text"]);
});

// ------------------------------------------------------------------------------------------------------------------ safety

const HOSTILE = [
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "[click me](javascript:alert(1))",
  "![pixel](https://evil.example/p.png)",
  '<a href="https://evil.example">link</a>',
  "**<b onmouseover=alert(1)>bold</b>**",
  "`<iframe src=https://evil.example>`",
  "### <svg onload=alert(1)>",
  "1. <style>body{display:none}</style>",
];

// Walk the whole result and collect every kind of node it contains.
function shapeOf(blocks) {
  const seen = new Set();
  const walk = (value) => {
    if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === "object") {
      seen.add(value.kind);
      Object.entries(value).forEach(([k, v]) => {
        if (!["kind", "text", "start"].includes(k)) walk(v);
      });
    }
  };
  walk(blocks);
  return seen;
}

test("hostile text only ever produces the known safe kinds (no link, image or html kind exists)", () => {
  const allowed = new Set(["text", "bold", "code", "heading", "paragraph", "ordered", "bullets"]);
  for (const hostile of HOSTILE) {
    for (const source of [hostile, `### Title\n${hostile}\n\n1. ${hostile}\n- ${hostile}`]) {
      for (const kind of shapeOf(parseMarkdown(source))) assert.ok(allowed.has(kind), `${hostile} produced ${kind}`);
    }
  }
});

test("hostile text is kept as visible text, character for character, so React will escape it", () => {
  const [block] = parseMarkdown("<script>alert(1)</script>");
  assert.equal(block.kind, "paragraph");
  assert.equal(text(block.lines[0]), "<script>alert(1)</script>");
  assert.equal(text(parseInline("[click me](javascript:alert(1))")), "[click me](javascript:alert(1))");
  assert.equal(text(parseInline("![pixel](https://evil.example/p.png)")), "![pixel](https://evil.example/p.png)");
});

test("markdown links and images are NOT turned into links or images: the brackets stay as text", () => {
  for (const source of ["[a](https://example.com)", "![a](https://example.com/a.png)", "<https://example.com>"]) {
    const pieces = parseMarkdown(source)[0].lines[0];
    assert.deepEqual(pieces.map((p) => p.kind), ["text"], source);
    assert.equal(pieces[0].text, source);
  }
});

test("the reader adds no markup of its own: the output holds no html tag it could have produced", () => {
  const source = JSON.stringify(parseMarkdown("### A\n1. one **two** `three`\n- four"));
  for (const tag of ["<p>", "<ol>", "<ul>", "<li>", "<h4>", "<strong>", "<code>", "<pre>", "<a ", "<br"]) assert.ok(!source.includes(tag), tag);
});

test("the component never uses dangerouslySetInnerHTML, a link or an image", async () => {
  const { readFileSync } = await import("node:fs");
  const component = readFileSync(new URL("../src/components/mc/MarkdownText.tsx", import.meta.url), "utf8");
  const code = component.replace(/\/\*[\s\S]*?\*\//g, ""); // ignore the comment that explains why it is not used
  assert.ok(!/dangerouslySetInnerHTML|innerHTML|\bhref=|<img|<script/.test(code));
});

// ------------------------------------------------------------------------------------------------------------------ robustness

test("plain text with no markdown stays ordinary paragraphs, so the other executives' replies look as before", () => {
  const blocks = parseMarkdown("First line of a plain answer.\nSecond line.\n\nA new paragraph.");
  assert.deepEqual(kinds(blocks), ["paragraph", "paragraph"]);
  assert.equal(blocks[0].lines.length, 2);
  assert.equal(hasMarkdown("Just a sentence. 2 + 2 = 4."), false);
  assert.equal(hasMarkdown("### Step 1"), true);
  assert.equal(hasMarkdown("a **b** c"), true);
});

test("numbers and dashes inside a sentence are not mistaken for a list", () => {
  assert.deepEqual(kinds(parseMarkdown("We saw 3. rooms and a - dash in the text")), ["paragraph"]);
  assert.deepEqual(kinds(parseMarkdown("The 2026 plan - agreed.")), ["paragraph"]);
});

test("empty, whitespace and null-ish input give no blocks and never throw", () => {
  for (const value of ["", "   \n\n  ", null, undefined]) assert.deepEqual(parseMarkdown(value), []);
});

test("windows line endings are handled the same as unix ones", () => {
  assert.deepEqual(kinds(parseMarkdown("### A\r\n1. one\r\n2. two\r\n\r\ntext")), ["heading", "ordered", "paragraph"]);
});

test("a very long reply is cut at the limit with a note, and does not hang", () => {
  const started = Date.now();
  const blocks = parseMarkdown("word ".repeat(MAX_MARKDOWN_CHARS));
  assert.ok(Date.now() - started < 2000);
  assert.ok(text(blocks.at(-1).lines.at(-1)).includes("The rest of this reply is not shown here"));
});

test("thousands of list items with inline markers are processed quickly", () => {
  const source = Array.from({ length: 3000 }, (_, i) => `${i + 1}. **item ${i}** with \`code\``).join("\n");
  const started = Date.now();
  const [list] = parseMarkdown(source.slice(0, MAX_MARKDOWN_CHARS));
  assert.ok(Date.now() - started < 2000);
  assert.equal(list.kind, "ordered");
});

test("a realistic CTO reply is read into the structure the CEO should see", () => {
  const reply = [
    "Happy to help. I have prepared the plan for your final approval. Nothing goes live until you approve it and the change is merged.",
    "",
    "### Why This Matters",
    "We need to check this table because it is the foundation of the **Learning Loop**: it is where your approved decisions are remembered.",
    "",
    "### Step 1",
    "1. Open the **Authorizations** page from the left menu.",
    "2. Find the request marked **Pending**.",
    "3. Click **Approve** and confirm.",
    "",
    "### What to Look For",
    "- A message saying a pull request was opened.",
    "- The file `frontend/src/components/mc/Badge.tsx` in that pull request.",
    "",
    "**Warning:** nothing is live until the pull request is merged.",
  ].join("\n");
  const blocks = parseMarkdown(reply);
  assert.deepEqual(kinds(blocks), ["paragraph", "heading", "paragraph", "heading", "ordered", "heading", "bullets", "paragraph"]);
  assert.equal(blocks[4].items.length, 3);
  assert.equal(blocks[6].items[1][1].kind, "code");
  assert.equal(blocks[7].lines[0][0].kind, "bold");
});

test("the answer view draws EVERY executive's reply through the reader (communication parity), not just the CTO's", async () => {
  const { readFileSync } = await import("node:fs");
  const view = readFileSync(new URL("../src/components/mc/AgentAnswerView.tsx", import.meta.url), "utf8");
  assert.ok(view.includes("const formatted = AGENT_KEYS.includes(agent as AgentKey);"));
  assert.ok(!view.includes('agent === "cto"'));
  assert.ok(view.includes("<MarkdownText text={chat ?? body} />"));
  assert.ok(view.includes('whitespace-pre-wrap text-sm leading-relaxed text-slate-100'));
});

// ---- Communication parity: the CAO, COO and CMO now write the same structured reply as the CTO, so each is checked with a realistic reply of
// its own shape AND with hostile input dressed in that agent's own voice. The reader is one safe function for all four; these prove it.
const REPLIES = {
  cao: [
    "### What I Am Seeing",
    "Fish gulping at the surface at dawn is the first sign that **dissolved oxygen** is falling.",
    "",
    "### Why This Matters",
    "Low oxygen stresses the fish before it kills them, so acting early protects the whole crop.",
    "",
    "### The Protocol",
    "1. Measure oxygen at **05:30** and note the reading.",
    "2. If it is under `4 mg/L`, run every aerator and **stop feeding** until it recovers.",
    "3. Re-measure after `30 minutes`.",
    "",
    "### My Recommendation",
    "Option 1: aerate now. Option 2: partial water exchange. I recommend option 1.",
  ].join("\n"),
  coo: [
    "### The Constraint",
    "Only **two** pond staff are on shift and the feed truck arrives on `Tuesday`.",
    "",
    "### The Plan",
    "1. Monday: count feed stock and move the order to **Tuesday morning**.",
    "2. Tuesday: both staff unload, then split ponds A and B.",
    "- Cost: about `USD 480` in overtime (an illustrative assumption).",
    "",
    "### My Recommendation",
    "Re-sequence the delivery. It costs less than a third crew.",
  ].join("\n"),
  cmo: [
    "### Who It Is For",
    "Farm owners deciding whether to try a new water-treatment product.",
    "",
    "### The Message",
    "Cleaner water with fewer water changes.",
    "",
    "### The Copy",
    "**Fewer water changes. Calmer ponds.**",
    "```",
    "Subject: Less water to change, more time to farm",
    "```",
    "",
    "### What to Check Before It Goes Out",
    "- Every claim needs a source. **Do not publish** until that is done.",
  ].join("\n"),
};

for (const [agent, reply] of Object.entries(REPLIES)) {
  test(`a realistic ${agent.toUpperCase()} reply is read into the structure its protocol asks for`, () => {
    const blocks = parseMarkdown(reply);
    assert.ok(blocks.filter((b) => b.kind === "heading").length >= 3);
    assert.ok(blocks.some((b) => b.kind === "ordered" || b.kind === "bullets"));
    assert.ok(blocks.some((b) => b.kind === "paragraph" && b.lines.some((l) => l.some((p) => p.kind === "bold"))) || blocks.some((b) => b.kind === "ordered" && b.items.some((i) => i.some((p) => p.kind === "bold"))));
    assert.ok(hasMarkdown(reply));
    assert.equal(blocks[0].kind, "heading"); // each agent leads with its own first heading (the risk, the constraint, the audience)
  });
}

test("the CAO's exact figures in backticks are shown as code and keep their units", () => {
  const blocks = parseMarkdown(REPLIES.cao);
  const list = blocks.find((b) => b.kind === "ordered");
  assert.equal(list.items.length, 3);
  assert.equal(list.items[1].find((p) => p.kind === "code").text, "4 mg/L");
  assert.equal(list.items[2].find((p) => p.kind === "code").text, "30 minutes");
});

test("the COO's costed plan keeps its numbers, its bullet and its bold constraint", () => {
  const blocks = parseMarkdown(REPLIES.coo);
  assert.equal(text(blocks[1].lines[0]), "Only two pond staff are on shift and the feed truck arrives on Tuesday.");
  assert.ok(blocks.some((b) => b.kind === "bullets" && text(b.items[0]).includes("USD 480")));
});

test("the CMO's copy block is shown as code and nothing inside it is interpreted", () => {
  const blocks = parseMarkdown(REPLIES.cmo);
  const code = blocks.find((b) => b.kind === "code");
  assert.equal(code.text, "Subject: Less water to change, more time to farm");
  const [hostile] = parseMarkdown("```\n**not bold** <b>x</b> [a](javascript:alert(1))\n```");
  assert.equal(hostile.kind, "code");
  assert.equal(hostile.text, "**not bold** <b>x</b> [a](javascript:alert(1))");
});

const AGENT_HOSTILE = {
  cao: "### The Protocol\n1. Dose <script>alert('cao')</script> at `5 mg/L`.\n2. [fish vet](javascript:alert(1)) ![x](http://evil/x.png)\n<img src=x onerror=alert(1)>",
  coo: "### The Plan\n- <iframe src=\"//evil\"></iframe> cost **USD 1,200**\n1. [approve](data:text/html,<script>alert(1)</script>)\n<a href=\"javascript:alert(1)\">budget</a>",
  cmo: "### The Copy\n**Buy now** <svg onload=alert(1)> [click](vbscript:msgbox(1))\n<style>body{display:none}</style>\n![pixel](https://tracker.example/p.gif)",
};

for (const [agent, reply] of Object.entries(AGENT_HOSTILE)) {
  test(`hostile ${agent.toUpperCase()}-style text never becomes HTML, a link or an image, and stays visible as text`, () => {
    const blocks = parseMarkdown(reply);
    const kindsSeen = new Set();
    const collect = (parts) => parts.forEach((p) => kindsSeen.add(p.kind));
    for (const b of blocks) {
      if (b.kind === "heading") collect(b.text);
      else if (b.kind === "paragraph") b.lines.forEach(collect);
      else if (b.kind === "ordered" || b.kind === "bullets") b.items.forEach(collect);
    }
    assert.ok([...kindsSeen].every((k) => ["text", "bold", "code"].includes(k)), [...kindsSeen].join());
    const shown = blocks.map((b) => (b.kind === "code" ? b.text : b.kind === "heading" ? text(b.text) : b.kind === "paragraph" ? b.lines.map(text).join("\n") : b.items.map(text).join("\n"))).join("\n");
    for (const needle of ["<script>", "javascript:", "<img", "<iframe", "<svg", "<style>", "vbscript:", "![", "data:text/html"]) {
      if (reply.includes(needle)) assert.ok(shown.includes(needle), `${needle} was lost or turned into something else`);
    }
  });
}

test("a reply from any of the four with no markdown at all still comes out as ordinary paragraphs", () => {
  for (const plain of ["Aerate now and recheck in half an hour.", "Move the delivery to Tuesday.", "Here is the post, ready to use."]) {
    assert.equal(hasMarkdown(plain), false);
    assert.deepEqual(kinds(parseMarkdown(plain)), ["paragraph"]);
  }
});

test("an old plain reply with a few line breaks keeps its lines (nothing is merged or lost)", () => {
  const [para] = parseMarkdown("Line one.\nLine two.\nLine three.");
  assert.equal(para.kind, "paragraph");
  assert.equal(para.lines.length, 3);
});

test("a structured reply of the maximum length the backend allows (5000 characters) is processed whole and quickly", () => {
  const long = "### Step\n" + "1. **Do** the thing `now`.\n".repeat(180);
  assert.ok(long.length > 4500 && long.length <= MAX_MARKDOWN_CHARS);
  const started = Date.now();
  const blocks = parseMarkdown(long);
  assert.ok(Date.now() - started < 1000);
  assert.equal(blocks[1].items.length, 180);
});

test("the answer view no longer special-cases any agent and keeps every safety property of the renderer", async () => {
  const { readFileSync } = await import("node:fs");
  const view = readFileSync(new URL("../src/components/mc/AgentAnswerView.tsx", import.meta.url), "utf8");
  const code = view.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.ok(!/dangerouslySetInnerHTML|innerHTML|\bhref=\{?["'`]?(?!#)/.test(code.replace(/AuthorizationDetail[^\n]*/g, "")));
  assert.ok(code.includes("<MarkdownText text={chat ?? body} />"));
  assert.ok(code.includes('<MarkdownText text={body} className="text-slate-200" />'));
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
  assert.ok(!deps.some((d) => /markdown|marked|remark|rehype|mdx|sanitize|dompurify|showdown/i.test(d)), "no markdown dependency may be added");
});
