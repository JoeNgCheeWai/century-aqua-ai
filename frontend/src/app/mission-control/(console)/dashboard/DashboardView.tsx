"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Badge, Card, Empty, ErrorNote, PageTitle, SectionTitle, Spinner, StatusBadge, buttonGhost, AgentChip } from "@/components/mc/ui";
import { ago, mc, useLoad } from "@/lib/mc-client";
import { AGENT_INFO, type Overview } from "@/lib/mc-types";

const REFRESH_MS = 30_000; // polling, not a live connection (WebSockets are out of scope)

export default function DashboardView() {
  const { data, error, loading, reload } = useLoad<Overview>((signal) => mc<Overview>("/overview", { signal }), []);

  useEffect(() => {
    const timer = setInterval(reload, REFRESH_MS);
    return () => clearInterval(timer);
  }, [reload]);

  const pending = data?.pending_authorizations;

  return (
    <>
      <PageTitle
        title="Dashboard"
        subtitle="The executive team at a glance."
        actions={
          <div className="flex items-center gap-3">
            {loading && <Spinner label="Refreshing" />}
            <Link href="/mission-control/council" className={buttonGhost}>
              New council session
            </Link>
          </div>
        }
      />

      {error && <ErrorNote message={error} onRetry={reload} />}

      {data && !data.backend_reachable && (
        <div className="mb-4">
          <ErrorNote message="The backend did not answer its health check. It may be waking up (a free Render instance can take a minute)." onRetry={reload} />
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {(data?.agents ?? []).map((agent) => (
          <Card key={agent.agent_id} className="flex flex-col">
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="font-mono text-sm font-semibold text-cyan-200">{AGENT_INFO[agent.agent_id].name}</p>
                <p className="text-xs text-slate-400">{AGENT_INFO[agent.agent_id].role}</p>
              </div>
              <Badge tone={!agent.available ? "red" : agent.last_activity ? "green" : "slate"}>
                {!agent.available ? "unavailable" : agent.last_activity ? "active" : "no activity"}
              </Badge>
            </div>
            <dl className="mt-3 space-y-1 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500">Last activity</dt>
                <dd className="text-slate-200">{agent.available ? ago(agent.last_activity) : "-"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Answers on record</dt>
                <dd className="text-slate-200">{agent.answers_on_record ?? "-"}</dd>
              </div>
            </dl>
            {agent.last_query && <p className="mt-2 line-clamp-2 text-xs italic text-slate-500">“{agent.last_query}”</p>}
            <Link href={`/mission-control/agents/${agent.agent_id}`} className={`${buttonGhost} mt-4 text-center`}>
              Ask {AGENT_INFO[agent.agent_id].name}
            </Link>
          </Card>
        ))}
        {!data && !error && Array.from({ length: 4 }, (_, i) => <Card key={i} className="h-44 animate-pulse">{null}</Card>)}
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <Card>
          <SectionTitle>Pending authorizations</SectionTitle>
          <p className={`text-5xl font-light ${pending ? "text-amber-300" : "text-emerald-300"}`}>{pending ?? "-"}</p>
          <p className="mt-1 text-sm text-slate-400">
            {pending == null ? "Could not be loaded." : pending === 0 ? "Nothing is waiting for a decision." : "Waiting for a human decision."}
          </p>
          <Link href="/mission-control/authorizations" className={`${buttonGhost} mt-4 inline-block`}>
            Review requests
          </Link>
        </Card>

        <Card className="lg:col-span-2">
          <div className="mb-2 flex items-center justify-between">
            <SectionTitle>Recent council sessions</SectionTitle>
            <Link href="/mission-control/council" className="text-xs text-cyan-300 hover:underline">
              All sessions →
            </Link>
          </div>
          {data?.errors.sessions ? (
            <ErrorNote message={`Council sessions could not be loaded (${data.errors.sessions}).`} />
          ) : data?.recent_sessions && data.recent_sessions.items.length === 0 ? (
            <Empty>No council sessions yet.</Empty>
          ) : (
            <ul className="divide-y divide-slate-800">
              {(data?.recent_sessions?.items ?? []).map((s) => (
                <li key={s.session_id}>
                  <Link href={`/mission-control/council/${s.session_id}`} className="flex items-start justify-between gap-3 rounded px-1 py-2 hover:bg-cyan-400/5">
                    <span className="min-w-0">
                      <span className="line-clamp-2 text-sm text-slate-200">{s.query}</span>
                      <span className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                        {s.participating_agents.map((a) => (
                          <AgentChip key={a} id={a} />
                        ))}
                        <span>{ago(s.started_at)}</span>
                      </span>
                    </span>
                    <StatusBadge status={s.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
