/**
 * POST /api/mc/agents/{cao|coo|cto|cmo} {message, history?, reset_epoch?}: ask one executive agent.
 *
 * The agent endpoints are synchronous: one long model call, which can take a minute or more. maxDuration asks the host for
 * enough time; a host with a lower limit (some Vercel plans) will cut the call short and the page shows the error.
 * The agent's own key is added here, on the server. Only history entries the backend accepts are forwarded.
 */

import { AGENT_TIMEOUT_MS, callBackend, isAgentId } from "@/lib/mc-backend";
import { ACTOR, json, readJson, relay, requireSession } from "@/lib/mc-session";

export const maxDuration = 300;

const MAX_MESSAGE_CHARS = 8000;
const MAX_HISTORY = 20;

export async function POST(request: Request, ctx: { params: Promise<{ agentId: string }> }) {
  const refused = await requireSession();
  if (refused) return refused;

  const { agentId } = await ctx.params;
  if (!isAgentId(agentId)) return json({ error: "UNKNOWN_AGENT" }, 404);

  const body = await readJson(request);
  const message = typeof body?.message === "string" ? body.message.trim() : "";
  if (!message || message.length > MAX_MESSAGE_CHARS) return json({ error: "INVALID_REQUEST" }, 400);

  const history: { role: "user" | "assistant"; content: string }[] = [];
  if (body?.history !== undefined) {
    if (!Array.isArray(body.history)) return json({ error: "INVALID_REQUEST" }, 400);
    for (const entry of body.history) {
      const e = entry as { role?: unknown; content?: unknown } | null;
      if (!e || (e.role !== "user" && e.role !== "assistant") || typeof e.content !== "string") return json({ error: "INVALID_REQUEST" }, 400);
      history.push({ role: e.role, content: e.content });
    }
  }

  const payload: { message: string; history: typeof history; reset_epoch?: number } = { message, history: history.slice(-MAX_HISTORY) };
  if (typeof body?.reset_epoch === "number" && Number.isInteger(body.reset_epoch) && body.reset_epoch >= 0) payload.reset_epoch = body.reset_epoch;

  return relay(await callBackend(`/api/${agentId}`, { method: "POST", agent: agentId, body: payload, actor: ACTOR, timeoutMs: AGENT_TIMEOUT_MS }));
}
