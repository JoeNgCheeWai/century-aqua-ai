/**
 * Mission Control authentication (Phase 6): a single shared PIN, a signed expiring cookie.
 *
 * Deliberately free of Next and of "@/..." imports so it can be tested with plain `node --test`.
 *
 * HOW IT WORKS
 *   login   POST /api/mc/login {pin}: the PIN is compared in constant time with ADMIN_PIN. On success the server sets an
 *           httpOnly cookie holding   v1.<expiry>.<nonce>.<HMAC-SHA256>   signed with ADMIN_SESSION_SECRET.
 *   every   proxy.ts (first lock) and every /api/mc/* handler (the lock that guards the data) verify that cookie.
 *   logout  clears the cookie.
 *
 * WHY TWO SECRETS. A PIN is short, so it must not be the signing key: anyone holding one cookie could brute-force a short
 * PIN offline and forge cookies forever. ADMIN_SESSION_SECRET is a long random value that signs the cookie. Rotating it
 * signs everyone out (the cookie is stateless, so this is the only way to revoke one before it expires).
 *
 * FAIL CLOSED. If either value is missing, or too short, nobody can log in and nothing under Mission Control opens.
 *
 * KNOWN LIMITS (single user, internal use): the failed-attempt lockout lives in memory, so on a serverless host each
 * instance counts separately. A 6+ character PIN behind a lockout is fine for a private tool; it is not a substitute for
 * real accounts. Multi-user support is out of scope for this phase.
 */

import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const SESSION_COOKIE = "mc_session";
export const SESSION_TTL_SECONDS = 8 * 60 * 60;
export const MIN_PIN_LENGTH = 6;
export const MIN_SECRET_LENGTH = 32;
export const DEFAULT_NEXT = "/mission-control/dashboard";

const VERSION = "v1";

type Env = Record<string, string | undefined>;

/** True when both ADMIN_PIN and ADMIN_SESSION_SECRET are set and long enough. */
export function authConfigured(env: Env = process.env): boolean {
  return (env.ADMIN_PIN ?? "").length >= MIN_PIN_LENGTH && (env.ADMIN_SESSION_SECRET ?? "").length >= MIN_SECRET_LENGTH;
}

function signature(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("hex");
}

function sameText(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  return left.length === right.length && timingSafeEqual(left, right);
}

/** A new session token that expires `ttlSeconds` after `nowMs`. */
export function createSessionToken(secret: string, nowMs: number = Date.now(), ttlSeconds: number = SESSION_TTL_SECONDS): string {
  const expires = Math.floor(nowMs / 1000) + ttlSeconds;
  const payload = `${VERSION}.${expires}.${randomBytes(12).toString("hex")}`;
  return `${payload}.${signature(payload, secret)}`;
}

/** True only for an unexpired token signed with `secret`. Never throws. */
export function verifySessionToken(token: string | null | undefined, secret: string | null | undefined, nowMs: number = Date.now()): boolean {
  if (!token || !secret || secret.length < MIN_SECRET_LENGTH) return false;
  const parts = token.split(".");
  if (parts.length !== 4 || parts[0] !== VERSION) return false;
  const [version, expires, nonce, mac] = parts;
  if (!/^\d{1,12}$/.test(expires) || !/^[0-9a-f]{24}$/.test(nonce) || !/^[0-9a-f]{64}$/.test(mac)) return false;
  if (!sameText(mac, signature(`${version}.${expires}.${nonce}`, secret))) return false;
  return Number(expires) * 1000 > nowMs;
}

/** Constant-time PIN check. Both sides are hashed first so the comparison does not depend on the PIN's length. */
export function pinMatches(candidate: unknown, pin: string): boolean {
  if (typeof candidate !== "string" || candidate.length === 0 || candidate.length > 200) return false;
  const a = createHash("sha256").update(candidate).digest();
  const b = createHash("sha256").update(pin).digest();
  return timingSafeEqual(a, b);
}

