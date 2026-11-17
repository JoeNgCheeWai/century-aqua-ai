/**
 * POST /api/mc/ceo-notifications/{id}/read: mark one notification as read. A convenience only: it changes nothing about the task. A notification is
 * settled when the task itself is decided, never from here.
 */

import { ACTOR, callBackend, json, relay, requireSession } from "@/lib/mc-session";

export async function POST(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const refused = await requireSession();
  if (refused) return refused;
  const { id } = await ctx.params;
  if (!/^\d{1,10}$/.test(id)) return json({ error: "INVALID_REQUEST" }, 400);
  return relay(await callBackend(`/api/ceo-notifications/${id}/read`, { method: "POST", actor: ACTOR }));
}
