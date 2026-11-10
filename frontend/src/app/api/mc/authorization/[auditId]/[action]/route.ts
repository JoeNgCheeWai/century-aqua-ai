/**
 * POST /api/mc/authorization/{auditId}/{approve|reject}: record a human's decision on an agent's authorization request.
 *
 * Approving records the decision only. Nothing is executed, here or in the backend: a person still carries out the change.
 */

import { ACTOR, callBackend, json, relay, requireSession } from "@/lib/mc-session";

export async function POST(_request: Request, ctx: { params: Promise<{ auditId: string; action: string }> }) {
  const refused = await requireSession();
  if (refused) return refused;
  const { auditId, action } = await ctx.params;
  if (!/^\d{1,10}$/.test(auditId) || (action !== "approve" && action !== "reject")) {
    return json({ error: "INVALID_REQUEST" }, 400);
  }
  return relay(await callBackend(`/api/authorization/${auditId}/${action}`, { method: "POST", actor: ACTOR }));
}
