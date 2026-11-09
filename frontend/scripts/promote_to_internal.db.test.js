/* eslint-disable @typescript-eslint/no-require-imports -- plain CommonJS test file */
/**
 * Runs promoteInDatabase against the REAL local Postgres (skips itself otherwise):
 *     node --test scripts/promote_to_internal.db.test.js
 *
 * SAFETY: only runs when DATABASE_URL points at localhost / 127.0.0.1, so it can never
 * write to a production database. It uses clerk ids starting with "test_promote_" and
 * removes every row it created.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const promote = require("./promote_to_internal.js");

const IDS = ["test_promote_free", "test_promote_new", "test_promote_banned"];

async function connectLocal() {
  const url = promote.readSetting("DATABASE_URL");
  let host = "";
  try {
    host = new URL(url).hostname;
  } catch {
    return null;
  }
  if (host !== "localhost" && host !== "127.0.0.1") return null;
  const client = new (require("pg").Client)({ connectionString: url, connectionTimeoutMillis: 5000 });
  client.on("error", () => {});
  try {
    await client.connect();
    await client.query('SELECT 1 FROM "User" LIMIT 1');
    return client;
  } catch {
    await client.end().catch(() => {});
    return null;
  }
}

async function cleanUp(db) {
  await db.query('DELETE FROM "AdminAuditLog" WHERE "targetId" = ANY($1)', [IDS]);
  await db.query('DELETE FROM "User" WHERE "clerkId" = ANY($1)', [IDS]);
}

test("promoteInDatabase on the real local database", async (t) => {
  const db = await connectLocal();
  if (!db) return t.skip("no local database reachable (npx prisma dev --detach --name caip-local)");
  try {
    await cleanUp(db);
    const soon = new Date(Date.now() + 3600000);
    await db.query(`INSERT INTO "User" ("id","clerkId","role","sessionResetTime","updatedAt") VALUES ('test_promote_id_1',$1,'free',$2,now())`, [IDS[0], soon]);
    await db.query(`INSERT INTO "User" ("id","clerkId","role","sessionResetTime","bannedAt","updatedAt") VALUES ('test_promote_id_3',$1,'pro',$2,now(),now())`, [IDS[2], soon]);

    const first = await promote.promoteInDatabase(db, IDS[0], "a@example.com");
    assert.deepEqual(first, { outcome: "promoted", previousRole: "free" });
    const row = (await db.query('SELECT "role", "bannedAt" FROM "User" WHERE "clerkId" = $1', [IDS[0]])).rows[0];
    assert.equal(row.role, "internal");
    assert.equal(row.bannedAt, null); // exactly what the staff gate requires

    const audit = (await db.query('SELECT "actorId","action","targetType","details" FROM "AdminAuditLog" WHERE "targetId" = $1', [IDS[0]])).rows;
    assert.equal(audit.length, 1);
    assert.equal(audit[0].actorId, "cli");
    assert.equal(audit[0].action, "promote_to_internal");
    assert.equal(audit[0].details.email, "a@example.com");
    assert.equal(audit[0].details.previousRole, "free");

    const second = await promote.promoteInDatabase(db, IDS[0], "a@example.com");
    assert.equal(second.outcome, "already");
    assert.equal((await db.query('SELECT count(*)::int AS n FROM "AdminAuditLog" WHERE "targetId" = $1', [IDS[0]])).rows[0].n, 1);

    const created = await promote.promoteInDatabase(db, IDS[1], "b@example.com");
    assert.equal(created.outcome, "created");
    const fresh = (await db.query('SELECT * FROM "User" WHERE "clerkId" = $1', [IDS[1]])).rows[0];
    assert.equal(fresh.role, "internal");
    assert.equal(fresh.messageCount, 0);
    assert.equal(fresh.tokenBalance, 0);
    assert.ok(fresh.sessionResetTime > new Date());
    assert.ok(fresh.id && fresh.updatedAt);

    await assert.rejects(promote.promoteInDatabase(db, IDS[2], "c@example.com"), /banned/);
    const banned = (await db.query('SELECT "role" FROM "User" WHERE "clerkId" = $1', [IDS[2]])).rows[0];
    assert.equal(banned.role, "pro"); // untouched
    assert.equal((await db.query('SELECT count(*)::int AS n FROM "AdminAuditLog" WHERE "targetId" = $1', [IDS[2]])).rows[0].n, 0);
  } finally {
    await cleanUp(db);
    await db.end();
  }
});
