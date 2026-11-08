import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "@/generated/prisma/client";

// One client per server instance (reused across hot reloads and warm serverless calls).
const globalForPrisma = globalThis as unknown as { __caipPrisma?: PrismaClient };

/**
 * The shared Prisma client. Created lazily so that importing this module (for
 * example during `next build`) never needs a database.
 *
 * Throws if DATABASE_URL is not set. For Neon, use the pooled connection string.
 */
export function getPrisma(): PrismaClient {
  if (globalForPrisma.__caipPrisma) return globalForPrisma.__caipPrisma;

  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set");
  }

  const client = new PrismaClient({
    adapter: new PrismaPg({ connectionString, max: 5 }),
  });
  globalForPrisma.__caipPrisma = client;
  return client;
}
