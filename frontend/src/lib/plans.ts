/**
 * Plan limits and usage types for the rate limiter.
 *
 * Pure data with no server-only imports, so the dashboard UI can import it too.
 * Source of truth for a user's plan is the `role` column in the database
 * (prisma/schema.prisma). Change the numbers here to refine the limits.
 */

export const ROLES = ["free", "pro", "research", "enterprise", "internal"] as const;
export type RoleName = (typeof ROLES)[number];

export interface PlanLimit {
  /** Messages allowed per session window. */
  messages: number;
  /** Length of one session window in milliseconds. */
  windowMs: number;
}

const HOUR_MS = 60 * 60 * 1000;

export const FREE_PLAN: PlanLimit = { messages: 10, windowMs: 4 * HOUR_MS };
export const PAID_PLAN: PlanLimit = { messages: 100, windowMs: 2 * HOUR_MS };

/** The limit for a role, or null when the role is unmetered (internal staff). */
export function limitFor(role: RoleName): PlanLimit | null {
  switch (role) {
    case "free":
      return FREE_PLAN;
    case "pro":
    case "research":
    case "enterprise":
      return PAID_PLAN;
    case "internal":
      return null;
  }
}

export const ROLE_LABELS: Record<RoleName, string> = {
  free: "Free plan",
  pro: "Pro plan",
  research: "Research plan",
  enterprise: "Enterprise plan",
  internal: "Internal",
};

/** A user's current usage. JSON-safe, so it can be passed to client components. */
export interface Usage {
  role: RoleName;
  unlimited: boolean;
  limit: number | null;
  used: number;
  remaining: number | null;
  /** ISO time when the current window ends; null if no window is running. */
  resetTime: string | null;
}

export interface ConsumeResult extends Usage {
  /** False when the limit was already reached and no message was taken. */
  allowed: boolean;
}
