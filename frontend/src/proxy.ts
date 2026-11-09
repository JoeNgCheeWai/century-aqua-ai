import { NextResponse, type NextRequest } from "next/server";
import { clerkMiddleware } from "@clerk/nextjs/server";
import { clerkConfigured } from "@/lib/clerk-config";
import { refusal, staffArea, staffGate } from "@/lib/staff-gate";

// Makes Clerk's auth() available to pages and route handlers, and is the FIRST of
// two locks on the staff areas:
//   /internal/*  and  /admin/*   (pages)      and   /api/internal/*  /api/admin/*
// A visitor who is not signed in is sent to sign-in (pages) or gets 401 (API).
// A signed-in user who is not staff gets a real 403 (pages and API). The layouts
// under app/internal and app/admin check again, so this is never the only lock.
// If the Clerk keys are missing the staff areas are closed completely (503)
// and the rest of the public site keeps working.

const clerkProxy = clerkMiddleware(async (auth, request) => {
  // Only staff-area paths need the (database) check; everything else passes straight through.
  if (!staffArea(request.nextUrl.pathname)) return;
  const { userId } = await auth();
  return staffGate(request.nextUrl, userId);
});

export default clerkConfigured
  ? clerkProxy
  : (request: NextRequest) => {
      const area = staffArea(request.nextUrl.pathname);
      if (area) return refusal(503, area, request.nextUrl);
      return NextResponse.next();
    };

export const config = {
  matcher: [
    // Skip Next.js internals and static files (including the hero video).
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|mp4)).*)",
    "/(api|trpc)(.*)",
  ],
};
