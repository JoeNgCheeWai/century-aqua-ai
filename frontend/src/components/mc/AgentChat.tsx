"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import AgentAnswerView from "@/components/mc/AgentAnswerView";
import { Card, ErrorNote, Spinner, buttonPrimary, inputClass } from "@/components/mc/ui";
import { McError, mc } from "@/lib/mc-client";
import { MEMORY_PREFIX, capTurns, historyFromTurns, parseStored, readStore, writeStore } from "@/lib/mc-memory";
import { AGENT_INFO, DEFAULT_TIER, MODEL_TIERS, type AgentAnswer, type AgentKey, type ModelTier } from "@/lib/mc-types";

/**
 * A hint, never an automatic switch: a question that looks strategic gets a one-line suggestion to try Opus. It is a
 * plain keyword check (no model call, so it costs nothing), it will miss some and flag some that are not, and the
 * human decides.
 */
const STRATEGIC_HINT =
  /\b(should we|strategy|strategic|roadmap|invest|investment|acquir\w*|expan\w*|launch|enter (a |the )?market|trade-?offs?|risk assessment|business case|scenario|feasibility|go\/no-go|prioriti[sz]e|long[- ]term|multi-?year)\b/i;

/** The small buttons under each of the user's own messages (Copy, Edit, Delete). */
const smallButton = "rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:text-white disabled:opacity-40";

/** The Stop button replaces Send while an answer is being written. */
const stopButton =
  "rounded-lg border border-red-400/50 bg-red-500/20 px-4 py-2 text-sm font-medium text-red-100 transition hover:bg-red-500/30";

type Turn =
  | { kind: "user"; text: string }
  | { kind: "agent"; answer: AgentAnswer }
  | { kind: "error"; text: string };

/**
 * Ask one executive. The agent endpoints are synchronous (one long model call), so this waits and shows a timer.
 *
 * MEMORY. The agents are stateless: they know only the `history` a request carries. Every question therefore sends the
 * conversation so far, and the conversation is kept in sessionStorage (this browser tab) so it survives switching to
 * another agent and back, and a refresh. "New conversation" clears it. The backend's AgentState is not used for this (it is
 * one shared row per agent and is never fed back to the model, so it cannot leak one conversation into another).
 *
 * MESSAGE CONTROLS. Stop cancels the browser's wait for an answer and puts the question back in the box (so the saved
 * conversation never holds two user messages in a row). Copy, Edit and Delete work on the user's own messages and change
 * only this tab's saved copy of the chat; the audit log on the server is never touched. Edit and Delete are switched off
 * while an answer is being written.
 */
