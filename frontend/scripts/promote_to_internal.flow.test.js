/* eslint-disable @typescript-eslint/no-require-imports -- plain CommonJS test file */
/**
 * End-to-end flow tests for scripts/promote_to_internal.js (fake Clerk, fake database):
 *     node --test scripts/promote_to_internal.flow.test.js
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const { PassThrough } = require("node:stream");
const promote = require("./promote_to_internal.js");

const SECRET = "sk_test_" + "DUMMY_FAKE_FOR_TESTS_ONLY_0001";
const EMAIL = "staff@example.com";
const NO_ENV_FILE = path.join(os.tmpdir(), "does-not-exist", ".env"); // never read the real .env
const OK_ENV = { CLERK_SECRET_KEY: SECRET, DATABASE_URL: "postgres://x:y@localhost:1/z" };

const clerkReply = (users) => async () => ({ status: 200, ok: true, json: async () => users });
const found = clerkReply([{ id: "user_1", first_name: "Ada", last_name: "", email_addresses: [{ email_address: EMAIL }] }]);

function fakeDb(row) {
  const calls = [];
  const client = {
    calls,
    async query(sql) {
      calls.push(sql.split(" ")[0]);
      return { rows: /^SELECT/.test(sql) && row ? [row] : [] };
    },
    async end() {
      client.ended = true;
    },
  };
  return client;
}

async function runWith(answers, options = {}) {
  const input = new PassThrough();
  const output = new PassThrough();
  let text = "";
  output.on("data", (chunk) => (text += chunk));
  const promise = promote.run({ input, output, envPath: NO_ENV_FILE, env: OK_ENV, fetchFn: found, ...options });
  for (const answer of answers) input.write(answer + "\n");
  input.end();
  return { code: await promise, text };
}

test("success prints the exact message and closes the database", async () => {
  const db = fakeDb({ role: "free", bannedAt: null });
  const { code, text } = await runWith([EMAIL], { connect: async () => db });
  assert.equal(code, 0);
  assert.ok(text.includes("Enter the email address of the account to promote to internal: "));
  assert.ok(text.includes(`✅ Success! User ${EMAIL} is now an internal staff member. Hard refresh your browser (Ctrl+F5) and visit /admin or /internal.`));
  assert.deepEqual(db.calls, ["BEGIN", "SELECT", "UPDATE", "INSERT", "COMMIT"]);
  assert.equal(db.ended, true);
});

test("a never-seen account is created; an already-internal one says so", async () => {
  const created = await runWith([EMAIL], { connect: async () => fakeDb(undefined) });
  assert.equal(created.code, 0);
  assert.match(created.text, /Success! User staff@example\.com is now an internal staff member/);
  assert.match(created.text, /database record was created/);
  const again = await runWith([EMAIL], { connect: async () => fakeDb({ role: "internal", bannedAt: null }) });
  assert.equal(again.code, 0);
  assert.match(again.text, /already an internal staff member/);
});

test("email is cleaned (case, quotes) and a bad one is asked again", async () => {
  const db = fakeDb({ role: "free", bannedAt: null });
  const { code, text } = await runWith(["nope", "", '  "Staff@Example.com" '], { connect: async () => db });
  assert.equal(code, 0);
  assert.equal((text.match(/is not a valid email address/g) || []).length, 2);
  assert.match(text, /Success! User staff@example\.com/);
});

test("too many bad emails, or closed input, change nothing and never call Clerk or the database", async () => {
  let touched = false;
  const trap = async () => {
    touched = true;
    throw new Error("must not be called");
  };
  const bad = await runWith(["a", "b", "c"], { connect: trap, fetchFn: trap });
  assert.equal(bad.code, 2);
  assert.match(bad.text, /Too many invalid attempts/);
  const cancelled = await runWith([], { connect: trap, fetchFn: trap });
  assert.equal(cancelled.code, 130);
  assert.match(cancelled.text, /Cancelled\. Nothing was changed/);
  assert.equal(touched, false);
});

test("an email that is not in Clerk gives a clear error and touches no database", async () => {
  let connected = false;
  const { code, text } = await runWith([EMAIL], { fetchFn: clerkReply([]), connect: async () => { connected = true; return fakeDb(); } });
  assert.equal(code, 1);
  assert.match(text, /No account with the email staff@example\.com exists in your Clerk app/);
  assert.match(text, /\/sign-up/);
  assert.equal(connected, false);
});

test("missing settings are reported before any prompt", async () => {
  for (const env of [{ DATABASE_URL: OK_ENV.DATABASE_URL }, { CLERK_SECRET_KEY: "pk_test_wrong", DATABASE_URL: OK_ENV.DATABASE_URL }]) {
    const { code, text } = await runWith([EMAIL], { env });
    assert.equal(code, 1);
    assert.match(text, /CLERK_SECRET_KEY is missing/);
    assert.ok(!text.includes("Enter the email address"));
  }
  const noDb = await runWith([EMAIL], { env: { CLERK_SECRET_KEY: SECRET } });
  assert.equal(noDb.code, 1);
  assert.match(noDb.text, /DATABASE_URL is missing/);
});

test("database problems explain what to do", async () => {
  const fail = (code) => async () => {
    throw Object.assign(new Error("x"), { code });
  };
  // connect() itself rejects: the script must still print the helpful message (no crash on cleanup)
  const down = await runWith([EMAIL], { connect: fail("ECONNREFUSED") });
  assert.equal(down.code, 1);
  assert.match(down.text, /npx prisma dev --detach --name caip-local/);
  const noTables = await runWith([EMAIL], { connect: async () => ({ query: fail("42P01"), end: async () => {} }) });
  assert.match(noTables.text, /prisma migrate deploy/);
  const banned = await runWith([EMAIL], { connect: async () => fakeDb({ role: "pro", bannedAt: new Date() }) });
  assert.equal(banned.code, 1);
  assert.match(banned.text, /is banned\. Nothing was changed/);
});
