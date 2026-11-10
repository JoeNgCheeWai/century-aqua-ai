import AuthorizationDetail from "@/components/mc/AuthorizationDetail";
import ScenarioGrid from "@/components/mc/ScenarioGrid";
import { Badge, Card, List, SectionTitle } from "@/components/mc/ui";
import type { AgentAnswer } from "@/lib/mc-types";

/** A structured agent answer: the recommendation, the reasoning chain, the scenarios and any authorization request. */
export default function AgentAnswerView({ answer, agent }: { answer: AgentAnswer; agent: string }) {
  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="cyan">{agent.toUpperCase()}</Badge>
        {answer.constitutional_score != null && <Badge tone={answer.needs_review ? "amber" : "green"}>constitutional score {answer.constitutional_score}</Badge>}
        {answer.needs_review && <Badge tone="amber">flagged for review</Badge>}
        {answer.audit_id != null ? <Badge tone="slate">audit #{answer.audit_id}</Badge> : <Badge tone="amber">not recorded in the audit log</Badge>}
      </div>

      <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{answer.response}</p>

      {answer.reasoning_chain.length > 0 && (
        <div>
          <SectionTitle>Reasoning chain</SectionTitle>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-300 marker:text-cyan-400/60">
            {answer.reasoning_chain.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      )}

      {answer.scenarios && (
        <div>
          <SectionTitle>Scenarios</SectionTitle>
          <ScenarioGrid scenarios={answer.scenarios} />
        </div>
      )}

      {answer.authorization_request && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-4">
          <SectionTitle>Authorization request: waiting for a human</SectionTitle>
          <AuthorizationDetail request={answer.authorization_request} agentId={agent} />
          <p className="mt-3 text-xs text-slate-400">Decide it under Authorizations (audit #{answer.audit_id ?? "?"}).</p>
        </div>
      )}

      {answer.sources.length > 0 && (
        <div>
          <SectionTitle>Sources</SectionTitle>
          <List items={answer.sources} />
        </div>
      )}
    </Card>
  );
}
