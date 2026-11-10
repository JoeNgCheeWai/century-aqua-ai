"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import AgentAnswerView from "@/components/mc/AgentAnswerView";
import { Card, ErrorNote, Spinner, buttonPrimary, inputClass } from "@/components/mc/ui";
import { McError, mc } from "@/lib/mc-client";
import { AGENT_INFO, type AgentAnswer, type AgentKey } from "@/lib/mc-types";

type Turn =
  | { kind: "user"; text: string }
  | { kind: "agent"; answer: AgentAnswer }
  | { kind: "error"; text: string };

/** Ask one executive. The agent endpoints are synchronous (one long model call), so this waits and shows a timer. */
export default function AgentChat({ agent }: { agent: AgentKey }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const epoch = useRef<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

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

  async function send(event: FormEvent) {
    event.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    // The conversation so far, so a follow-up has context: the user's questions and each answer's text.
    const history: { role: "user" | "assistant"; content: string }[] = [];
    for (const t of turns) {
      if (t.kind === "user") history.push({ role: "user", content: t.text });
      else if (t.kind === "agent") history.push({ role: "assistant", content: t.answer.response });
    }
    setTurns((prev) => [...prev, { kind: "user", text: message }]);
    setText("");
    setBusy(true);
    try {
      const answer = await mc<AgentAnswer>(`/agents/${agent}`, {
        method: "POST",
        body: { message, history, ...(epoch.current !== null ? { reset_epoch: epoch.current } : {}) },
      });
      epoch.current = answer.reset_epoch;
      setTurns((prev) => [...prev, { kind: "agent", answer }]);
    } catch (e) {
      if (!(e instanceof McError && e.status === 401)) setTurns((prev) => [...prev, { kind: "error", text: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  const info = AGENT_INFO[agent];
  return (
    <>
      <div className="space-y-4" aria-live="polite">
        {turns.length === 0 && (
          <Card>
            <p className="text-sm text-slate-300">
              Ask {info.name} ({info.role}) a question. A strategic question returns a reasoning chain and three scenarios.
              {agent === "cto" || agent === "cmo"
                ? " A request to change something returns an authorization request that waits for a human."
                : " Every answer is recorded in the audit log."}
            </p>
          </Card>
        )}
        {turns.map((t, i) =>
          t.kind === "user" ? (
            <div key={i} className="ml-auto max-w-2xl rounded-xl border border-blue-400/30 bg-blue-500/10 px-4 py-2 text-sm text-blue-50">
              {t.text}
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
            <p className="mt-1 text-xs text-slate-500">Executive answers are long model calls and can take a minute or more.</p>
          </Card>
        )}
        <div ref={endRef} />
      </div>

      <form onSubmit={send} className="sticky bottom-0 mt-4 flex gap-2 bg-slate-950/80 py-3 backdrop-blur">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(e);
          }}
          rows={2}
          maxLength={8000}
          placeholder={`Ask ${info.name}…  (Ctrl+Enter to send)`}
          className={`${inputClass} resize-y`}
          aria-label={`Message to ${info.name}`}
        />
        <button type="submit" disabled={busy || !text.trim()} className={buttonPrimary}>
          Send
        </button>
      </form>
    </>
  );
}
