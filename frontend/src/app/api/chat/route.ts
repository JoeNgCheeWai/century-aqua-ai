/**
 * POST /api/chat: rate-limited front door to the FastAPI backend.
 *
 * Signed-in users are metered by plan (see src/lib/plans.ts). Anonymous
 * visitors are NOT metered yet (they have no identity to meter); they are
 * forwarded unchanged, exactly as before.
 *
 * The browser only ever talks to this route, so the limit cannot be skipped by
 * calling the backend through the site. (next.config.ts proxies nothing but
 * /api/health to the backend.)
 */

import { auth } from "@clerk/nextjs/server";
import { clerkConfigured } from "@/lib/clerk-config";
import type { Usage } from "@/lib/plans";
import { consumeMessage, refundMessage } from "@/lib/usage";

const BACKEND_URL = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "");
// Render's free tier can take ~30-60 s to wake up; give the backend that long.
const BACKEND_TIMEOUT_MS = 60_000;
const MAX_MESSAGE_CHARS = 4000;
const MAX_HISTORY = 20;

interface HistoryItem {
  role: string;
  content: string;
}

function parseBody(raw: unknown): { message: string; history: HistoryItem[] } | null {
  if (typeof raw !== "object" || raw === null) return null;
  const { message, history } = raw as { message?: unknown; history?: unknown };
  if (typeof message !== "string" || !message.trim() || message.length > MAX_MESSAGE_CHARS) {
    return null;
  }
  if (history !== undefined && !Array.isArray(history)) return null;

  const items: HistoryItem[] = [];
  for (const entry of (history ?? []) as unknown[]) {
    if (typeof entry !== "object" || entry === null) return null;
    const { role, content } = entry as { role?: unknown; content?: unknown };
    if (typeof role !== "string" || typeof content !== "string") return null;
    items.push({ role, content });
  }
  return { message, history: items.slice(-MAX_HISTORY) };
}

function errorResponse(error: string, status: number, extra: Record<string, unknown> = {}) {
  return Response.json({ error, ...extra }, { status });
}

async function safeRefund(userId: string | null) {
  if (!userId) return;
  try {
    await refundMessage(userId);
  } catch (error) {
    console.error("chat: could not refund message:", (error as Error).name);
  }
}

export async function POST(request: Request) {
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return errorResponse("INVALID_REQUEST", 400);
  }
  const body = parseBody(raw);
  if (!body) return errorResponse("INVALID_REQUEST", 400);

  // Without Clerk keys nobody can be signed in, so everyone is treated as anonymous.
  const userId = clerkConfigured ? (await auth()).userId : null;

  // 1. Take one message from the signed-in user's allowance.
  let usage: Usage | undefined;
  if (userId) {
    try {
      const result = await consumeMessage(userId);
      if (!result.allowed) {
        const resetTime = result.resetTime ?? new Date().toISOString();
        const retryAfter = Math.max(
          1,
          Math.ceil((new Date(resetTime).getTime() - Date.now()) / 1000),
        );
        return Response.json(
          { error: "RATE_LIMIT_EXCEEDED", resetTime },
          { status: 429, headers: { "Retry-After": String(retryAfter) } },
        );
      }
      usage = result;
    } catch (error) {
      // Fail closed: if usage cannot be checked we cannot enforce the limit.
      console.error("chat: usage check failed:", (error as Error).name);
      return errorResponse("USAGE_UNAVAILABLE", 503);
    }
  }

  // 2. Forward to the backend.
  let backend: Response;
  try {
    backend = await fetch(`${BACKEND_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS),
    });
  } catch (error) {
    console.error("chat: backend unreachable:", (error as Error).name);
    await safeRefund(userId);
    return errorResponse("BACKEND_UNREACHABLE", 502);
  }

  if (!backend.ok) {
    // The user got no answer, so do not charge them for it.
    await safeRefund(userId);
    return errorResponse("BACKEND_ERROR", 502, { status: backend.status });
  }

  let data: Record<string, unknown>;
  try {
    data = (await backend.json()) as Record<string, unknown>;
  } catch {
    await safeRefund(userId);
    return errorResponse("BACKEND_ERROR", 502);
  }

  // 3. Return the answer, plus the user's updated allowance when signed in.
  return Response.json(usage ? { ...data, usage } : data);
}
