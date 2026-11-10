/**
 * POST /api/mc/council {query, agents?}: start a council session. The backend answers 202 {session_id, status: RUNNING}
 * at once (the async job pattern); the page then polls GET /api/mc/council/{session_id} until the status is terminal.
 */

import { callBackend, json, readJson, relay, requireSession } from "@/lib/mc-session";

const MAX_QUERY_CHARS = 4000;
const COUNCIL_AGENTS = new Set(["cao", "coo", "cto", "cmo"]);

export async function POST(request: Request) {
  const refused = await requireSession();
  if (refused) return refused;

  const body = await readJson(request);
  const query = typeof body?.query === "string" ? body.query.trim() : "";
  if (!query || query.length > MAX_QUERY_CHARS) return json({ error: "INVALID_REQUEST" }, 400);

  const payload: { query: string; agents?: string[] } = { query };
  if (body?.agents !== undefined) {
    const agents = body.agents;
    if (!Array.isArray(agents) || agents.length > 4 || !agents.every((a) => typeof a === "string" && COUNCIL_AGENTS.has(a))) {
      return json({ error: "INVALID_REQUEST" }, 400);
    }
    if (agents.length) payload.agents = agents as string[];
  }
  return relay(await callBackend("/api/council", { method: "POST", body: payload }));
}
