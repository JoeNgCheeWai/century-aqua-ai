/**
 * Tests for the Mission Control conversation memory helpers (src/lib/mc-memory.ts), no network and no browser:
 *     node --test scripts/mission_control_memory.test.mjs
 * (Node 22.18+ runs the TypeScript file directly.)
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  BOARDROOM_MAX_CHARS,
  MAX_HISTORY_MESSAGES,
  MAX_STORED_TURNS,
  boardroomQuery,
  capTurns,
  historyFromTurns,
  parseStored,
} from "../src/lib/mc-memory.ts";

const user = (text) => ({ kind: "user", text });
const agent = (response) => ({ kind: "agent", answer: { response } });

test("history is the user's questions and the agents' full answers, in order", () => {
  const turns = [user("I run a 40 ha farm."), agent("Noted: 40 ha."), user("What size is my farm?")];
  assert.deepEqual(historyFromTurns(turns), [
    { role: "user", content: "I run a 40 ha farm." },
    { role: "assistant", content: "Noted: 40 ha." },
    { role: "user", content: "What size is my farm?" },
  ]);
});

test("error turns are not part of the conversation", () => {
  const turns = [user("a"), { kind: "error", text: "The backend refused the key." }, user("b")];
  assert.deepEqual(historyFromTurns(turns).map((m) => m.content), ["a", "b"]);
});

test("only the newest 20 messages are sent, and the list never starts with an assistant message", () => {
  const turns = [];
  for (let i = 0; i < 15; i++) turns.push(user(`q${i}`), agent(`a${i}`)); // 30 messages
  const history = historyFromTurns(turns);
  assert.ok(history.length <= MAX_HISTORY_MESSAGES);
  assert.equal(history[0].role, "user");
  assert.equal(history.at(-1).content, "a14");
  const odd = historyFromTurns([agent("orphan"), user("q")]);
  assert.deepEqual(odd, [{ role: "user", content: "q" }]);
});

test("a malformed agent turn is skipped, never thrown on", () => {
  assert.deepEqual(historyFromTurns([user("q"), { kind: "agent" }, { kind: "agent", answer: {} }, { kind: "agent", answer: { response: 5 } }]), [
    { role: "user", content: "q" },
  ]);
});

test("stored text that is not a list is treated as an empty conversation", () => {
  for (const raw of [null, undefined, "", "not json", "{}", '"x"', "5", "null"]) assert.deepEqual(parseStored(raw), [], String(raw));
  assert.deepEqual(parseStored('[{"kind":"user","text":"hi"}]'), [{ kind: "user", text: "hi" }]);
});

test("the stored conversation keeps the newest turns", () => {
  const turns = Array.from({ length: MAX_STORED_TURNS + 10 }, (_, i) => user(`t${i}`));
  const capped = capTurns(turns);
  assert.equal(capped.length, MAX_STORED_TURNS);
  assert.equal(capped.at(-1).text, `t${MAX_STORED_TURNS + 9}`);
  assert.equal(capTurns([user("x")]).length, 1);
});

const entry = (n, options = []) => ({ question: `Question number ${n}`, status: "RESOLVED", options, ceoQuestions: [] });

test("a Boardroom question with no earlier discussion is sent unchanged", () => {
  assert.equal(boardroomQuery("  Should we expand?  ", []), "Should we expand?");
});

test("a Boardroom follow-up carries the earlier outcome and ends with the new question", () => {
  const q = boardroomQuery("What if the budget is halved?", [entry(1, ["A: Pilot first - start with one pond."])]);
  assert.match(q, /Earlier in this conversation/);
  assert.match(q, /Question 1: Question number 1/);
  assert.match(q, /A: Pilot first - start with one pond\./);
  assert.ok(q.endsWith("New question: What if the budget is halved?"));
});

test("a follow-up after a session that asked the CEO a question includes what was asked", () => {
  const q = boardroomQuery("Budget is 50k.", [{ question: "Launch?", status: "NEEDS_CEO_INPUT", options: [], ceoQuestions: ["What is the budget cap?"] }]);
  assert.match(q, /needs ceo input/);
  assert.match(q, /The CEO was asked: What is the budget cap\?/);
});

test("the Boardroom query never exceeds the backend limit, drops the OLDEST discussion first and never cuts the new question", () => {
  const big = Array.from({ length: 30 }, (_, i) => entry(i + 1, ["A: " + "word ".repeat(80), "B: " + "word ".repeat(80)]));
  const q = boardroomQuery("The very newest question?", big);
  assert.ok(q.length <= BOARDROOM_MAX_CHARS, String(q.length));
  assert.ok(q.endsWith("New question: The very newest question?"));
  assert.match(q, /Question 30:/);
  assert.doesNotMatch(q, /Question 1:/);
});

test("a very long new question is not buried: with no room for context it is sent alone", () => {
  const long = "x".repeat(3900);
  const q = boardroomQuery(long, [entry(1, ["A: something"])]);
  assert.ok(q.length <= BOARDROOM_MAX_CHARS);
  assert.ok(q.includes("x".repeat(3000)));
});
