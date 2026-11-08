import { NextResponse } from "next/server";
import { clerkMiddleware } from "@clerk/nextjs/server";
import { clerkConfigured } from "@/lib/clerk-config";

// Makes Clerk's auth() available to pages and route handlers. Route protection
// itself is done in the pages (see app/dashboard/page.tsx), which Clerk
// recommends over blocking inside the proxy. If the Clerk keys are missing the
// proxy steps aside, so the public site keeps working.
export default clerkConfigured ? clerkMiddleware() : () => NextResponse.next();

export const config = {
  matcher: [
    // Skip Next.js internals and static files (including the hero video).
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|mp4)).*)",
    "/(api|trpc)(.*)",
  ],
};
