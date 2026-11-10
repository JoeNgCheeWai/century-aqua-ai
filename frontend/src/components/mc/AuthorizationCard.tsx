"use client";

import { useState } from "react";
import AuthorizationDetail from "@/components/mc/AuthorizationDetail";
import { ActionBadge, AgentChip, Card, ErrorNote, buttonGhost } from "@/components/mc/ui";
import { McError, mc, when } from "@/lib/mc-client";
import type { AuditEntry, HumanAction } from "@/lib/mc-types";

interface Decision {
  audit_id: number;
  human_action: HumanAction;
  human_action_at: string | null;
  human_action_by: string | null;
  executed: boolean;
  note: string;
}

/**
 * One authorization request with its Approve and Reject buttons. A decision needs a second click ("Yes, approve"),
 * because it is final, and it only RECORDS the decision: nothing is run or published by this system.
 */
export default function AuthorizationCard({ entry, onDecided }: { entry: AuditEntry; onDecided: () => void }) {
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const request = entry.authorization_request;
  if (!request) return null;
  const pending = entry.human_action === "Pending" && !decision;

  async function decide(action: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      setDecision(await mc<Decision>(`/authorization/${entry.id}/${action}`, { method: "POST" }));
      setConfirming(null);
      // Keep the confirmation visible for a moment, then reload (the item leaves the Pending view).
      setTimeout(onDecided, 2500);
    } catch (e) {
      if (!(e instanceof McError && e.status === 401)) setError((e as Error).message);
      // 409: somebody already decided it. Reload so the card shows the real state.
      if (e instanceof McError && e.status === 409) setTimeout(onDecided, 2500);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <AgentChip id={entry.agent_id} />
          <span className="text-xs text-slate-500">
            audit #{entry.id} · asked {when(entry.timestamp)}
          </span>
        </div>
        <ActionBadge action={decision?.human_action ?? entry.human_action} />
      </div>

      <p className="text-sm italic text-slate-400">“{entry.query.length > 300 ? `${entry.query.slice(0, 300)}…` : entry.query}”</p>

      <AuthorizationDetail request={request} agentId={entry.agent_id} />

      {entry.human_action !== "Pending" && !decision && (
        <p className="text-xs text-slate-500">
          {entry.human_action} {when(entry.human_action_at)}
          {entry.human_action_by ? ` by ${entry.human_action_by}` : ""}.
        </p>
      )}

      {decision && (
        <div role="status" className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 p-3 text-sm text-emerald-100">
          Recorded as <strong>{decision.human_action}</strong> {when(decision.human_action_at)}. {decision.note}
        </div>
      )}
      {error && <ErrorNote message={error} />}

      {pending && (
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-3">
          {confirming ? (
            <>
              <span className="text-sm text-slate-300">
                {confirming === "approve" ? "Approve this request? This records your decision; nothing is run or published." : "Reject this request?"}
              </span>
              <button
                disabled={busy}
                onClick={() => decide(confirming)}
                className={
                  confirming === "approve"
                    ? "rounded-lg bg-emerald-500 px-3 py-1.5 text-sm font-medium text-slate-950 disabled:opacity-40"
                    : "rounded-lg bg-red-500 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                }
              >
                {busy ? "Recording…" : `Yes, ${confirming}`}
              </button>
              <button disabled={busy} onClick={() => setConfirming(null)} className={buttonGhost}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button onClick={() => setConfirming("approve")} className="rounded-lg border border-emerald-400/50 px-4 py-1.5 text-sm text-emerald-200 hover:bg-emerald-400/10">
                Approve
              </button>
              <button onClick={() => setConfirming("reject")} className="rounded-lg border border-red-400/50 px-4 py-1.5 text-sm text-red-200 hover:bg-red-400/10">
                Reject
              </button>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
