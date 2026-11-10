/**
 * Tests for the Mission Control PIN login (src/lib/mc-auth.ts), no network and no database:
 *     node --test scripts/mission_control_auth.test.mjs
 * (Node 22.18+ runs the TypeScript file directly.) The whole flow over HTTP (login route, cookie, proxy) is checked
 * against a running server by scripts/mission_control_flow.check.mjs.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_NEXT,
  LoginThrottle,
  SESSION_TTL_SECONDS,
  authConfigured,
  createSessionToken,
  decideAccess,
  isPublic,
  mcArea,
  pinMatches,
  safeNextPath,
  verifySessionToken,
} from "../src/lib/mc-auth.ts";

const SECRET = "a".repeat(64);
const OTHER = "b".repeat(64);
const ENV = { ADMIN_PIN: "482913", ADMIN_SESSION_SECRET: SECRET };
const NOW = Date.UTC(2026, 10, 10, 12, 0, 0);

test("login is only configured when both the PIN and the signing secret are set and long enough", () => {
  assert.equal(authConfigured(ENV), true);
  assert.equal(authConfigured({}), false);
  assert.equal(authConfigured({ ADMIN_PIN: "482913" }), false);
  assert.equal(authConfigured({ ADMIN_SESSION_SECRET: SECRET }), false);
  assert.equal(authConfigured({ ADMIN_PIN: "123", ADMIN_SESSION_SECRET: SECRET }), false); // PIN too short
  assert.equal(authConfigured({ ADMIN_PIN: "482913", ADMIN_SESSION_SECRET: "short" }), false); // secret too short
});

test("the PIN is compared exactly, and anything that is not a string is refused", () => {
  assert.equal(pinMatches("482913", "482913"), true);
  assert.equal(pinMatches("482914", "482913"), false);
  assert.equal(pinMatches("4829130", "482913"), false);
  assert.equal(pinMatches("48291", "482913"), false);
  assert.equal(pinMatches("", "482913"), false);
  assert.equal(pinMatches(482913, "482913"), false);
  assert.equal(pinMatches(null, "482913"), false);
  assert.equal(pinMatches(["482913"], "482913"), false);
  assert.equal(pinMatches("x".repeat(500), "482913"), false);
});

test("a token verifies with its own secret until it expires", () => {
  const token = createSessionToken(SECRET, NOW);
  assert.equal(verifySessionToken(token, SECRET, NOW), true);
  assert.equal(verifySessionToken(token, SECRET, NOW + (SESSION_TTL_SECONDS - 5) * 1000), true);
  assert.equal(verifySessionToken(token, SECRET, NOW + SESSION_TTL_SECONDS * 1000 + 1000), false); // expired
});

test("a token signed with another secret, or edited, is refused", () => {
  const token = createSessionToken(SECRET, NOW);
  assert.equal(verifySessionToken(token, OTHER, NOW), false);
  const [version, expires, nonce, mac] = token.split(".");
  const later = String(Number(expires) + 99999);
  assert.equal(verifySessionToken([version, later, nonce, mac].join("."), SECRET, NOW), false); // extended expiry
  assert.equal(verifySessionToken([version, expires, nonce, "0".repeat(64)].join("."), SECRET, NOW), false);
  assert.equal(verifySessionToken(`v2.${expires}.${nonce}.${mac}`, SECRET, NOW), false);
});

test("malformed or empty tokens and secrets never verify and never throw", () => {
  for (const bad of [undefined, null, "", "garbage", "v1.1.2.3", "v1...", "v1.99999999999.zz.zz", "a.b.c.d.e", "\u{1F4A5}"]) {
    assert.equal(verifySessionToken(bad, SECRET, NOW), false, String(bad));
  }
  const token = createSessionToken(SECRET, NOW);
  assert.equal(verifySessionToken(token, "", NOW), false);
  assert.equal(verifySessionToken(token, undefined, NOW), false);
  assert.equal(verifySessionToken(token, "short", NOW), false); // a weak secret signs nothing
});

test("two tokens made at the same moment are different (a nonce), and both verify", () => {
  const a = createSessionToken(SECRET, NOW);
  const b = createSessionToken(SECRET, NOW);
  assert.notEqual(a, b);
  assert.equal(verifySessionToken(a, SECRET, NOW), true);
  assert.equal(verifySessionToken(b, SECRET, NOW), true);
});

test("only Mission Control paths are guarded, whatever the spelling", () => {
  assert.equal(mcArea("/mission-control/dashboard"), "page");
  assert.equal(mcArea("/mission-control"), "page");
  assert.equal(mcArea("/api/mc/overview"), "api");
  assert.equal(mcArea("/%6Dission-control/audit"), "page"); // percent-encoded
  assert.equal(mcArea("//mission-control//audit"), "page"); // repeated slashes
  assert.equal(mcArea("/MISSION-CONTROL/Audit"), "page"); // upper case
  assert.equal(mcArea("/API/MC/overview"), "api");
  for (const open of ["/", "/dashboard", "/internal", "/admin", "/api/chat", "/api/health", "/mission-controls", "/api/mcx"]) {
    assert.equal(mcArea(open), null, open);
  }
});

test("only the login page and the login and logout endpoints are public", () => {
  for (const p of ["/mission-control/login", "/mission-control/login/", "/api/mc/login", "/api/mc/logout", "/MISSION-CONTROL/LOGIN"]) {
    assert.equal(isPublic(p), true, p);
  }
  for (const p of ["/mission-control/dashboard", "/mission-control", "/api/mc/overview", "/mission-control/login/x", "/api/mc/login/x"]) {
    assert.equal(isPublic(p), false, p);
  }
});

test("without a session a page is sent to login and an API call gets 401", () => {
  assert.deepEqual(decideAccess("/mission-control/audit", undefined, ENV, NOW), { type: "unauthenticated", area: "page", next: "/mission-control/audit" });
  assert.deepEqual(decideAccess("/api/mc/overview", undefined, ENV, NOW), { type: "unauthenticated", area: "api", next: DEFAULT_NEXT });
  assert.equal(decideAccess("/mission-control/audit", "forged.token", ENV, NOW).type, "unauthenticated");
  assert.equal(decideAccess("/api/mc/audit-logs", createSessionToken(OTHER, NOW), ENV, NOW).type, "unauthenticated");
});

test("with a valid session everything under Mission Control is allowed, and the public paths always are", () => {
  const token = createSessionToken(SECRET, NOW);
  for (const p of ["/mission-control/dashboard", "/api/mc/overview", "/mission-control/council/abc"]) {
    assert.deepEqual(decideAccess(p, token, ENV, NOW), { type: "allow" }, p);
  }
  assert.deepEqual(decideAccess("/mission-control/login", undefined, ENV, NOW), { type: "allow" });
  assert.deepEqual(decideAccess("/api/mc/login", undefined, ENV, NOW), { type: "allow" });
  assert.deepEqual(decideAccess("/dashboard", undefined, ENV, NOW), { type: "allow" }); // not ours: Clerk's business
});

test("an expired session is refused", () => {
  const token = createSessionToken(SECRET, NOW);
  assert.equal(decideAccess("/api/mc/overview", token, ENV, NOW + (SESSION_TTL_SECONDS + 60) * 1000).type, "unauthenticated");
});

test("if login is not configured everything private is closed (503), even with a token", () => {
  const token = createSessionToken(SECRET, NOW);
  assert.deepEqual(decideAccess("/mission-control/dashboard", token, {}, NOW), { type: "unavailable", area: "page" });
  assert.deepEqual(decideAccess("/api/mc/overview", token, { ADMIN_PIN: "482913" }, NOW), { type: "unavailable", area: "api" });
  assert.deepEqual(decideAccess("/mission-control/login", undefined, {}, NOW), { type: "allow" }); // the form still shows
});

test("the page to open after login stays inside Mission Control (no open redirect)", () => {
  assert.equal(safeNextPath("/mission-control/audit"), "/mission-control/audit");
  const id = "/mission-control/council/123e4567-e89b-12d3-a456-426614174000";
  assert.equal(safeNextPath(id), id);
  const bad = ["https://evil.example", "//evil.example", "/\\evil.example", "/dashboard", "/mission-control/../admin",
    "/mission-control//evil", "javascript:alert(1)", "", null, undefined, "/mission-control/login", "/mission-control"];
  for (const next of bad) assert.equal(safeNextPath(next), DEFAULT_NEXT, String(next));
});

test("five wrong PINs lock a caller out, the lockout ends, and a success clears the count", () => {
  const throttle = new LoginThrottle(5, 60_000);
  for (let i = 0; i < 4; i++) throttle.fail("ip", NOW + i);
  assert.equal(throttle.check("ip", NOW + 10).allowed, true);
  throttle.fail("ip", NOW + 4);
  const locked = throttle.check("ip", NOW + 10);
  assert.equal(locked.allowed, false);
  assert.ok(locked.retryAfterSeconds >= 1 && locked.retryAfterSeconds <= 60);
  assert.equal(throttle.check("someone-else", NOW + 10).allowed, true); // per caller
  assert.equal(throttle.check("ip", NOW + 61_000).allowed, true); // the window passed
  throttle.success("ip");
  assert.equal(throttle.check("ip", NOW + 10).allowed, true);
});

