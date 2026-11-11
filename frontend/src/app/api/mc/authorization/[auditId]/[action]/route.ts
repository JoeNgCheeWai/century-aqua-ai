/**
 * POST /api/mc/authorization/{auditId}/{approve|reject}: record a human's decision on an agent's authorization request.
 *
 * Approving records the decision. If the request carries an execution_plan (Phase 6.6), the BACKEND then opens a pull request
 * from it (never a merge) and returns the outcome under `execution`. This route only relays; it never touches GitHub, and it
 * never holds the GitHub token (that lives on the backend only). A request without a plan executes nothing, as before.
 *
 * maxDuration: opening a pull request is several GitHub calls in a row, so allow more than the default.
 */

export const maxDuration = 60;


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
