/**
 * Conversation memory for Mission Control chats (pure functions: no React, no network, so plain node can test them).
 *
 * WHY THIS EXISTS. The backend agents are stateless: they only know what a request carries (`history`). The chat page sent
 * the history, but kept the conversation in component state, so it was lost on a tab switch, a navigation or a refresh.
 * These helpers keep it in sessionStorage (per browser tab, cleared when the tab closes) and rebuild the request history
 * from it. The backend's AgentState is NOT used for this: it is one shared row per agent and is deliberately never fed
 * back to the model, because that would leak one person's conversation into another's.
 *
 * Nothing here is secret: it is the human's own conversation, in their own browser tab.
 */

export const MEMORY_PREFIX = "mc-chat:";
/** The most turns kept per conversation (a question and its answer are two turns). Older ones are dropped first. */
export const MAX_STORED_TURNS = 40;
/** The backend keeps the last 20 messages and the Mission Control route accepts 20, so send no more than that. */
export const MAX_HISTORY_MESSAGES = 20;

export interface Message {
  role: "user" | "assistant";
  content: string;
}

/** Just enough of a chat turn to rebuild the history. Agent turns carry the full `response`. */
export type MemoryTurn = { kind: "user"; text: string } | { kind: "agent"; answer: { response: string } } | { kind: "error"; text: string };

/** sessionStorage can be unavailable (private mode, blocked storage): memory then lasts only while the page stays open. */
export function readStore(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStore(key: string, value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch {
    // storage full or blocked: the conversation simply is not remembered across a refresh
  }
}

/** Parse what sessionStorage returned. Anything that is not a list (corrupt, hand-edited, from an older shape) is empty. */
export function parseStored<T>(raw: string | null | undefined): T[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? (value as T[]) : [];
  } catch {
    return [];
  }
}

/** Keep the newest turns. */
export function capTurns<T>(turns: T[], max: number = MAX_STORED_TURNS): T[] {
  return turns.length > max ? turns.slice(turns.length - max) : turns;
}

/**
 * The messages to send with the next question: the user's questions and each agent answer's full text, in order. Error
 * turns are skipped (they are not part of the conversation). The newest MAX_HISTORY_MESSAGES are kept, and the list never
 * starts with an assistant message (the API wants the first message from the user).
 */
export function historyFromTurns(turns: MemoryTurn[]): Message[] {
  const messages: Message[] = [];
  for (const turn of turns) {
    if (turn.kind === "user") messages.push({ role: "user", content: turn.text });
    else if (turn.kind === "agent" && typeof turn.answer?.response === "string") messages.push({ role: "assistant", content: turn.answer.response });
  }
  let kept = messages.slice(-MAX_HISTORY_MESSAGES);
  while (kept.length > 0 && kept[0].role === "assistant") kept = kept.slice(1);
  return kept;
}

/** One finished Boardroom discussion, reduced to what a follow-up needs. */
export interface BoardroomEntry {
  question: string;
  status: string;
  /** "A: title - summary" lines for the options the Boardroom produced (empty if it produced none). */
  options: string[];
  /** What the CEO was asked, when the Boardroom stopped to ask. */
  ceoQuestions: string[];
}

export const BOARDROOM_MAX_CHARS = 4000;

function clip(text: string, max: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length <= max ? clean : `${clean.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/**
 * The query for a Boardroom FOLLOW-UP. The Boardroom has no memory of its own (every session starts clean, by design), so a
 * follow-up carries the earlier discussion inside the question text, newest first when space is short, within the backend's
 * 4,000 character limit. The new question always comes last and is never cut. With no earlier discussion, the question is
 * returned unchanged.
 */
export function boardroomQuery(question: string, earlier: BoardroomEntry[], max: number = BOARDROOM_MAX_CHARS): string {
  const q = question.trim();
  if (earlier.length === 0) return q;

  const head = "Earlier in this conversation (for context only; the new question is at the end):";
  const tail = `\n\nNew question: ${q}`;
  const budget = max - head.length - tail.length;
  if (budget < 200) return q.slice(0, max); // the new question alone nearly fills the limit: send it as it is

  const blocks: string[] = [];
  let used = 0;
  for (let i = earlier.length - 1; i >= 0; i--) {
    const e = earlier[i];
    const lines = [`Question ${i + 1}: ${clip(e.question, 300)}`, `Outcome: ${e.status.replace(/_/g, " ").toLowerCase()}`];
    for (const option of e.options) lines.push(`- ${clip(option, 260)}`);
    for (const ask of e.ceoQuestions) lines.push(`- The CEO was asked: ${clip(ask, 200)}`);
    const block = `\n\n${lines.join("\n")}`;
    if (used + block.length > budget) break; // older discussions are dropped first
    blocks.unshift(block);
    used += block.length;
  }
  return blocks.length === 0 ? q : `${head}${blocks.join("")}${tail}`;
}
