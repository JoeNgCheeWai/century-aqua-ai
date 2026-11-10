/**
 * Turns the pure access decision (decideAccess in mc-auth.ts) into a response for src/proxy.ts.
 *
 * An unauthenticated page request is redirected to the login page (with ?next=). An unauthenticated API request gets a
 * real 401 JSON. If the PIN or the signing secret is not configured, everything except the login page answers 503.
 */

import { NextResponse } from "next/server";
import { decideAccess, mcArea } from "./mc-auth";

export { mcArea };

/** A refusal for a Mission Control request without a valid session, or undefined to let it through. */
export function mcGate(
  url: URL,
  token: string | undefined,
  env: Record<string, string | undefined> = process.env,
  nowMs: number = Date.now(),
): NextResponse | undefined {
  const decision = decideAccess(url.pathname, token, env, nowMs);
  if (decision.type === "allow") return undefined;

  const headers = { "cache-control": "no-store" };
  if (decision.type === "unavailable") {
    if (decision.area === "api") return NextResponse.json({ error: "UNAVAILABLE" }, { status: 503, headers });
    return new NextResponse("Service unavailable: Mission Control login is not configured.", {
      status: 503,
      headers: { ...headers, "content-type": "text/plain; charset=utf-8" },
    });
  }
  if (decision.area === "api") return NextResponse.json({ error: "UNAUTHENTICATED" }, { status: 401, headers });
  const target = new URL("/mission-control/login", url);
  target.searchParams.set("next", decision.next);
  return NextResponse.redirect(target);
}
