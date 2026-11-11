"use client";

import { useState } from "react";
import AuthorizationDetail from "@/components/mc/AuthorizationDetail";
import ScenarioGrid from "@/components/mc/ScenarioGrid";
import { Badge, Card, List, SectionTitle } from "@/components/mc/ui";
import type { AgentAnswer } from "@/lib/mc-types";

/**
 * An agent's answer, conversational first (Phase 6.5). The short chat reply is what you read; the full recommendation,
 * the reasoning chain, the scenarios and the sources sit behind "Show technical details". If the answer has no chat
 * reply (the model gave none, or it was vetted out in code), the full recommendation is shown instead, so nothing is
 * ever hidden that the human needs. An authorization request is always visible: it is waiting for a decision.
 */
export default function AgentAnswerView({ answer, agent }: { answer: AgentAnswer; agent: string }) {
  const [open, setOpen] = useState(false);
  const hasChat = !!answer.chat_reply;
  const hasDetails = answer.reasoning_chain.length > 0 || !!answer.scenarios || answer.sources.length > 0 || hasChat;
  const detailsId = `details-${answer.audit_id ?? "x"}-${agent}`;

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="cyan">{agent.toUpperCase()}</Badge>
        {answer.needs_review && <Badge tone="amber">flagged for review</Badge>}
        {answer.audit_id == null && <Badge tone="amber">not recorded in the audit log</Badge>}
      </div>

      <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{hasChat ? answer.chat_reply : answer.response}</p>

      {answer.authorization_request && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-4">
          <SectionTitle>Waiting for your decision</SectionTitle>
          <AuthorizationDetail request={answer.authorization_request} agentId={agent} />
          <p className="mt-3 text-xs text-slate-400">Approve or reject it under Authorizations (audit #{answer.audit_id ?? "?"}).</p>
        </div>
      )}

      {hasDetails && (
        <div>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={detailsId}
            className="text-xs text-cyan-300 hover:underline"
          >
            {open ? "Hide technical details" : "Show technical details"}
          </button>
          {open && (
            <div id={detailsId} className="mt-3 space-y-4 border-t border-slate-800 pt-4">
              <div className="flex flex-wrap items-center gap-2">
                {answer.constitutional_score != null && (
                  <Badge tone={answer.needs_review ? "amber" : "green"}>constitutional score {answer.constitutional_score}</Badge>
                )}
                {answer.audit_id != null && <Badge tone="slate">audit #{answer.audit_id}</Badge>}
                {answer.model && <Badge tone="slate">{answer.model}</Badge>}
              </div>

              {hasChat && (
                <div>
                  <SectionTitle>Full recommendation</SectionTitle>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{answer.response}</p>
                </div>
              )}

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

              {answer.sources.length > 0 && (
                <div>
                  <SectionTitle>Sources</SectionTitle>
                  <List items={answer.sources} />
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
