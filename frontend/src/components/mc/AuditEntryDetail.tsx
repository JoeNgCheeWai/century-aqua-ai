import AuthorizationDetail from "@/components/mc/AuthorizationDetail";
import ScenarioGrid from "@/components/mc/ScenarioGrid";
import { Json, List, SectionTitle } from "@/components/mc/ui";
import { when } from "@/lib/mc-client";
import type { AuditEntry } from "@/lib/mc-types";

/** The full record of one audit entry: the answer, the reasoning chain, the scenarios and any authorization request. */
export default function AuditEntryDetail({ entry }: { entry: AuditEntry }) {
  const consult = entry.pre_consultation_requests;
  const hasConsult = !!consult && (consult.coo_query || consult.cto_query);

  return (
    <div className="space-y-4 border-t border-slate-800 pt-4">
      <div>
        <SectionTitle>Question</SectionTitle>
        <p className="whitespace-pre-wrap text-sm text-slate-200">{entry.query}</p>
      </div>

      <div>
        <SectionTitle>Response</SectionTitle>
        <p className="max-h-96 overflow-auto whitespace-pre-wrap text-sm leading-relaxed text-slate-300">{entry.response}</p>
      </div>

      {entry.reasoning_chain.length > 0 && (
        <div>
          <SectionTitle>Reasoning chain</SectionTitle>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-300 marker:text-cyan-400/60">
            {entry.reasoning_chain.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      )}

      {entry.scenarios && (
        <div>
          <SectionTitle>Scenarios</SectionTitle>
          <ScenarioGrid scenarios={entry.scenarios} />
        </div>
      )}

      {hasConsult && (
        <div>
          <SectionTitle>Pre-consultation requests</SectionTitle>
          <List items={[consult?.coo_query ? `COO: ${consult.coo_query}` : "", consult?.cto_query ? `CTO: ${consult.cto_query}` : ""].filter(Boolean)} />
        </div>
      )}

      {entry.authorization_request && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-4">
          <SectionTitle>Authorization request</SectionTitle>
          <AuthorizationDetail request={entry.authorization_request} agentId={entry.agent_id} />
          <p className="mt-3 text-xs text-slate-400">
            Decision: {entry.human_action}
            {entry.human_action_at ? ` on ${when(entry.human_action_at)}` : ""}
            {entry.human_action_by ? ` by ${entry.human_action_by}` : ""}.
          </p>
        </div>
      )}

      {entry.sources_cited.length > 0 && (
        <div>
          <SectionTitle>Sources cited</SectionTitle>
          <List items={entry.sources_cited} />
        </div>
      )}

      <details>
        <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">Raw record</summary>
        <div className="mt-2">
          <Json value={entry} />
        </div>
      </details>
    </div>
  );
}
