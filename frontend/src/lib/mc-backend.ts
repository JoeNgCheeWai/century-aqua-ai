/**
 * Server-side client for the FastAPI backend, used ONLY by the /api/mc/* route handlers (Phase 6).
 *
 * THE BROWSER NEVER SEES A BACKEND KEY. Every key (INTERNAL_COUNCIL_KEY for the council, audit log, session list and
 * authorization decisions; INTERNAL_CAO/COO/CTO/CMO_KEY for the four agents) is read from the server environment here and
 * added to the outgoing request. The route handlers check the Mission Control session cookie first (see mc-session.ts).
 *
 * This file imports `server-only`-style secrets from process.env, so it must never be imported by a client component.
 */

export const BACKEND_URL = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "");

export const AGENT_IDS = ["cao", "coo", "cto", "cmo"] as const;
export type AgentId = (typeof AGENT_IDS)[number];

export function isAgentId(value: string): value is AgentId {
  return (AGENT_IDS as readonly string[]).includes(value);
}

/** The key and header each backend endpoint wants. */
const AGENT_AUTH: Record<AgentId, { env: string; header: string }> = {
  cao: { env: "INTERNAL_CAO_KEY", header: "X-Internal-CAO-Key" },
  coo: { env: "INTERNAL_COO_KEY", header: "X-Internal-COO-Key" },
  cto: { env: "INTERNAL_CTO_KEY", header: "X-Internal-CTO-Key" },
  cmo: { env: "INTERNAL_CMO_KEY", header: "X-Internal-CMO-Key" },
};
const COUNCIL_AUTH = { env: "INTERNAL_COUNCIL_KEY", header: "X-Internal-Council-Key" };

// Render's free tier can take 30-60 s to wake, and an executive answer is one long model call.
export const AGENT_TIMEOUT_MS = 170_000;
export const READ_TIMEOUT_MS = 70_000;

export interface BackendResult {
  status: number;
  body: unknown;
}

function authHeader(auth: { env: string; header: string }): Record<string, string> | null {
  const key = process.env[auth.env];
  return key ? { [auth.header]: key } : null;
}

/** Call the backend. Never throws: a failure becomes {status, body:{error}} so a route handler can pass it on. */
export async function callBackend(
  path: string,
  options: { method?: "GET" | "POST"; body?: unknown; agent?: AgentId; actor?: string; timeoutMs?: number } = {},
): Promise<BackendResult> {
  const auth = authHeader(options.agent ? AGENT_AUTH[options.agent] : COUNCIL_AUTH);
  if (!auth) return { status: 503, body: { error: "NOT_CONFIGURED", detail: "The key for this backend endpoint is not set on the server." } };

  const headers: Record<string, string> = { ...auth, "Content-Type": "application/json" };
  if (options.actor) {
    headers["X-Admin-Id"] = options.actor;
    headers["X-User-Id"] = options.actor;
  }
  try {
    const response = await fetch(`${BACKEND_URL}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      signal: AbortSignal.timeout(options.timeoutMs ?? READ_TIMEOUT_MS),
    });
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = { error: "BAD_RESPONSE" };
    }
    // A 401 from the BACKEND means the key this server sent was refused (the key set here differs from the one on the
    // backend). It must never reach the browser as a 401: the browser reads 401 as "the Mission Control session ended"
    // and sends the user to the login page. Report it as a bad gateway with a readable cause instead.
    if (response.status === 401) {
      console.error(`mission-control: backend refused the key for ${path} (check that the key env var matches the backend's)`);
      return {
        status: 502,
        body: {
          error: "BACKEND_KEY_REJECTED",
          detail: "The backend refused the key this server sent. The key set on the web host does not match the key on the backend.",
        },
      };
    }
    return { status: response.status, body };
  } catch (error) {
    const name = (error as Error).name;
    console.error("mission-control: backend call failed:", name);
    return { status: 502, body: { error: name === "TimeoutError" ? "BACKEND_TIMEOUT" : "BACKEND_UNREACHABLE" } };
  }
}

/** Build a query string from the parameters that are set. */
export function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
