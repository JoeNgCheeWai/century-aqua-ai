/**
 * Server-only helpers for Mission Control route handlers and pages (they read the request's cookies).
 *
 * Every /api/mc/* handler (except login and logout) calls requireSession() FIRST. The proxy (src/proxy.ts) already
 * refused requests without a valid cookie; this is the second lock, so no handler depends on the proxy alone.
 */

import { cookies } from "next/headers";
import { SESSION_COOKIE, authConfigured, verifySessionToken } from "./mc-auth";
import { callBackend, type BackendResult } from "./mc-backend";

const NO_STORE = { "cache-control": "no-store" };

export function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: NO_STORE });
}

/** Pass a backend result straight through (status and JSON body). */
export function relay(result: BackendResult): Response {
  return json(result.body, result.status);
}

/** A Response to send back when the caller has no valid session, or null when the session is good. */
export async function requireSession(): Promise<Response | null> {
  if (!authConfigured()) return json({ error: "UNAVAILABLE" }, 503);
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  return verifySessionToken(token, process.env.ADMIN_SESSION_SECRET) ? null : json({ error: "UNAUTHENTICATED" }, 401);
}

/** Who is acting, for the audit trail (X-Admin-Id). Single-user for now, so it is one fixed id. */
export const ACTOR = "mission-control";

/** Parse a JSON body, or null if it is missing or not an object. */
export async function readJson(request: Request): Promise<Record<string, unknown> | null> {
  try {
    const value: unknown = await request.json();
    return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export { callBackend };
