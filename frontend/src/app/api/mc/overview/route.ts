/**
 * GET /api/mc/overview: everything the dashboard shows, gathered in one call on the server.
 *
 * Per agent: the last answer's time and the number of answers on record (from the audit log). The pending-authorization
 * count, the five latest council sessions, and whether the backend answered at all. "Last activity" is the honest signal
 * available: an agent has no heartbeat, so this does not claim an agent is "online".
 */

import { AGENT_IDS, BACKEND_URL, callBackend } from "@/lib/mc-backend";
import { json, requireSession } from "@/lib/mc-session";

interface AuditPage {
  items?: { timestamp: string | null; query: string; constitutional_score: number }[];
  total?: number;
}

export async function GET() {
  const refused = await requireSession();
  if (refused) return refused;

  const [agentPages, pending, sessions, health] = await Promise.all([
    Promise.all(AGENT_IDS.map((id) => callBackend(`/api/audit-logs?agent_id=${id}&limit=1`))),
    callBackend("/api/audit-logs?human_action=Pending&has_authorization=true&limit=1"),
    callBackend("/api/council/sessions?limit=5"),
    fetch(`${BACKEND_URL}/api/health`, { cache: "no-store", signal: AbortSignal.timeout(20_000) }).then((r) => r.ok, () => false),
  ]);

  const agents = AGENT_IDS.map((id, i) => {
    const page = agentPages[i];
    const body = (page.status === 200 ? page.body : null) as AuditPage | null;
    const last = body?.items?.[0];
    return {
      agent_id: id,
      available: page.status === 200,
      answers_on_record: body?.total ?? null,
      last_activity: last?.timestamp ?? null,
      last_query: last?.query ?? null,
      last_score: last?.constitutional_score ?? null,
    };
  });

  return json({
    backend_reachable: health,
    agents,
    pending_authorizations: pending.status === 200 ? ((pending.body as AuditPage).total ?? 0) : null,
    recent_sessions: sessions.status === 200 ? sessions.body : null,
    // Which parts failed, so the page can say so instead of showing a silent zero.
    errors: {
      audit: agentPages.some((p) => p.status !== 200) ? agentPages.find((p) => p.status !== 200)?.status : null,
      authorizations: pending.status === 200 ? null : pending.status,
      sessions: sessions.status === 200 ? null : sessions.status,
    },
  });
}
