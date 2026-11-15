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

test("the answer view draws the CTO's reply through the reader and every other executive as plain text", async () => {
  const { readFileSync } = await import("node:fs");
  const view = readFileSync(new URL("../src/components/mc/AgentAnswerView.tsx", import.meta.url), "utf8");
  assert.ok(view.includes('const formatted = agent === "cto";'));
  assert.ok(view.includes("<MarkdownText text={chat ?? body} />"));
  assert.ok(view.includes('whitespace-pre-wrap text-sm leading-relaxed text-slate-100'));
});
