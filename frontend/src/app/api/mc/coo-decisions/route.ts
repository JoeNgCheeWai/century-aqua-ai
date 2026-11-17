/** GET /api/mc/coo-decisions: what the COO decided, plus the code-written audit summary. Session cookie required; the council key is added server-side. */

import { callBackend, queryString } from "@/lib/mc-backend";
import { relay, requireSession } from "@/lib/mc-session";

export async function GET(request: Request) {
  const refused = await requireSession();
  if (refused) return refused;
  const p = new URL(request.url).searchParams;
  const query = queryString({ days: p.get("days"), period: p.get("period") });
  return relay(await callBackend(`/api/coo-decisions${query}`));
}
