"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import CouncilOptions from "@/components/mc/CouncilOptions";
import { Badge, Card, ErrorNote, Spinner, StatusBadge, buttonPrimary, inputClass } from "@/components/mc/ui";
import { McError, mc, useCouncilPoll } from "@/lib/mc-client";
import { MEMORY_PREFIX, boardroomQuery, parseStored, readStore, writeStore, type BoardroomEntry } from "@/lib/mc-memory";
import { DEFAULT_SEATS, MIN_SEATS, SEATS, canConvene, roleNote, seatProblem, toggleSeat, type SeatId } from "@/lib/mc-boardroom";
import { AGENT_INFO } from "@/lib/mc-types";

const MEMORY_KEY = `${MEMORY_PREFIX}boardroom`;
const MAX_ENTRIES = 10;

/**
 * Convene the Boardroom (the executive council): start a session, show progress while it runs in the background, and show
 * the options when it resolves. The CEO decides who attends: "I'll choose" makes all four executives independently
 * selectable (any 2 to 4), and nobody is added on the CEO's behalf. "Automatic" lets the system pick from the question
 * instead. The only rule is the backend's: at least two different agents.
 *
 * MEMORY. Each Boardroom session is a separate backend job that starts clean (by design), so a follow-up question carries
 * the earlier discussions inside the question text (see boardroomQuery in lib/mc-memory.ts). Every finished session in this
 * conversation is remembered in sessionStorage (this browser tab). "New conversation" forgets them.
 */
