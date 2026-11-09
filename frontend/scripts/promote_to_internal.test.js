/* eslint-disable @typescript-eslint/no-require-imports -- plain CommonJS test file */
/**
 * Tests for scripts/promote_to_internal.js using fakes only (no network, no database):
 *     node --test scripts/promote_to_internal.test.js
 * The real-database test is in promote_to_internal.db.test.js.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { PassThrough } = require("node:stream");
const promote = require("./promote_to_internal.js");

// Fake on purpose (underscore-separated words, not a real key shape).
const SECRET = "sk_test_" + "DUMMY_FAKE_FOR_TESTS_ONLY_0001";
const EMAIL = "staff@example.com";

function reply(status, body, seen) {
  return async (url, init) => {
    if (seen) {
      seen.url = String(url);
      seen.headers = init.headers;
    }
    return { status, ok: status >= 200 && status < 300, json: async () => body };
  };
}
const clerkUser = (id, ...emails) => ({ id, first_name: "Ada", last_name: "Lovelace", email_addresses: emails.map((e) => ({ email_address: e })) });

function fakeClient(row, failOn) {
  const calls = [];
  return {
    calls,
    async query(sql, params) {
      calls.push({ sql, params });
      if (failOn && failOn.test(sql)) throw new Error("boom");
      if (/^SELECT/.test(sql)) return { rows: row ? [row] : [] };
      return { rows: [] };
    },
    async end() {
      calls.push({ sql: "END" });
    },
  };
}
const kinds = (client) => client.calls.map((c) => c.sql.split(" ")[0] + (c.sql.includes('"AdminAuditLog"') ? ":audit" : ""));

test("email cleaning and validation", () => {
  assert.equal(promote.cleanEmail("  Staff@Example.COM \n"), EMAIL);
  assert.equal(promote.cleanEmail("<staff@example.com>"), EMAIL);
  assert.equal(promote.cleanEmail('"staff@example.com"'), EMAIL);
  assert.equal(promote.cleanEmail("mailto:staff@example.com"), EMAIL);
  assert.equal(promote.cleanEmail(undefined), "");
  for (const ok of [EMAIL, "a.b+tag@sub.example.co"]) assert.ok(promote.isValidEmail(ok), ok);
  for (const bad of ["", "nope", "a@b", "a b@example.com", "@example.com", "a@@example.com"]) assert.ok(!promote.isValidEmail(bad), bad);
});

test("findClerkUser: request shape, both response shapes, local re-check", async () => {
  const seen = {};
  const found = await promote.findClerkUser(EMAIL, SECRET, reply(200, [clerkUser("user_1", "Staff@Example.com")], seen));
  assert.deepEqual(found, { id: "user_1", name: "Ada Lovelace" });
  assert.ok(seen.url.startsWith("https://api.clerk.com/v1/users?"));
  assert.ok(seen.url.includes("email_address=staff%40example.com"));
  assert.equal(seen.headers.Authorization, `Bearer ${SECRET}`);

  const wrapped = await promote.findClerkUser(EMAIL, SECRET, reply(200, { data: [clerkUser("user_2", EMAIL)] }));
  assert.equal(wrapped.id, "user_2");
  assert.equal(await promote.findClerkUser(EMAIL, SECRET, reply(200, [])), null);
  // A loose server-side filter must never promote somebody else.
  assert.equal(await promote.findClerkUser(EMAIL, SECRET, reply(200, [clerkUser("user_9", "other@example.com")])), null);
  await assert.rejects(promote.findClerkUser(EMAIL, SECRET, reply(200, [clerkUser("a", EMAIL), clerkUser("b", EMAIL)])), /More than one/);
});

test("findClerkUser: failures give clear messages and never echo the key", async () => {
  const cases = [
    [reply(401, {}), /CLERK_SECRET_KEY/],
    [reply(403, {}), /CLERK_SECRET_KEY/],
    [reply(429, {}), /rate-limiting/],
    [reply(500, {}), /HTTP 500/],
    [async () => { throw new Error(`socket hang up ${SECRET}`); }, /Could not reach Clerk/],
    [async () => ({ status: 200, ok: true, json: async () => { throw new Error("bad json"); } }), /could not be read/],
  ];
  for (const [fetchFn, pattern] of cases) {
    await assert.rejects(promote.findClerkUser(EMAIL, SECRET, fetchFn), (error) => pattern.test(error.message) && !error.message.includes(SECRET));
  }
});

test("promoteInDatabase: free user is promoted and audited in one transaction", async () => {
  const client = fakeClient({ role: "free", bannedAt: null });
  const result = await promote.promoteInDatabase(client, "user_1", EMAIL);
  assert.deepEqual(result, { outcome: "promoted", previousRole: "free" });
  assert.deepEqual(kinds(client), ["BEGIN", "SELECT", "UPDATE", "INSERT:audit", "COMMIT"]);
  assert.ok(client.calls[1].sql.includes("FOR UPDATE"));
  assert.deepEqual(client.calls[3].params.length, 2);
  assert.equal(JSON.parse(client.calls[3].params[1]).previousRole, "free");
});

test("promoteInDatabase: unknown account is created; already-internal changes nothing", async () => {
  const created = fakeClient(undefined);
  assert.equal((await promote.promoteInDatabase(created, "user_2", EMAIL)).outcome, "created");
  assert.deepEqual(kinds(created), ["BEGIN", "SELECT", "INSERT", "INSERT:audit", "COMMIT"]);

  const same = fakeClient({ role: "internal", bannedAt: null });
  assert.equal((await promote.promoteInDatabase(same, "user_3", EMAIL)).outcome, "already");
  assert.deepEqual(kinds(same), ["BEGIN", "SELECT", "COMMIT"]); // no write, no audit line
});

test("promoteInDatabase: banned accounts are refused and rolled back; errors roll back", async () => {
  const banned = fakeClient({ role: "pro", bannedAt: new Date() });
  await assert.rejects(promote.promoteInDatabase(banned, "user_4", EMAIL), /banned/);
  assert.deepEqual(kinds(banned), ["BEGIN", "SELECT", "ROLLBACK"]);

  const failing = fakeClient({ role: "free", bannedAt: null }, /^UPDATE/);
  await assert.rejects(promote.promoteInDatabase(failing, "user_5", EMAIL), /boom/);
  assert.equal(kinds(failing).at(-1), "ROLLBACK");
  assert.ok(!kinds(failing).includes("COMMIT"));
});
