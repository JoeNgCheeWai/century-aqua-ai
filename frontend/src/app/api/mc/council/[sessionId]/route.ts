/** GET /api/mc/council/{sessionId}: poll one council session (RUNNING returns only the status; terminal returns everything). */

import { callBackend, json, relay, requireSession } from "@/lib/mc-session";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(_request: Request, ctx: { params: Promise<{ sessionId: string }> }) {
  const refused = await requireSession();
  if (refused) return refused;
  const { sessionId } = await ctx.params;
  if (!UUID.test(sessionId)) return json({ detail: "Council session not found." }, 404);
  return relay(await callBackend(`/api/council/${sessionId}`));
}
