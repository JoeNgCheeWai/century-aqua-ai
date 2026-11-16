/**
 * The proxy's treatment of POST /api/mc/agents/{cao|coo|cto|cmo}, proven with no network, no Next and no database:
 *     node --test scripts/mission_control_agents_gate.test.mjs
 *
 * WHY THIS EXISTS. A 403 was seen on POST /api/mc/agents/cto with no function logs, while the CAO, COO and CMO worked. These tests pin what
 * THIS repository's gate does with those four paths: the decision is the same for all four, it is never a 403, and an unauthenticated one is a
 * 401 JSON. If a 403 still appears in production with no "[MIDDLEWARE] Request" log line, it was answered BEFORE this code (the Vercel firewall
 * or a platform rule), which no test here can reach.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createSessionToken, decideAccess, mcArea } from "../src/lib/mc-auth.ts";

const SECRET = "a".repeat(64);
const ENV = { ADMIN_PIN: "482913", ADMIN_SESSION_SECRET: SECRET };
const NOW = Date.UTC(2026, 10, 16, 12, 0, 0);
const AGENTS = ["cao", "coo", "cto", "cmo"];

test("all four agent paths are Mission Control API paths and are decided identically", () => {
  const token = createSessionToken(SECRET, NOW);
  for (const mode of [{ token, expect: "allow" }, { token: undefined, expect: "unauthenticated" }, { token: "v1.0.bad.bad", expect: "unauthenticated" }]) {
    const decisions = AGENTS.map((agent) => {
      const path = `/api/mc/agents/${agent}`;
      assert.equal(mcArea(path), "api", path);
      const d = decideAccess(path, mode.token, ENV, NOW);
      return d.type;
    });
    assert.deepEqual(decisions, AGENTS.map(() => mode.expect), JSON.stringify(mode.expect));
  }
});

test("no decision the gate can make for an agent path is a 403: it is allow, 401 or 503, never forbidden", () => {
  const token = createSessionToken(SECRET, NOW);
  const seen = new Set();
  for (const agent of AGENTS) {
    for (const [tok, env] of [[token, ENV], [undefined, ENV], [token, {}], [undefined, {}]]) seen.add(decideAccess(`/api/mc/agents/${agent}`, tok, env, NOW).type);
  }
  assert.deepEqual([...seen].sort(), ["allow", "unauthenticated", "unavailable"]);
});

test("odd spellings of the CTO path get the same decision as the others (no path trick is treated differently)", () => {
  const token = createSessionToken(SECRET, NOW);
  for (const path of ["/api/mc/agents/CTO", "/api/mc//agents/cto", "/api/mc/agents/%63to", "/API/MC/AGENTS/CTO"]) {
    assert.equal(mcArea(path), "api", path);
    assert.equal(decideAccess(path, token, ENV, NOW).type, "allow", path);
    assert.equal(decideAccess(path, undefined, ENV, NOW).type, "unauthenticated", path);
  }
});

test("the proxy source has no agent-specific branch and no 403 response of its own for Mission Control", () => {
  const proxy = readFileSync(new URL("../src/proxy.ts", import.meta.url), "utf8");
  const gate = readFileSync(new URL("../src/lib/mc-gate.ts", import.meta.url), "utf8");
  for (const [name, source] of [["proxy.ts", proxy], ["mc-gate.ts", gate]]) {
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    assert.ok(!/\bcto\b/i.test(code.replace(/AGENTS|agentId/g, "")), `${name} must not mention the CTO`);
    assert.ok(!/status:\s*403/.test(code), `${name} must not build a 403 for Mission Control`);
  }
  // The staff gate is the only code that can answer 403, and it is reached only for /internal and /admin paths (staffArea), never /api/mc.
  const staff = readFileSync(new URL("../src/lib/staff-gate.ts", import.meta.url), "utf8");
  assert.match(staff, /\^\\\/api\\\/\(internal\|admin\)/);
});

test("the proxy logs the path and method of every request and says so before it returns a 403, and never logs a cookie, header, query or body", () => {
  const proxy = readFileSync(new URL("../src/proxy.ts", import.meta.url), "utf8");
  assert.ok(proxy.includes('console.log("[MIDDLEWARE] Request:", { pathname: request.nextUrl.pathname, method: request.method })'));
  assert.ok(proxy.includes('console.log("[MIDDLEWARE] Returning 403"'));
  const logged = [...proxy.matchAll(/console\.log\(([^;]*)\);/g)].map((m) => m[1]).join(" ");
  for (const secret of ["cookie", "cookies", "headers", "search", "searchParams", "body", "authorization", "SESSION_COOKIE", "token"]) {
    assert.ok(!new RegExp(`\\b${secret}\\b`, "i").test(logged), `a log line mentions ${secret}`);
  }
});

test("the middleware matcher covers /api/mc/agents/cto (so the logging line WILL appear if the request reaches the proxy)", () => {
  const proxy = readFileSync(new URL("../src/proxy.ts", import.meta.url), "utf8");
  // Take the real matcher strings out of the source and apply them the way Next does (path-to-regexp style, anchored at both ends).
  const block = proxy.slice(proxy.indexOf("matcher: ["));
  const patterns = [...block.matchAll(/^\s*"(\/[^"]*)",?\s*$/gm)].map((m) => m[1].replace(/\\\\/g, "\\"));
  assert.ok(patterns.length >= 2, "found the matcher entries");
  const matches = (path) => patterns.some((pattern) => new RegExp(`^${pattern}$`).test(path));
  for (const agent of AGENTS) assert.ok(matches(`/api/mc/agents/${agent}`), `the proxy matcher does not cover /api/mc/agents/${agent}`);
  assert.ok(!matches("/_next/static/chunk.js"), "internals are skipped");
});
