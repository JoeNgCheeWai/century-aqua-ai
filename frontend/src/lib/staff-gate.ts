/**
 * Decision logic for the staff areas, kept free of Clerk so it can be tested
 * directly. src/proxy.ts calls it for every request.
 *
 *   pages:  /internal/*  /admin/*
 *   API:    /api/internal/*  /api/admin/*
 */

import { NextResponse } from "next/server";
import { checkStaff, statusFor, type StaffCheck } from "@/lib/access";

export type StaffArea = "page" | "api" | null;

/**
 * Normalise before matching, so odd spellings cannot slip past the check:
 * percent-encoding (/%61dmin), repeated slashes (//admin) and upper case (/ADMIN)
 * are all treated as the staff area. Over-matching is harmless: a non-staff
 * visitor just gets the refusal.
 */
export function normalizePath(pathname: string): string {
  let path = pathname;
  try {
    path = decodeURIComponent(path);
  } catch {
    // malformed escape: match on the raw text
  }
  return path.replace(/\/+/g, "/").toLowerCase();
}

export function staffArea(pathname: string): StaffArea {
  const path = normalizePath(pathname);
  if (/^\/api\/(internal|admin)(\/|$)/.test(path)) return "api";
  if (/^\/(internal|admin)(\/|$)/.test(path)) return "page";
  return null;
}

/** The response for a refused request: JSON for the API, a redirect or plain page otherwise. */
export function refusal(status: number, area: Exclude<StaffArea, null>, url: URL): NextResponse {
  if (area === "api") {
    const error = status === 401 ? "UNAUTHENTICATED" : status === 503 ? "UNAVAILABLE" : "FORBIDDEN";
    return NextResponse.json({ error }, { status, headers: { "cache-control": "no-store" } });
  }
  if (status === 401) {
    const target = new URL("/sign-in", url);
    target.searchParams.set("redirect_url", url.pathname);
    return NextResponse.redirect(target);
  }
  // A real status code (not a redirect), so non-staff cannot tell what is behind it.
  return new NextResponse(
    status === 503 ? "Service unavailable" : "Forbidden: this area is for Century Aqua staff only.",
    { status, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } },
  );
}

/**
 * Returns a refusal for a staff-area request from someone who is not staff, or
 * undefined to let the request through (also for every non-staff path).
 */
export async function staffGate(
  url: URL,
  userId: string | null | undefined,
  check: (id: string | null | undefined) => Promise<StaffCheck> = checkStaff,
): Promise<NextResponse | undefined> {
  const area = staffArea(url.pathname);
  if (!area) return undefined;
  const result = await check(userId);
  if (result.ok) return undefined;
  return refusal(statusFor(result.reason), area, url);
}
