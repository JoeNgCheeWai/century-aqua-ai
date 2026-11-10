/** GET /api/mc/council/sessions?status&limit&offset: the council sessions list (summary rows). */

import { callBackend, queryString } from "@/lib/mc-backend";
import { relay, requireSession } from "@/lib/mc-session";

export async function GET(request: Request) {
  const refused = await requireSession();
  if (refused) return refused;
  const p = new URL(request.url).searchParams;
  const query = queryString({ status: p.get("status"), limit: p.get("limit"), offset: p.get("offset") });
  return relay(await callBackend(`/api/council/sessions${query}`));
}
