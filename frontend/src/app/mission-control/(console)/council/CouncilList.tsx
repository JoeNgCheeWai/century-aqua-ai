"use client";

import Link from "next/link";
import { useState } from "react";
import { AgentChip, Card, Empty, ErrorNote, PageTitle, Spinner, StatusBadge, buttonGhost, buttonPrimary, inputClass } from "@/components/mc/ui";
import { mc, useLoad, when } from "@/lib/mc-client";
import type { CouncilStatus, CouncilSummary, Paged } from "@/lib/mc-types";

const PAGE = 20;
const STATUSES: CouncilStatus[] = ["RUNNING", "RESOLVED", "ESCALATED_TO_CEO", "NEEDS_CEO_INPUT", "FAILED"];

export default function CouncilList() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad<Paged<CouncilSummary>>(
    (signal) => mc(`/council/sessions?limit=${PAGE}&offset=${offset}${status ? `&status=${status}` : ""}`, { signal }),
    [status, offset],
  );

  return (
    <>
      <PageTitle
        title="Council sessions"
        subtitle="Every session the executive council has held."
        actions={
          <Link href="/mission-control/agents/council" className={buttonPrimary}>
            Convene the council
          </Link>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
          className={`${inputClass} max-w-xs`}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ").toLowerCase()}
            </option>
          ))}
        </select>
        <button onClick={reload} className={buttonGhost}>
          Refresh
        </button>
        {loading && <Spinner label="Loading" />}
      </div>

      {error && <ErrorNote message={error} onRetry={reload} />}
      {data && data.items.length === 0 && <Empty>No sessions{status ? " with that status" : " yet"}.</Empty>}

      <div className="space-y-3">
        {(data?.items ?? []).map((s) => (
          <Link key={s.session_id} href={`/mission-control/council/${s.session_id}`} className="block">
            <Card className="transition hover:border-cyan-400/40">
              <div className="flex items-start justify-between gap-3">
                <p className="line-clamp-2 text-sm text-slate-100">{s.query}</p>
                <StatusBadge status={s.status} />
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
                <span className="flex gap-1">
                  {s.participating_agents.map((a) => (
                    <AgentChip key={a} id={a} />
                  ))}
                </span>
                <span>{when(s.started_at)}</span>
                <span>{s.option_count} option{s.option_count === 1 ? "" : "s"}</span>
                <span>{s.model_calls} model calls</span>
              </div>
            </Card>
          </Link>
        ))}
      </div>

      {data && data.total > PAGE && (
        <div className="mt-4 flex items-center justify-between text-sm text-slate-400">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className={buttonGhost}>
            ← Newer
          </button>
          <span>
            {offset + 1}-{Math.min(offset + PAGE, data.total)} of {data.total}
          </span>
          <button disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)} className={buttonGhost}>
            Older →
          </button>
        </div>
      )}
    </>
  );
}
