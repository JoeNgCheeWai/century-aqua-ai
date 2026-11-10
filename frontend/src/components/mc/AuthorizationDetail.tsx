import { Code, List, RiskBadge, SectionTitle } from "@/components/mc/ui";
import type { AuthorizationRequest } from "@/lib/mc-types";

/**
 * One authorization request, in full. The CTO's request is about code (a diff and commands); the CMO's is about content
 * (the copy to publish, deployment steps, and the claims a human must verify), so each shows the fields it has.
 * Everything is rendered as text, never as HTML.
 */
export default function AuthorizationDetail({ request, agentId }: { request: AuthorizationRequest; agentId: string }) {
  const commands = request.proposed_commands?.length ? request.proposed_commands : request.deployment_commands ?? [];
  const isContent = agentId === "cmo" || (!!request.proposed_content && !request.proposed_code_diff);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <RiskBadge level={request.risk_level} />
        <span className="text-sm font-medium text-slate-100">{request.action_summary}</span>
      </div>

      <div>
        <SectionTitle>Impact</SectionTitle>
        <p className="text-sm text-slate-300">{request.impact}</p>
      </div>

      {request.proposed_code_diff && (
        <div>
          <SectionTitle>Proposed code diff (for review, never applied by the agent)</SectionTitle>
          <Code>{request.proposed_code_diff}</Code>
        </div>
      )}

      {request.proposed_content && (
        <div>
          <SectionTitle>Proposed content (for review, never published by the agent)</SectionTitle>
          <Code>{request.proposed_content}</Code>
        </div>
      )}

      {commands.length > 0 && (
        <div>
          <SectionTitle>{isContent ? "Deployment steps (for a human to carry out)" : "Proposed commands (for a human to run)"}</SectionTitle>
          <Code>{commands.join("\n")}</Code>
        </div>
      )}

      {(request.claims_to_verify?.length ?? 0) > 0 && (
        <div className="rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
          <SectionTitle>Claims a human must substantiate first</SectionTitle>
          <List items={request.claims_to_verify ?? []} />
        </div>
      )}

      {request.rollback_plan && (
        <div>
          <SectionTitle>Rollback plan</SectionTitle>
          <p className="text-sm text-slate-300">{request.rollback_plan}</p>
        </div>
      )}

      <p className="text-xs text-slate-500">
        {request.status.replace(/_/g, " ")} · executed: {String(request.executed)}. Approving records your decision only; nothing is run or published by this system.
      </p>
    </div>
  );
}
