/**
 * POST /api/mc/login {pin}: Mission Control sign-in (public; everything else under /api/mc needs the cookie this sets).
 *
 * Checks the PIN against ADMIN_PIN in constant time, rate-limits wrong guesses, and on success sets an httpOnly,
 * sameSite=strict, signed, 8-hour cookie. A wrong PIN and a locked-out caller never reveal anything about the real PIN.
 */

import { cookies, headers } from "next/headers";
import {
  LoginThrottle,
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  authConfigured,
  createSessionToken,
  pinMatches,
  safeNextPath,
} from "@/lib/mc-auth";
import { json, readJson } from "@/lib/mc-session";

// One throttle per server instance. In-memory by design (single user); see the limits noted in mc-auth.ts.
const throttle = new LoginThrottle(5, 15 * 60 * 1000);

async function callerKey(): Promise<string> {
  const h = await headers();
  return (h.get("x-forwarded-for")?.split(",")[0] ?? h.get("x-real-ip") ?? "unknown").trim().slice(0, 64);
}

export async function POST(request: Request) {
  if (!authConfigured()) return json({ error: "UNAVAILABLE", detail: "Login is not configured on the server." }, 503);

  const who = await callerKey();
  const gate = throttle.check(who);
  if (!gate.allowed) {
    return Response.json(
      { error: "TOO_MANY_ATTEMPTS", retryAfterSeconds: gate.retryAfterSeconds },
      { status: 429, headers: { "cache-control": "no-store", "Retry-After": String(gate.retryAfterSeconds) } },
    );
  }

  const body = await readJson(request);
  if (!body) return json({ error: "INVALID_REQUEST" }, 400);

  if (!pinMatches(body.pin, process.env.ADMIN_PIN as string)) {
    throttle.fail(who);
    return json({ error: "INVALID_PIN" }, 401);
  }

  throttle.success(who);
  (await cookies()).set(SESSION_COOKIE, createSessionToken(process.env.ADMIN_SESSION_SECRET as string), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  });
  // The server decides where to go next, so the login page can never be used as an open redirect.
  return json({ ok: true, next: safeNextPath(typeof body.next === "string" ? body.next : null) });
}
