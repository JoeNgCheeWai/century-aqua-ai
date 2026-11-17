"use client";

import { useState } from "react";
import Link from "next/link";
import { ActionBadge, AgentChip, Badge, Card, Empty, ErrorNote, PageTitle, SectionTitle, Spinner, buttonGhost, type Tone } from "@/components/mc/ui";
import { mc, useLoad, when } from "@/lib/mc-client";
import type { CooDecisionItem, CooDecisions } from "@/lib/mc-types";

type Group = CooDecisionItem["group"];
const GROUPS: { id: Group; label: string; tone: Tone; empty: string }[] = [
  { id: "approved", label: "Approved by the COO", tone: "green", empty: "The COO has approved nothing in this period." },
  { id: "escalated", label: "Escalated to you", tone: "red", empty: "Nothing has been escalated to you in this period." },
  { id: "rejected", label: "Rejected by the COO", tone: "slate", empty: "The COO has rejected nothing in this period." },
];
const PERIODS = [
  { id: "day", label: "Day", days: 1 },
  { id: "week", label: "Week", days: 7 },
  { id: "month", label: "Month", days: 30 },
] as const;

function DecisionCard({ item }: { item: CooDecisionItem }) {
  const [open, setOpen] = useState(false);
  const last = item.coo_decisions[item.coo_decisions.length - 1];
  return (
    <Card className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <AgentChip id={item.agent_id} />
          {item.risk_level && <Badge tone={item.risk_level === "critical" || item.risk_level === "high" ? "red" : item.risk_level === "medium" ? "amber" : "green"}>{item.risk_level} risk</Badge>}
          <span className="text-xs text-slate-500">
            request #{item.audit_id} · {when(item.at)}
          </span>
        </div>
        <ActionBadge action={item.human_action} />
      </div>
      <p className="text-sm text-slate-100">{item.action_summary || "No summary recorded."}</p>
      {last && (
        <p className="text-sm text-slate-300">
          <span className="text-xs uppercase tracking-wide text-slate-500">COO&apos;s reasoning: </span>
          {last.reasoning}
        </p>
      )}
      {item.group === "escalated" && (
        <p className="text-xs text-slate-400">
          {item.human_action === "Pending" ? "Waiting for your decision. " : `You decided it: ${item.human_action}${item.human_action_by ? ` (${item.human_action_by})` : ""}. `}
          {item.notification ? `You were notified (${item.notification.status}). ` : "No notification was recorded for it. "}
          <Link href="/mission-control/agents/coo" className="text-cyan-300 hover:underline">
            Discuss with the COO
          </Link>
        </p>
      )}
      {item.coo_decisions.length > 1 && (
        <>
          <button onClick={() => setOpen(!open)} className={buttonGhost}>
            {open ? "Hide" : "Show"} all {item.coo_decisions.length} decisions
          </button>
          {open && (
            <ol className="space-y-1 border-l border-slate-700 pl-3 text-xs text-slate-400">
              {item.coo_decisions.map((d, i) => (
                <li key={i}>
                  <strong className="text-slate-300">{d.decision}</strong> → {d.outcome}: {d.reasoning}
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </Card>
  );
}

export default function CooDecisionsView() {
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>(PERIODS[1]);
  const { data, error, loading, reload } = useLoad<CooDecisions>(
    (signal) => mc(`/coo-decisions?period=${period.id}&days=${Math.max(30, period.days)}`, { signal }),
    [period.id],
  );

  return (
    <>
      <PageTitle
        title="COO Decisions"
        subtitle="The COO approves or rejects tasks for every executive and sends only critical ones to you. Every decision is logged automatically, with its reasoning."
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Audit period" className="flex gap-2">
          {PERIODS.map((p) => (
            <button
              key={p.id}
              role="tab"
              aria-selected={period.id === p.id}
              onClick={() => setPeriod(p)}
              className={`rounded-lg border px-3 py-1.5 text-sm transition ${period.id === p.id ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-100" : "border-slate-700 text-slate-400 hover:text-white"}`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button onClick={reload} className={buttonGhost}>
          Refresh
        </button>
        {loading && <Spinner label="Loading" />}
      </div>

      {error && <ErrorNote message={error} onRetry={reload} />}

      {data && (
        <Card className="mb-6">
          <SectionTitle>Audit summary for the last {period.label.toLowerCase()}</SectionTitle>
          {!data.audit_ok && <p className="mb-2 text-sm text-amber-200">The audit summary could not be made. Nothing below is guessed.</p>}
          <pre className="whitespace-pre-wrap font-sans text-sm text-slate-200">{data.audit_summary}</pre>
          <p className="mt-2 text-xs text-slate-500">Written by code from the audit log, not by a model. Ask the COO to run an audit to investigate a finding.</p>
        </Card>
      )}

      {data &&
        GROUPS.map((group) => {
          const items = data.items.filter((i) => i.group === group.id);
          return (
            <section key={group.id} className="mb-6 space-y-3">
              <div className="flex items-center gap-2">
                <SectionTitle>{group.label}</SectionTitle>
                <Badge tone={group.tone}>{items.length}</Badge>
              </div>
              {items.length === 0 ? <Empty>{group.empty}</Empty> : items.map((item) => <DecisionCard key={item.audit_id} item={item} />)}
            </section>
          );
        })}
    </>
  );
}
