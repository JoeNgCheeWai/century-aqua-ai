"use client";

import Link from "next/link";
import { useState } from "react";
import CouncilOptions from "@/components/mc/CouncilOptions";
import DebateFlow from "@/components/mc/DebateFlow";
import { Badge, Card, ErrorNote, Json, PageTitle, SectionTitle, Spinner, StatusBadge, buttonGhost } from "@/components/mc/ui";
import { useCouncilPoll, when } from "@/lib/mc-client";

type Tab = "outcome" | "debate" | "audit";

/** The audit browser, narrowed to the day(s) this session ran: the agents' own audit rows are not linked per turn. */
function auditLink(started: string | null | undefined, completed: string | null | undefined): string {
  const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : "");
  const params = new URLSearchParams();
  if (started) params.set("start_date", day(started));
  if (completed || started) params.set("end_date", day(completed ?? started));
  return `/mission-control/audit?${params.toString()}`;
}

export default function SessionView({ sessionId }: { sessionId: string }) {
  const { session, error } = useCouncilPoll(sessionId);
  const [tab, setTab] = useState<Tab>("outcome");

  if (!session) {
    return error ? (
      <>
        <PageTitle title="Boardroom session" />
        <ErrorNote message={error} />
      </>
    ) : (
      <Spinner label="Loading the session…" />
    );
  }

  const running = session.status === "RUNNING";
  const seconds = session.completed_at && session.started_at ? Math.round((Date.parse(session.completed_at) - Date.parse(session.started_at)) / 1000) : null;

  return (
    <>
      <PageTitle
        title="Boardroom session"
        subtitle={session.query ?? "Session in progress…"}
        actions={
          <Link href="/mission-control/council" className={buttonGhost}>
            ← All sessions
          </Link>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <StatusBadge status={session.status} />
        {running && <Spinner label="In session. This page refreshes every 3 seconds." />}
        <span className="text-xs text-slate-500">Started {when(session.started_at)}</span>
        {seconds !== null && <span className="text-xs text-slate-500">took {seconds}s</span>}
        {session.model_calls != null && !running && <span className="text-xs text-slate-500">{session.model_calls} model calls</span>}
        {session.total_tokens != null && <span className="text-xs text-slate-500">{session.total_tokens.toLocaleString()} tokens</span>}
        {session.executed === false && <Badge tone="amber">advisory only · nothing executed</Badge>}
      </div>
      {error && <p className="mb-3 text-xs text-amber-300">Could not refresh: {error}</p>}

      {!running && (
        <>
          <div role="tablist" aria-label="Session views" className="mb-4 flex gap-2">
            {([["outcome", "Outcome"], ["debate", "Debate"], ["audit", "Audit trail"]] as const).map(([key, label]) => (
              <button
                key={key}
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                  tab === key ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-100" : "border-slate-700 text-slate-400 hover:text-white"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "outcome" && (
            <>
              <CouncilOptions session={session} />
              {(session.agent_flagged_questions?.length ?? 0) > 0 && (
                <Card className="mt-4">
                  <SectionTitle>Everything the agents flagged (the record)</SectionTitle>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-slate-400">
                    {session.agent_flagged_questions?.map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                </Card>
              )}
            </>
          )}

          {tab === "debate" && <DebateFlow session={session} />}

          {tab === "audit" && (
            <div className="space-y-4">
              <Card>
                <SectionTitle>Where the trail is</SectionTitle>
                <p className="text-sm text-slate-300">
                  Every turn of this session is stored in the council turn log (the Debate tab shows it). Council turns are not written to the agents&apos; own
                  audit log, so there is no per-turn audit entry to open. The link below shows the agents&apos; audit entries from the same day(s), which may
                  include unrelated questions.
                </p>
                <Link href={auditLink(session.started_at, session.completed_at)} className={`${buttonGhost} mt-3 inline-block`}>
                  Open the audit log for those days →
                </Link>
              </Card>
              <Card>
                <SectionTitle>Raw session record</SectionTitle>
                <Json value={session} />
              </Card>
            </div>
          )}
        </>
      )}
    </>
  );
}