export default function CouncilChat() {
  const [text, setText] = useState("");
  const [earlier, setEarlier] = useState<BoardroomEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const pendingQuestion = useRef("");
  const recorded = useRef<string | null>(null);
  const [auto, setAuto] = useState(false);
  const [seats, setSeats] = useState<SeatId[]>(DEFAULT_SEATS);
  const [starting, setStarting] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [submittedAt, setSubmittedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const { session, error: pollError } = useCouncilPoll(sessionId);
  const running = sessionId !== null && (!session || session.status === "RUNNING");

  // Storage is read after mount (it does not exist on the server). The state is set in a microtask, not synchronously in the
  // effect body (React's lint rule against cascading renders, and the same approach as useLoad in lib/mc-client.ts).
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      const saved = parseStored<BoardroomEntry>(readStore(MEMORY_KEY));
      if (saved.length > 0) setEarlier(saved);
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!loaded) return;
    writeStore(MEMORY_KEY, earlier.length ? JSON.stringify(earlier) : null);
  }, [earlier, loaded]);

  // When a session finishes, remember what it concluded (once), so the next question can build on it.
  useEffect(() => {
    if (!session || !sessionId || session.status === "RUNNING" || recorded.current === sessionId) return;
    recorded.current = sessionId;
    const entry: BoardroomEntry = {
      question: pendingQuestion.current || session.query || "",
      status: session.status,
      options: (session.options ?? []).map((o) => `${o.option_id}: ${o.title} - ${o.summary}`),
      ceoQuestions: session.ceo_questions ?? [],
    };
    setEarlier((prev) => [...prev, entry].slice(-MAX_ENTRIES));
  }, [session, sessionId]);

  useEffect(() => {
    if (!running || submittedAt === null) return;
    const timer = setInterval(() => setElapsed(Date.now() - submittedAt), 1000);
    return () => clearInterval(timer);
  }, [running, submittedAt]);

  async function start(event: FormEvent) {
    event.preventDefault();
    const query = text.trim();
    if (!query || starting || running) return;
    if (!auto && !canConvene(seats)) return; // the button is disabled too; this guards Enter and any other route in
    setStarting(true);
    setStartError(null);
    setSessionId(null);
    try {
      // The backend answers 202 {session_id, status: RUNNING} at once; the work continues in the background.
      // `agents` is left out for automatic selection. When the human chooses, exactly the ticked agents are sent.
      // A follow-up carries the earlier discussions inside the question (the backend session starts clean).
      const withContext = boardroomQuery(query, earlier);
      const body = auto ? { query: withContext } : { query: withContext, agents: seats };
      const started = await mc<{ session_id: string }>("/council", { method: "POST", body });
      pendingQuestion.current = query;
      setText("");
      setSubmittedAt(Date.now());
      setElapsed(0);
      setSessionId(started.session_id);
    } catch (e) {
      if (!(e instanceof McError && e.status === 401)) setStartError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  function newConversation() {
    if (starting || running) return;
    setEarlier([]);
    setSessionId(null);
    recorded.current = null;
    pendingQuestion.current = "";
    writeStore(MEMORY_KEY, null);
  }

  return (
    <>
      {earlier.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-cyan-400/20 bg-cyan-400/5 px-3 py-2 text-xs text-slate-300">
          <span>
            The Boardroom remembers {earlier.length} earlier discussion{earlier.length === 1 ? "" : "s"} in this conversation. Your next question can build on{" "}
            {earlier.length === 1 ? "it" : "them"}: “{earlier[earlier.length - 1].question.slice(0, 90)}
            {earlier[earlier.length - 1].question.length > 90 ? "…" : ""}”
          </span>
          <button type="button" onClick={newConversation} disabled={starting || running} className="rounded border border-slate-600 px-2 py-0.5 text-slate-200 hover:text-white disabled:opacity-40">
            New conversation
          </button>
        </div>
      )}
      <form onSubmit={start} className="space-y-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          maxLength={4000}
          placeholder="Put a decision to the Boardroom. Include the constraints (budget, markets, timing)."
          className={inputClass}
          aria-label="Question for the Boardroom"
        />

        <fieldset className="space-y-2 rounded-lg border border-slate-700/60 p-3">
          <legend className="px-1 text-xs uppercase tracking-[0.18em] text-cyan-300/80">Who sits at the table</legend>
          <div className="flex flex-wrap gap-4 text-sm text-slate-300">
            <label className="flex items-center gap-2">
              <input type="radio" name="seating" checked={!auto} onChange={() => setAuto(false)} />
              I&apos;ll choose
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="seating" checked={auto} onChange={() => setAuto(true)} />
              Automatic (the system picks from the question)
            </label>
          </div>
          <div className="flex flex-wrap gap-4 text-sm" role="group" aria-label="Executives at the table">
            {SEATS.map((id) => (
              <label key={id} className={`flex items-center gap-2 ${auto ? "text-slate-500" : "text-slate-200"}`}>
                <input type="checkbox" checked={auto ? false : seats.includes(id)} disabled={auto} onChange={() => setSeats((prev) => toggleSeat(prev, id))} />
                {AGENT_INFO[id].name}
              </label>
            ))}
          </div>
          {auto ? (
            <p className="text-xs text-slate-500">The system seats the CAO and COO and adds the CTO and CMO when the question touches their field. The Boardroom advises and never acts.</p>
          ) : (
            <div className="space-y-1 text-xs">
              {seatProblem(seats) ? (
                <p role="alert" className="text-amber-300">
                  {seatProblem(seats)}
                </p>
              ) : (
                <p className="text-slate-400">
                  {seats.length} at the table: {seats.map((a) => a.toUpperCase()).join(", ")}. {roleNote(seats)}
                </p>
              )}
              <p className="text-slate-500">Any combination of {MIN_SEATS} to 4 works. The Boardroom advises and never acts.</p>
            </div>
          )}
        </fieldset>

        <div className="flex justify-end">
          <button type="submit" disabled={starting || running || !text.trim() || (!auto && !canConvene(seats))} className={buttonPrimary}>
            {starting ? "Starting…" : "Convene the Boardroom"}
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
                {running ? <Spinner label={`Boardroom in session… ${Math.round(elapsed / 1000)}s`} /> : session && <StatusBadge status={session.status} />}
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
            {running && <p className="mt-2 text-xs text-slate-500">Polling every 3 seconds. You can leave this page: the session keeps running and is listed under Boardroom.</p>}
            {pollError && <p className="mt-2 text-xs text-amber-300">Could not refresh: {pollError}</p>}
          </Card>
          {session && session.status !== "RUNNING" && <CouncilOptions session={session} />}
        </div>
      )}
    </>
  );
}
