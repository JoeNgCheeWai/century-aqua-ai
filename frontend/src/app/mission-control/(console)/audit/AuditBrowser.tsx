"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import AuditEntryDetail from "@/components/mc/AuditEntryDetail";
import AuditFilters, { NO_FILTERS, type Filters } from "@/components/mc/AuditFilters";
import { ActionBadge, AgentChip, Badge, Card, Empty, ErrorNote, PageTitle, Spinner, buttonGhost } from "@/components/mc/ui";
import { mc, useLoad, when } from "@/lib/mc-client";
import type { AuditEntry, Paged } from "@/lib/mc-types";

const PAGE = 25;

export default function AuditBrowser() {
  // A council session links here with ?start_date&end_date, so a day range can arrive in the URL.
  const params = useSearchParams();
  const [filters, setFilters] = useState<Filters>({
    ...NO_FILTERS,
    agent: params.get("agent_id") ?? "",
    from: params.get("start_date") ?? "",
    to: params.get("end_date") ?? "",
  });
  const [typed, setTyped] = useState("");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);

  // Search as you type, but only ask the server once typing pauses.
  useEffect(() => {
    const timer = setTimeout(() => {
      setFilters((f) => (f.search === typed.trim() ? f : { ...f, search: typed.trim() }));
      setOffset(0);
    }, 400);
    return () => clearTimeout(timer);
  }, [typed]);

  const { data, error, loading, reload } = useLoad<Paged<AuditEntry>>(
    (signal) => {
      const q = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
      if (filters.agent) q.set("agent_id", filters.agent);
      if (filters.from) q.set("start_date", filters.from);
      if (filters.to) q.set("end_date", filters.to);
      if (filters.status) q.set("human_action", filters.status);
      if (filters.authorization) q.set("has_authorization", filters.authorization);
      if (filters.search) q.set("search", filters.search);
      return mc(`/audit-logs?${q}`, { signal });
    },
    [filters, offset],
  );

  const filtered = Object.values(filters).some(Boolean);

  return (
    <>
      <PageTitle title="Audit log" subtitle="Every executive-agent answer on record. Nothing here is ever deleted by a reset." />

      <AuditFilters
        filters={filters}
        typed={typed}
        onTyped={setTyped}
        onChange={(patch) => {
          setFilters((f) => ({ ...f, ...patch }));
          setOffset(0);
        }}
        onClear={() => {
          setTyped("");
          setFilters(NO_FILTERS);
          setOffset(0);
        }}
        onRefresh={reload}
      />

      <div className="mb-3 flex items-center gap-3 text-sm text-slate-400">
        {loading ? <Spinner label="Loading" /> : data && <span>{data.total} {data.total === 1 ? "entry" : "entries"}</span>}
      </div>

      {error && <ErrorNote message={error} onRetry={reload} />}
      {data && data.items.length === 0 && <Empty>{filtered ? "No entries match these filters." : "The audit log is empty."}</Empty>}

      <div className="space-y-2">
        {(data?.items ?? []).map((entry) => {
          const open = openId === entry.id;
          return (
            <Card key={entry.id} className="!p-0">
              <button onClick={() => setOpenId(open ? null : entry.id)} aria-expanded={open} className="flex w-full items-start justify-between gap-3 p-4 text-left hover:bg-cyan-400/5">
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <AgentChip id={entry.agent_id} />
                    <span className="text-xs text-slate-500">
                      #{entry.id} · {when(entry.timestamp)}
                    </span>
                    {entry.needs_review && <Badge tone="amber">score {entry.constitutional_score}: review</Badge>}
                  </span>
                  <span className="mt-1 line-clamp-2 block text-sm text-slate-200">{entry.query}</span>
                </span>
                <span className="flex shrink-0 flex-col items-end gap-1">
                  {entry.authorization_request && <ActionBadge action={entry.human_action} />}
                  <span className="text-xs text-slate-500">{open ? "Hide" : "Details"}</span>
                </span>
              </button>
              {open && (
                <div className="px-4 pb-4">
                  <AuditEntryDetail entry={entry} />
                </div>
              )}
            </Card>
          );
        })}
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
