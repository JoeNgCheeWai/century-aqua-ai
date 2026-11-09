/**
 * Who may see /internal and /admin.
 *
 * Both areas are for staff only. "Staff" means the signed-in user's row in OUR
 * database has role 'internal' and is not banned. The role is read from the
 * database on every call (never from a token), so promoting or demoting someone
 * takes effect immediately.
 *
 * Failing closed: if the database cannot be reached the answer is "unavailable",
 * which every caller turns into a refusal, never into access.
 */

import { getPrisma } from "@/lib/prisma";
import type { RoleName } from "@/lib/plans";

export type StaffCheck =
  | { ok: true; clerkId: string }
  | { ok: false; reason: "signed_out" | "not_staff" | "banned" | "unavailable" };

/** Pure decision from a database row, so it can be tested without a database. */
export function decideStaffAccess(
  row: { role: RoleName; bannedAt: Date | null } | null,
  clerkId: string,
): StaffCheck {
  if (!row) return { ok: false, reason: "not_staff" };
  if (row.bannedAt) return { ok: false, reason: "banned" };
  if (row.role !== "internal") return { ok: false, reason: "not_staff" };
  return { ok: true, clerkId };
}

/**
 * Check a Clerk user id. Never creates a database row: a stranger visiting
 * /admin must not become a user just by asking.
 */
export async function checkStaff(clerkId: string | null | undefined): Promise<StaffCheck> {
  if (!clerkId) return { ok: false, reason: "signed_out" };
  try {
    const row = await getPrisma().user.findUnique({
      where: { clerkId },
      select: { role: true, bannedAt: true },
    });
    return decideStaffAccess(row, clerkId);
  } catch (error) {
    console.error("access: staff check failed:", (error as Error).name);
    return { ok: false, reason: "unavailable" };
  }
}

/** HTTP status for a refusal. */
export function statusFor(reason: Exclude<StaffCheck, { ok: true }>["reason"]): number {
  switch (reason) {
    case "signed_out":
      return 401;
    case "unavailable":
      return 503;
    default:
      return 403;
  }
}
