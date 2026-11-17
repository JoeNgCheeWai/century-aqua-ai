/**
 * GET /api/mc/ceo-notifications: the CEO's notifications (a critical decision the COO escalated). Session cookie required; the council key is added
 * server-side. status: open (default) | unread | read | resolved | all. A backend failure is relayed as an error, never as an empty list.
 */

import { callBackend, queryString } from "@/lib/mc-backend";
import { relay, requireSession } from "@/lib/mc-session";

export async function GET(request: Request) {
  const refused = await requireSession();
  if (refused) return refused;
  const p = new URL(request.url).searchParams;
  const query = queryString({ status: p.get("status"), limit: p.get("limit"), offset: p.get("offset") });
  return relay(await callBackend(`/api/ceo-notifications${query}`));
}
