import { NextResponse, type NextRequest } from "next/server";
import { clerkMiddleware } from "@clerk/nextjs/server";
import { clerkConfigured } from "@/lib/clerk-config";
import { SESSION_COOKIE } from "@/lib/mc-auth";
import { mcArea, mcGate } from "@/lib/mc-gate";
import { refusal, staffArea, staffGate } from "@/lib/staff-gate";

// Makes Clerk's auth() available to pages and route handlers, and is the FIRST of
// two locks on the staff areas:
//   /internal/*  and  /admin/*   (pages)      and   /api/internal/*  /api/admin/*
// A visitor who is not signed in is sent to sign-in (pages) or gets 401 (API).
// A signed-in user who is not staff gets a real 403 (pages and API). The layouts
// under app/internal and app/admin check again, so this is never the only lock.
// If the Clerk keys are missing the staff areas are closed completely (503)
// and the rest of the public site keeps working.
//
// MISSION CONTROL (Phase 6) is a third, separate area: /mission-control/* and /api/mc/*. It does NOT use Clerk: it is
// locked by the PIN session cookie (see lib/mc-auth.ts), so it works even where Clerk is not configured. It is checked
// here first, and every /api/mc handler checks the cookie again.

// LOGGING. When the proxy itself refuses a request (any 4xx or 5xx) it says so, with the PATH and METHOD only: never the query string, a cookie, a
// header or a body, so nothing secret can reach a log. There is deliberately NO line for requests that pass: it would fire on every page load and
// health poll. (It was there briefly while a 403 on POST /api/mc/agents/cto was diagnosed; that 403 was a browser extension, not this code. See
// PROJECT_CONTEXT.md Section 28.) A refusal that Vercel's request log shows but that has no "[MIDDLEWARE] Returning" line here was not sent by this proxy.
function logDecision<T extends NextResponse | undefined>(request: NextRequest, response: T): T {
  if (response) {
    const pathname = request.nextUrl.pathname;
    if (response.status === 403) console.log("[MIDDLEWARE] Returning 403", { pathname, method: request.method });
    else if (response.status >= 400) console.log(`[MIDDLEWARE] Returning ${response.status}`, { pathname, method: request.method });
  }
  return response;
}

const clerkProxy = clerkMiddleware(async (auth, request) => {
  if (mcArea(request.nextUrl.pathname)) return logDecision(request, mcGate(request.nextUrl, request.cookies.get(SESSION_COOKIE)?.value));
  // Only staff-area paths need the (database) check; everything else passes straight through.
  if (!staffArea(request.nextUrl.pathname)) return;
  const { userId } = await auth();
  return logDecision(request, await staffGate(request.nextUrl, userId));
});

export default clerkConfigured
  ? clerkProxy
  : (request: NextRequest) => {
      if (mcArea(request.nextUrl.pathname)) {
        return logDecision(request, mcGate(request.nextUrl, request.cookies.get(SESSION_COOKIE)?.value)) ?? NextResponse.next();
      }
      const area = staffArea(request.nextUrl.pathname);
      if (area) return logDecision(request, refusal(503, area, request.nextUrl));
      return NextResponse.next();
    };

export const config = {
  matcher: [
    // Skip Next.js internals and static files (including the hero video).
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|mp4)).*)",
    "/(api|trpc)(.*)",
  ],
};
