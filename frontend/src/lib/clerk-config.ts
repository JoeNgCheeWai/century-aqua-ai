/**
 * True when Clerk's keys are present. Without them Clerk throws on every
 * request, which would take the whole public site down. The app therefore
 * degrades instead: the homepage, the anonymous chat and /api/health keep
 * working, and the dashboard and sign-in pages explain that sign-in is not set up.
 */
export const clerkConfigured = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY,
);