/**
 * The page to open after login. Only a path inside Mission Control is accepted (never another site, never "//host"),
 * so the login page cannot be used as an open redirect.
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !/^\/mission-control(\/[A-Za-z0-9._~\-/]*)?$/.test(next) || next.includes("//")) return DEFAULT_NEXT;
  // A browser resolves "/mission-control/../admin" to "/admin", which would leave Mission Control. No dot segments at all.
  if (next.split("/").some((segment) => segment === "." || segment === "..")) return DEFAULT_NEXT;
  if (next === "/mission-control/login" || next === "/mission-control") return DEFAULT_NEXT;
  return next;
}

// ---------------------------------------------------------------------------------------------------------------------
// Which requests belong to Mission Control, and what to do with one that has no valid session. Pure (no Next imports), so
// it is tested with plain node. src/lib/mc-gate.ts turns the decision into a NextResponse for the proxy.
//   pages   /mission-control/*   (public: /mission-control/login)
//   API     /api/mc/*            (public: /api/mc/login and /api/mc/logout)
// ---------------------------------------------------------------------------------------------------------------------

export type McArea = "page" | "api" | null;

/** Percent-encoding, repeated slashes and upper case cannot slip past the match (same idea as the staff gate). */
export function normalizePath(pathname: string): string {
  let path = pathname;
  try {
    path = decodeURIComponent(path);
  } catch {
    // malformed escape: match on the raw text
  }
  return path.replace(/\/+/g, "/").toLowerCase();
}

export function mcArea(pathname: string): McArea {
  const path = normalizePath(pathname);
  if (/^\/api\/mc(\/|$)/.test(path)) return "api";
  if (/^\/mission-control(\/|$)/.test(path)) return "page";
  return null;
}

/** The requests that must work without a session: showing the login form, and logging in or out. */
export function isPublic(pathname: string): boolean {
  const path = normalizePath(pathname).replace(/\/$/, "");
  return path === "/mission-control/login" || path === "/api/mc/login" || path === "/api/mc/logout";
}

export type AccessDecision =
  | { type: "allow" }
  | { type: "unavailable"; area: "page" | "api" }
  | { type: "unauthenticated"; area: "page" | "api"; next: string };

export function decideAccess(
  pathname: string,
  token: string | undefined,
  env: Env = process.env,
  nowMs: number = Date.now(),
): AccessDecision {
  const area = mcArea(pathname);
  if (!area || isPublic(pathname)) return { type: "allow" };
  if (!authConfigured(env)) return { type: "unavailable", area };
  if (verifySessionToken(token, env.ADMIN_SESSION_SECRET, nowMs)) return { type: "allow" };
  return { type: "unauthenticated", area, next: safeNextPath(pathname) };
}

/** Lockout after repeated wrong PINs: `max` failures inside `windowMs` lock that caller out for `windowMs`. */
export class LoginThrottle {
  private failures = new Map<string, number[]>();
  private readonly max: number;
  private readonly windowMs: number;

  constructor(max = 5, windowMs = 15 * 60 * 1000) {
    this.max = max;
    this.windowMs = windowMs;
  }

  private recent(key: string, nowMs: number): number[] {
    const kept = (this.failures.get(key) ?? []).filter((t) => nowMs - t < this.windowMs);
    if (kept.length) this.failures.set(key, kept);
    else this.failures.delete(key);
    return kept;
  }

  check(key: string, nowMs: number = Date.now()): { allowed: boolean; retryAfterSeconds: number } {
    const kept = this.recent(key, nowMs);
    if (kept.length < this.max) return { allowed: true, retryAfterSeconds: 0 };
    return { allowed: false, retryAfterSeconds: Math.max(1, Math.ceil((kept[0] + this.windowMs - nowMs) / 1000)) };
  }

  fail(key: string, nowMs: number = Date.now()): void {
    this.failures.set(key, [...this.recent(key, nowMs), nowMs]);
    if (this.failures.size > 1000) this.failures.delete(this.failures.keys().next().value as string); // bound the memory
  }

  success(key: string): void {
    this.failures.delete(key);
  }
}