export default function AgentChat({ agent }: { agent: AgentKey }) {
  const storeKey = `${MEMORY_PREFIX}${agent}`;
  const [turns, setTurns] = useState<Turn[]>([]);
  // Storage is read after mount (it does not exist on the server); until then nothing is written, so an empty first render
  // can never overwrite a saved conversation.
  const [loaded, setLoaded] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [tier, setTier] = useState<ModelTier>(DEFAULT_TIER);
  const [copyState, setCopyState] = useState<{ index: number; status: "copied" | "failed" } | null>(null);
  const epoch = useRef<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // The request in flight (if any) and whether the user pressed Stop for it.
  const controllerRef = useRef<AbortController | null>(null);
  const stoppedRef = useRef(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The state is set in a microtask, not synchronously in the effect body (React's lint rule against cascading renders, and
  // the same approach as useLoad in lib/mc-client.ts).
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      const saved = parseStored<Turn>(readStore(storeKey));
      const rawEpoch = readStore(`${storeKey}:epoch`);
      const savedEpoch = Number(rawEpoch);
      if (saved.length > 0) setTurns(saved);
      if (rawEpoch !== null && Number.isInteger(savedEpoch) && savedEpoch >= 0) epoch.current = savedEpoch;
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, [storeKey]);

  useEffect(() => {
    if (!loaded) return;
    writeStore(storeKey, turns.length ? JSON.stringify(capTurns(turns)) : null);
  }, [turns, loaded, storeKey]);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Date.now() - started), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  // A block body on purpose: React treats whatever an effect RETURNS as its cleanup function, so an arrow with an
  // expression body would hand React the value of scrollIntoView() and crash on the next run ("destroy is not a function").
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy]);

  // Clear the "Copied" timer if the chat goes away.
  useEffect(() => {
    return () => {
      if (copyTimer.current) clearTimeout(copyTimer.current);
    };
  }, []);

  async function send(event: FormEvent) {
    event.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    // The conversation so far, so a follow-up has context: the user's questions and each answer's full text.
    const history = historyFromTurns(turns);
    // Where this question sits, so Stop can take it back out of the conversation.
    const userIndex = turns.length;
    const controller = new AbortController();
    controllerRef.current = controller;
    stoppedRef.current = false;
    setTurns((prev) => [...prev, { kind: "user", text: message }]);
    setText("");
    setBusy(true);
    try {
      const answer = await mc<AgentAnswer>(`/agents/${agent}`, {
        method: "POST",
        body: { message, history, model: tier, ...(epoch.current !== null ? { reset_epoch: epoch.current } : {}) },
        signal: controller.signal,
      });
      epoch.current = answer.reset_epoch;
      writeStore(`${storeKey}:epoch`, answer.reset_epoch === null ? null : String(answer.reset_epoch));
      setTurns((prev) => [...prev, { kind: "agent", answer }]);
    } catch (e) {
      if (stoppedRef.current || (e as Error).name === "AbortError") {
        // Stopped by the user: take the question back out of the conversation and return it to the box. If a new draft was
        // typed meanwhile, the stopped question goes in front of it, so nothing is lost.
        setTurns((prev) => prev.slice(0, userIndex));
        setText((prev) => (prev.trim() ? `${message}\n\n${prev}`.slice(0, 8000) : message));
      } else if (!(e instanceof McError && e.status === 401)) {
        setTurns((prev) => [...prev, { kind: "error", text: (e as Error).message }]);
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setBusy(false);
    }
  }

  /** Cancel the wait for the current answer. The server may still finish its work; the browser just stops waiting. */
  function stop() {
    if (!busy) return;
    stoppedRef.current = true;
    controllerRef.current?.abort();
  }

  function flashCopy(index: number, status: "copied" | "failed") {
    setCopyState({ index, status });
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopyState(null), 1500);
  }

  async function copyMessage(index: number, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      flashCopy(index, "copied");
    } catch {
      // no clipboard access (blocked by the browser, or not a secure page)
      flashCopy(index, "failed");
    }
  }

  /** Put a message back in the box and drop it, its answer and everything after it, so the history stays in order. */
  function editTurn(index: number) {
    if (busy) return;
    const target = turns[index];
    if (!target || target.kind !== "user") return;
    const later = turns.length - index - 1;
    const hasDraft = text.trim().length > 0;
    if (later > 0 || hasDraft) {
      const parts: string[] = [];
      if (later > 0) parts.push("remove its answer and everything after it");
      if (hasDraft) parts.push("replace the text you have typed in the box");
      if (!window.confirm(`Editing will ${parts.join(" and ")}. Continue?`)) return;
    }
    setTurns(turns.slice(0, index));
    setText(target.text);
    window.setTimeout(() => textareaRef.current?.focus(), 0);
  }

  /** Remove a message and the answer (or error) that followed it. The rest of the conversation stays in order. */
  function deleteTurn(index: number) {
    if (busy) return;
    if (turns[index]?.kind !== "user") return;
    if (!window.confirm("Delete this message and the answer to it? This cannot be undone.")) return;
    let end = index + 1;
    while (end < turns.length && turns[end].kind !== "user") end += 1;
    setTurns([...turns.slice(0, index), ...turns.slice(end)]);
  }

  function newConversation() {
    if (busy) return;
    setTurns([]);
    epoch.current = null;
    writeStore(storeKey, null);
    writeStore(`${storeKey}:epoch`, null);
  }

  const info = AGENT_INFO[agent];
  const remembered = turns.filter((t) => t.kind !== "error").length;
  const suggestOpus = tier !== "opus" && !busy && STRATEGIC_HINT.test(text);
  return (
    <>
      <div className="space-y-4" aria-live="polite">
        {turns.length === 0 && (
          <Card>
            <p className="text-sm text-slate-300">
              Ask {info.name} ({info.role}) a question.
              {agent === "cto" || agent === "cmo"
                ? " A request to change something returns an authorization request that waits for a human."
                : " Every answer is recorded in the audit log."}{" "}
              You get a short answer first; use “Show internal reasoning &amp; sources” for the full reasoning.
            </p>
          </Card>
        )}
        {turns.map((t, i) =>
          t.kind === "user" ? (
            <div key={i} className="ml-auto max-w-2xl">
              <div className="rounded-xl border border-blue-400/30 bg-blue-500/10 px-4 py-2 text-sm text-blue-50">{t.text}</div>
              <div className="mt-1 flex justify-end gap-2">
                <button type="button" onClick={() => copyMessage(i, t.text)} className={smallButton} aria-label="Copy this message">
                  {copyState?.index === i ? (copyState.status === "copied" ? "Copied" : "Copy failed") : "Copy"}
                </button>
                <button type="button" onClick={() => editTurn(i)} disabled={busy} className={smallButton} aria-label="Edit this message">
                  Edit
                </button>
                <button type="button" onClick={() => deleteTurn(i)} disabled={busy} className={smallButton} aria-label="Delete this message and its answer">
                  Delete
                </button>
              </div>
            </div>
          ) : t.kind === "error" ? (
            <ErrorNote key={i} message={t.text} />
          ) : (
            <AgentAnswerView key={i} answer={t.answer} agent={agent} />
          ),
        )}
        {busy && (
          <Card>
            <Spinner label={`${info.name} is thinking… ${Math.round(elapsed / 1000)}s`} />
            <p className="mt-1 text-xs text-slate-500">
              Executive answers are long model calls and can take a minute or more. Press Stop (or Esc) to cancel and get your question back.
            </p>
          </Card>
        )}
        <div ref={endRef} />
      </div>

      <form onSubmit={send} className="sticky bottom-0 mt-4 space-y-2 bg-slate-950/80 py-3 backdrop-blur">
        <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Model">
          {MODEL_TIERS.map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={tier === m.id}
              disabled={busy}
              onClick={() => setTier(m.id)}
              title={`${m.hint}. Rough cost per answer: ${m.cost}.`}
              className={`rounded-lg border px-3 py-1 text-xs transition disabled:opacity-50 ${
                tier === m.id ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-100" : "border-slate-700 text-slate-400 hover:border-cyan-400/30 hover:text-white"
              }`}
            >
              <span aria-hidden="true">{m.icon} </span>
              {m.label} <span className="text-slate-500">{m.cost}</span>
            </button>
          ))}
          <span className="text-[11px] text-slate-500">Rough cost per answer. Sonnet is the default.</span>
          {remembered > 0 && (
            <span className="ml-auto flex items-center gap-2 text-[11px] text-slate-500">
              {info.name} remembers this conversation
              <button type="button" onClick={newConversation} disabled={busy} className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 hover:text-white disabled:opacity-40">
                New conversation
              </button>
            </span>
          )}
        </div>
        {suggestOpus && (
          <p role="status" className="rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-1.5 text-xs text-amber-100">
            This looks like a strategic question. Consider Opus for deeper analysis.{" "}
            <button type="button" onClick={() => setTier("opus")} className="underline hover:text-white">
              Switch to Opus
            </button>
          </p>
        )}
        <div className="flex gap-2">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && busy) {
                e.preventDefault();
                stop();
                return;
              }
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(e);
            }}
            rows={2}
            maxLength={8000}
            placeholder={`Ask ${info.name}…  (Ctrl+Enter to send, Esc to stop)`}
            className={`${inputClass} resize-y`}
            aria-label={`Message to ${info.name}`}
          />
          {busy ? (
            <button key="stop" type="button" onClick={stop} className={stopButton} aria-label="Stop waiting for the answer">
              Stop
            </button>
          ) : (
            <button key="send" type="submit" disabled={!text.trim()} className={buttonPrimary}>
              Send
            </button>
          )}
        </div>
      </form>
    </>
  );
}
