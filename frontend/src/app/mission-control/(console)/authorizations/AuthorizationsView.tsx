"use client";

import { useState } from "react";
import AuthorizationCard from "@/components/mc/AuthorizationCard";
import { Empty, ErrorNote, PageTitle, Spinner, buttonGhost, inputClass } from "@/components/mc/ui";
import { mc, useLoad } from "@/lib/mc-client";
import type { AuditEntry, Paged } from "@/lib/mc-types";

const PAGE = 20;
type View = "Pending" | "Approved" | "Rejected";

export default function AuthorizationsView() {
  const [view, setView] = useState<View>("Pending");
  const [agent, setAgent] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad<Paged<AuditEntry>>(
    (signal) => mc(`/audit-logs?has_authorization=true&human_action=${view}&limit=${PAGE}&offset=${offset}${agent ? `&agent_id=${agent}` : ""}`, { signal }),
    [view, agent, offset],
  );

  return (
    <>
      <PageTitle title="Authorization requests" subtitle="Changes and publications the agents proposed. They act on nothing until a human decides." />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Decision" className="flex gap-2">
          {(["Pending", "Approved", "Rejected"] as View[]).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={view === v}
              onClick={() => {
                setView(v);
                setOffset(0);
              }}
              className={`rounded-lg border px-3 py-1.5 text-sm transition ${view === v ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-100" : "border-slate-700 text-slate-400 hover:text-white"}`}
            >
              {v}
              {view === v && data ? ` (${data.total})` : ""}
            </button>
          ))}
        </div>
        <select
          value={agent}
          onChange={(e) => {
            setAgent(e.target.value);
            setOffset(0);
          }}
          className={`${inputClass} max-w-[10rem]`}
          aria-label="Filter by agent"
        >
          <option value="">Both agents</option>
          <option value="cto">CTO</option>
          <option value="cmo">CMO</option>
        </select>
        <button onClick={reload} className={buttonGhost}>
          Refresh
        </button>
        {loading && <Spinner label="Loading" />}
      </div>

      {error && <ErrorNote message={error} onRetry={reload} />}
      {data && data.items.length === 0 && <Empty>{view === "Pending" ? "Nothing is waiting for a decision." : `No ${view.toLowerCase()} requests.`}</Empty>}

      <div className="space-y-4">
        {(data?.items ?? []).map((entry) => (
          <AuthorizationCard key={entry.id} entry={entry} onDecided={reload} />
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
