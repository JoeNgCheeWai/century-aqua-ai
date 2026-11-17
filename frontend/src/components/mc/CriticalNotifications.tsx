"use client";

import { useState } from "react";
import Link from "next/link";
import { AgentChip, Badge, Card, Empty, ErrorNote, SectionTitle, Spinner, buttonGhost } from "@/components/mc/ui";
import { McError, mc, useLoad, when } from "@/lib/mc-client";
import type { CeoNotification, NotificationList } from "@/lib/mc-types";

/**
 * The critical decisions the COO has escalated to the CEO, each with the COO's plain-language WHY and its recommendation. Shown above the pending
 * requests on "Needs your decision". Marking one read only quiets the red count: the notification is settled when the task itself is decided below.
 * The COO's discussion happens in its chat (the link), which is not stored on this page.
 */
export default function CriticalNotifications() {
  const { data, error, loading, reload } = useLoad<NotificationList>((signal) => mc("/ceo-notifications?status=open&limit=50", { signal }), []);
  const [busy, setBusy] = useState<number | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function markRead(note: CeoNotification) {
    setBusy(note.id);
    setFailure(null);
    try {
      await mc(`/ceo-notifications/${note.id}/read`, { method: "POST" });
      reload();
    } catch (e) {
      if (!(e instanceof McError && e.status === 401)) setFailure((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mb-6 space-y-3">
      <div className="flex items-center gap-3">
        <SectionTitle>Critical, explained by the COO</SectionTitle>
        {loading && <Spinner label="Loading" />}
      </div>
      {error && <ErrorNote message={error} onRetry={reload} />}
      {failure && <ErrorNote message={failure} />}
      {data && data.items.length === 0 && <Empty>No critical decision is waiting. The COO decides everything else.</Empty>}
      {(data?.items ?? []).map((note) => (
        <Card key={note.id} className={note.status === "unread" ? "space-y-3 border-red-400/40" : "space-y-3"}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              {note.agent_id && <AgentChip id={note.agent_id} />}
              <Badge tone="red">critical</Badge>
              {note.status === "unread" && <Badge tone="amber">new</Badge>}
              <span className="text-xs text-slate-500">
                {note.audit_id ? `request #${note.audit_id} · ` : ""}told you {when(note.created_at)}
              </span>
            </div>
          </div>
          <p className="text-sm font-medium text-slate-100">{note.title}</p>
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">Why it is critical</p>
            <p className="mt-1 text-sm text-slate-200">{note.explanation}</p>
          </div>
          {note.recommendation && (
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">The COO recommends</p>
              <p className="mt-1 text-sm text-slate-200">{note.recommendation}</p>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-3">
            <Link href="/mission-control/agents/coo" className={buttonGhost}>
              Discuss with the COO
            </Link>
            {note.status === "unread" && (
              <button disabled={busy === note.id} onClick={() => markRead(note)} className={buttonGhost}>
                {busy === note.id ? "Saving…" : "Mark as read"}
              </button>
            )}
            <span className="text-xs text-slate-500">Decide it below, in the request itself.</span>
          </div>
        </Card>
      ))}
    </div>
  );
}
