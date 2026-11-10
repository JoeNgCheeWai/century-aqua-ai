/** GET /api/mc/audit-logs: the audit-log browser's data. Session cookie required; adds the council key server-side. */

import { callBackend, queryString } from "@/lib/mc-backend";
import { relay, requireSession } from "@/lib/mc-session";

export async function GET(request: Request) {
  const refused = await requireSession();
  if (refused) return refused;
  const p = new URL(request.url).searchParams;
  const query = queryString({
    agent_id: p.get("agent_id"),
    start_date: p.get("start_date"),
    end_date: p.get("end_date"),
    human_action: p.get("human_action"),
    has_authorization: p.get("has_authorization"),
    search: p.get("search"),
    limit: p.get("limit"),
    offset: p.get("offset"),
  });
  return relay(await callBackend(`/api/audit-logs${query}`));
}
