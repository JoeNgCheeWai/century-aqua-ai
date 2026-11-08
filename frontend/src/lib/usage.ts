/**
 * Per-user message metering, backed by the Prisma `User` table.
 *
 * Window rules
 *  - A session window starts with the first message after the previous window
 *    ended (or after sign-up). `sessionResetTime` is when it ends.
 *  - When it ends, the count starts again from zero.
 *
 * Concurrency: taking a message is a conditional SQL UPDATE
 * (`messageCount < limit`), so parallel requests can never exceed the limit.
 */

import type { PrismaClient, User } from "@/generated/prisma/client";
import { getPrisma } from "@/lib/prisma";
import {
  FREE_PLAN,
  limitFor,
  type ConsumeResult,
  type RoleName,
  type Usage,
} from "@/lib/plans";

type Db = Pick<PrismaClient, "user">;

/** Work out usage from a stored row. Pure: no database access. */
export function usageFrom(
  user: Pick<User, "role" | "messageCount" | "sessionResetTime">,
  now: Date,
): Usage {
  const role: RoleName = user.role;
  const plan = limitFor(role);
  if (!plan) {
    return { role, unlimited: true, limit: null, used: 0, remaining: null, resetTime: null };
  }
  // A window is only running if something was used and it has not ended yet.
  const active = user.messageCount > 0 && user.sessionResetTime.getTime() > now.getTime();
  const used = active ? user.messageCount : 0;
  return {
    role,
    unlimited: false,
    limit: plan.messages,
    used,
    remaining: Math.max(0, plan.messages - used),
    resetTime: active ? user.sessionResetTime.toISOString() : null,
  };
}

/** The user's row, created as a free user on first sight (safe under concurrency). */
export async function getOrCreateUser(
  clerkId: string,
  now: Date = new Date(),
  db: Db = getPrisma(),
): Promise<User> {
  return db.user.upsert({
    where: { clerkId },
    update: {},
    create: {
      clerkId,
      sessionResetTime: new Date(now.getTime() + FREE_PLAN.windowMs),
    },
  });
}

/** Current usage for display. Never consumes a message. */
export async function getUsage(
  clerkId: string,
  now: Date = new Date(),
  db: Db = getPrisma(),
): Promise<Usage> {
  return usageFrom(await getOrCreateUser(clerkId, now, db), now);
}

/**
 * Take one message from the user's allowance.
 * `allowed` is false (and nothing is taken) when the limit is already reached.
 */
export async function consumeMessage(
  clerkId: string,
  now: Date = new Date(),
  db: Db = getPrisma(),
): Promise<ConsumeResult> {
  const user = await getOrCreateUser(clerkId, now, db);
  const plan = limitFor(user.role);
  if (!plan) {
    return { allowed: true, ...usageFrom(user, now) };
  }

  // 1. Start a fresh window if the old one ended, or if nothing has been used yet.
  await db.user.updateMany({
    where: {
      clerkId,
      OR: [{ sessionResetTime: { lte: now } }, { messageCount: 0 }],
    },
    data: {
      messageCount: 0,
      sessionResetTime: new Date(now.getTime() + plan.windowMs),
    },
  });

  // 2. Take a message only while under the limit (atomic in SQL).
  const taken = await db.user.updateMany({
    where: { clerkId, messageCount: { lt: plan.messages } },
    data: { messageCount: { increment: 1 } },
  });

  const fresh = await db.user.findUniqueOrThrow({ where: { clerkId } });
  return { allowed: taken.count === 1, ...usageFrom(fresh, now) };
}

/** Give a message back (used when the backend failed, so the user is not charged). */
export async function refundMessage(clerkId: string, db: Db = getPrisma()): Promise<void> {
  await db.user.updateMany({
    where: { clerkId, messageCount: { gt: 0 } },
    data: { messageCount: { decrement: 1 } },
  });
}
