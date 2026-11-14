import AuthorizationDetail from "@/components/mc/AuthorizationDetail";
import MockupGallery from "@/components/mc/MockupGallery";
import ScenarioGrid from "@/components/mc/ScenarioGrid";
import { Badge, Card, List, SectionTitle } from "@/components/mc/ui";
import { splitResponse } from "@/lib/mc-answer";
import type { AgentAnswer } from "@/lib/mc-types";

/**
 * An agent's answer, kept clean. What a reader sees by default, top to bottom:
 *   1. the answer itself: the short conversational reply (or, when the model gave none, the full recommendation);
 *   2. any AI-generated mockup images;
 *   3. an authorization request, if one is waiting for a decision (always visible: it needs the human).
 * Everything else sits behind ONE native <details> toggle, "Show internal reasoning & sources", closed by default: the full
 * recommendation (when a short reply is shown above), the reasoning chain, the scenarios, the sources the model cited, the
 * knowledge-base documents that were retrieved, and the audit facts (score, audit id, model).
 *
 * The backend appends two notes to the reply text itself (the documents list and a plain-text copy of the authorization
 * request). They are separated out by lib/mc-answer.ts so the default view is not cluttered by them. Nothing is deleted: the
 * documents are listed inside the toggle, and the authorization card already shows the request. If a note does not match its
 * known shape the text is shown whole, never cut.
 */
export default function AgentAnswerView({ answer, agent }: { answer: AgentAnswer; agent: string }) {
  const request = answer.authorization_request ?? null;
  const mockups = request?.mockup_urls ?? [];
  const { body, documents } = splitResponse(answer.response, !!request);
  const chat = answer.chat_reply?.trim() || null;
  const reasoning = answer.reasoning_chain ?? [];
  const sources = answer.sources ?? [];
  const hasInternals = reasoning.length > 0 || !!answer.scenarios || sources.length > 0 || documents.length > 0 || !!chat;

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="cyan">{agent.toUpperCase()}</Badge>
        {answer.needs_review && <Badge tone="amber">flagged for review</Badge>}
        {answer.audit_id == null && <Badge tone="amber">not recorded in the audit log</Badge>}
      </div>

      <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{chat ?? body}</p>

      {mockups.length > 0 && <MockupGallery urls={mockups} large />}

      {request && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-4">
          <SectionTitle>Waiting for your decision</SectionTitle>
          <AuthorizationDetail request={request} agentId={agent} showMockups={false} />
          <p className="mt-3 text-xs text-slate-400">Approve or reject it under Authorizations (audit #{answer.audit_id ?? "?"}).</p>
        </div>
      )}

      {hasInternals && (
        <details className="group rounded-lg border border-slate-800 bg-black/20">
          <summary className="cursor-pointer select-none list-none rounded-lg px-3 py-2 text-xs text-cyan-300 hover:bg-cyan-400/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 [&::-webkit-details-marker]:hidden">
            <span aria-hidden="true" className="mr-1.5 inline-block transition-transform group-open:rotate-90">
              ▸
            </span>
            Show internal reasoning &amp; sources
          </summary>
          <div className="space-y-4 border-t border-slate-800 px-3 py-4">
            <div className="flex flex-wrap items-center gap-2">
              {answer.constitutional_score != null && (
                <Badge tone={answer.needs_review ? "amber" : "green"}>constitutional score {answer.constitutional_score}</Badge>
              )}
              {answer.audit_id != null && <Badge tone="slate">audit #{answer.audit_id}</Badge>}
              {answer.model && <Badge tone="slate">{answer.model}</Badge>}
            </div>

            {chat && (
              <div>
                <SectionTitle>Full recommendation</SectionTitle>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{body}</p>
              </div>
            )}

            {reasoning.length > 0 && (
              <div>
                <SectionTitle>Reasoning chain</SectionTitle>
                <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-300 marker:text-cyan-400/60">
                  {reasoning.map((step, i) => (
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

            {sources.length > 0 && (
              <div>
                <SectionTitle>Sources</SectionTitle>
                <List items={sources} />
              </div>
            )}

            {documents.length > 0 && (
              <div>
                <SectionTitle>Knowledge base documents retrieved</SectionTitle>
                <List items={documents} />
              </div>
            )}
          </div>
        </details>
      )}
    </Card>
  );
}
