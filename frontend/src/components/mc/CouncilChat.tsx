"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import CouncilOptions from "@/components/mc/CouncilOptions";
import { Badge, Card, ErrorNote, Spinner, StatusBadge, buttonPrimary, inputClass } from "@/components/mc/ui";
import { McError, mc, useCouncilPoll } from "@/lib/mc-client";

/** Start a council session, show progress while it runs in the background, and show the options when it resolves. */
export default function CouncilChat() {
  const [text, setText] = useState("");
  const [starting, setStarting] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [submittedAt, setSubmittedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const { session, error: pollError } = useCouncilPoll(sessionId);
  const running = sessionId !== null && (!session || session.status === "RUNNING");

  useEffect(() => {
    if (!running || submittedAt === null) return;
    const timer = setInterval(() => setElapsed(Date.now() - submittedAt), 1000);
    return () => clearInterval(timer);
  }, [running, submittedAt]);

  async function start(event: FormEvent) {
    event.preventDefault();
    const query = text.trim();
    if (!query || starting || running) return;
    setStarting(true);
    setStartError(null);
    setSessionId(null);
    try {
      // The backend answers 202 {session_id, status: RUNNING} at once; the work continues in the background.
      const started = await mc<{ session_id: string }>("/council", { method: "POST", body: { query } });
      setSubmittedAt(Date.now());
      setElapsed(0);
      setSessionId(started.session_id);
    } catch (e) {
      if (!(e instanceof McError && e.status === 401)) setStartError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <form onSubmit={start} className="space-y-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          maxLength={4000}
          placeholder="Put a decision to the council. Include the constraints (budget, markets, timing)."
          className={inputClass}
          aria-label="Question for the council"
        />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-xl text-xs text-slate-500">
            The CAO and COO always sit; the CTO and CMO join when the question touches their field. The council advises and never acts.
          </p>
          <button type="submit" disabled={starting || running || !text.trim()} className={buttonPrimary}>
            {starting ? "Starting…" : "Convene the council"}
          </button>
        </div>
      </form>

      {startError && (
        <div className="mt-4">
          <ErrorNote message={startError} />
        </div>
      )}

      {sessionId && (
        <div className="mt-6 space-y-4">
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3">
                {running ? <Spinner label={`Council in session… ${Math.round(elapsed / 1000)}s`} /> : session && <StatusBadge status={session.status} />}
                {session?.participating_agents?.map((a) => (
                  <Badge key={a} tone="blue">
                    {a.toUpperCase()}
                  </Badge>
                ))}
              </div>
              <Link href={`/mission-control/council/${sessionId}`} className="text-xs text-cyan-300 hover:underline">
                Open full session →
              </Link>
            </div>
            {running && <p className="mt-2 text-xs text-slate-500">Polling every 3 seconds. You can leave this page: the session keeps running and is listed under Council.</p>}
            {pollError && <p className="mt-2 text-xs text-amber-300">Could not refresh: {pollError}</p>}
          </Card>
          {session && session.status !== "RUNNING" && <CouncilOptions session={session} />}
        </div>
      )}
    </>
  );
}
