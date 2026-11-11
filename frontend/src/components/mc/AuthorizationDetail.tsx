"use client";

import { useState } from "react";
import { Code, List, RiskBadge, SectionTitle } from "@/components/mc/ui";
import type { AuthorizationRequest } from "@/lib/mc-types";

/**
 * One authorization request (Phase 6.5: plain language first). A non-technical decision-maker sees, in this order: what is
 * being asked and how risky it is, the plain-language summary (problem, what it solves, the risk), the impact, and any
 * claims that must be substantiated first. The technical material (the CTO's diff and commands, the CMO's deployment
 * steps, the rollback plan) is behind "Show technical details".
 *
 * Two things are deliberately NOT hidden: the CMO's proposed content (it is exactly what would go public, so it is what
 * is being approved) and the claims a human must substantiate. Everything is rendered as text, never as HTML.
 * `startOpen` opens the technical details at once (used where the whole record is being read, such as the audit log).
 */
export default function AuthorizationDetail({
  request,
  agentId,
  startOpen = false,
}: {
  request: AuthorizationRequest;
  agentId: string;
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const commands = request.proposed_commands?.length ? request.proposed_commands : request.deployment_commands ?? [];
  const isContent = agentId === "cmo" || (!!request.proposed_content && !request.proposed_code_diff);
  const hasTechnical = !!request.proposed_code_diff || commands.length > 0 || !!request.rollback_plan;
  const summary = request.executive_summary;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <RiskBadge level={request.risk_level} />
        <span className="text-sm font-medium text-slate-100">{request.action_summary}</span>
      </div>

      {summary ? (
        <dl className="space-y-2 rounded-lg border border-cyan-400/25 bg-cyan-400/5 p-3">
          {(
            [
              ["What is this for?", summary.purpose],
              ["What problem does it solve?", summary.problem],
              ["What is the risk?", summary.risk],
            ] as const
          ).map(([question, answer]) => (
            <div key={question}>
              <dt className="text-xs font-medium uppercase tracking-[0.14em] text-cyan-300/80">{question}</dt>
              <dd className="mt-0.5 text-sm leading-relaxed text-slate-100">{answer}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-xs text-slate-500">No plain-language summary was written for this request. Read the impact and the technical details.</p>
      )}

      <div>
        <SectionTitle>Impact</SectionTitle>
        <p className="text-sm text-slate-300">{request.impact}</p>
      </div>

      {request.proposed_content && (
        <div>
          <SectionTitle>Content that would be published (for review, never published by the agent)</SectionTitle>
          <Code>{request.proposed_content}</Code>
        </div>
      )}

      {(request.claims_to_verify?.length ?? 0) > 0 && (
        <div className="rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
          <SectionTitle>Claims a human must substantiate first</SectionTitle>
          <List items={request.claims_to_verify ?? []} />
        </div>
      )}

      {hasTechnical && (
        <div>
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="text-xs text-cyan-300 hover:underline">
            {open ? "Hide technical details" : "Show technical details"}
          </button>
          {open && (
            <div className="mt-3 space-y-4 border-t border-slate-800 pt-4">
              {request.proposed_code_diff && (
                <div>
                  <SectionTitle>Proposed code diff (for review, never applied by the agent)</SectionTitle>
                  <Code>{request.proposed_code_diff}</Code>
                </div>
              )}
              {commands.length > 0 && (
                <div>
                  <SectionTitle>{isContent ? "Deployment steps (for a human to carry out)" : "Proposed commands (for a human to run)"}</SectionTitle>
                  <Code>{commands.join("\n")}</Code>
                </div>
              )}
              {request.rollback_plan && (
                <div>
                  <SectionTitle>Rollback plan</SectionTitle>
                  <p className="text-sm text-slate-300">{request.rollback_plan}</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <p className="text-xs text-slate-500">
        {request.status.replace(/_/g, " ")} · executed: {String(request.executed)}. Approving records your decision only; nothing is run or published by this system.
      </p>
    </div>
  );
}
