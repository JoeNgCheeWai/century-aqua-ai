#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports -- plain CommonJS Node script */
/**
 * Promote an account to role 'internal' (staff) so it can open /internal and /admin.
 *
 * Run from the frontend folder:
 *     node scripts/promote_to_internal.js
 *
 * You type the account's email address. The database stores only Clerk ids (not
 * emails), so the script asks Clerk's API which account owns that email, then
 * sets that account's role in the database. If the account has no database row
 * yet (it never opened the dashboard), the row is created. The change is written
 * together with a line in AdminAuditLog (actor "cli").
 *
 * Needs in frontend/.env: CLERK_SECRET_KEY and DATABASE_URL. The local database
 * must be running (npx prisma dev --detach --name caip-local).
 *
 * This writes with the `pg` driver rather than Prisma Client: the generated
 * client is TypeScript, which a plain Node script cannot load.
 *
 * Exit codes: 0 promoted (or already internal), 1 failed, 2 invalid input,
 * 130 cancelled.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const readline = require("node:readline");

const ENV_PATH = path.join(__dirname, "..", ".env");
const CLERK_API = "https://api.clerk.com/v1";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_ATTEMPTS = 3;
const FREE_WINDOW_MS = 4 * 60 * 60 * 1000; // same as the free plan window in src/lib/plans.ts

/** An error whose message is safe and useful to print as-is. */
class PromoteError extends Error {
  constructor(message, code = 1) {
    super(message);
    this.exitCode = code;
  }
}

/** Trim, drop quotes / <> / "mailto:", lower-case. */
function cleanEmail(raw) {
  return String(raw ?? "")
    .trim()
    .replace(/^mailto:/i, "")
    .replace(/^["'<`]+|["'>`]+$/g, "")
    .trim()
    .toLowerCase();
}

function isValidEmail(email) {
  return EMAIL_RE.test(email) && email.length <= 254;
}

/** A setting from the environment, else from the .env file. Never printed. */
function readSetting(name, envPath = ENV_PATH, env = process.env) {
  if (env[name]) return env[name];
  try {
    return require("dotenv").parse(fs.readFileSync(envPath))[name] || "";
  } catch {
    return "";
  }
}

/**
 * Ask Clerk which account owns this email. Returns { id, name } or null.
 * The match is re-checked locally, so a loose server-side filter can never
 * promote the wrong person.
 */
async function findClerkUser(rawEmail, secretKey, fetchFn = fetch) {
  const email = cleanEmail(rawEmail); // Clerk's filter is case-sensitive and stores lower case
  const url = `${CLERK_API}/users?${new URLSearchParams({ email_address: email, limit: "10" })}`;
  let response;
  try {
    response = await fetchFn(url, {
      headers: { Authorization: `Bearer ${secretKey}`, Accept: "application/json" },
      signal: AbortSignal.timeout(20000),
    });
  } catch {
    throw new PromoteError("Could not reach Clerk. Check your internet connection (and any VPN or firewall) and try again.");
  }
  if (response.status === 401 || response.status === 403) {
    throw new PromoteError("Clerk rejected CLERK_SECRET_KEY in frontend/.env (wrong, revoked, or from a different Clerk app). Run: node scripts/setup_clerk.js");
  }
  if (response.status === 429) throw new PromoteError("Clerk is rate-limiting requests. Wait a minute and try again.");
  if (!response.ok) throw new PromoteError(`Clerk returned an unexpected error (HTTP ${response.status}). Try again in a moment.`);

  let data;
  try {
    data = await response.json();
  } catch {
    throw new PromoteError("Clerk sent a response that could not be read. Try again in a moment.");
  }
  const list = Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : [];
  const matches = list.filter((user) =>
    (user.email_addresses || []).some((entry) => String(entry.email_address).toLowerCase() === email),
  );
  if (matches.length === 0) return null;
  if (matches.length > 1) throw new PromoteError(`More than one Clerk account matches ${email}. Nothing was changed.`);
  const user = matches[0];
  return { id: user.id, name: [user.first_name, user.last_name].filter(Boolean).join(" ") };
}

/**
 * Set the account's role to 'internal' in one transaction.
 * Returns { outcome: 'promoted' | 'created' | 'already', previousRole }.
 * Throws PromoteError for a banned account (it is never silently un-banned).
 */
async function promoteInDatabase(client, clerkId, email, now = new Date()) {
  await client.query("BEGIN");
  try {
    const found = await client.query('SELECT "role", "bannedAt" FROM "User" WHERE "clerkId" = $1 FOR UPDATE', [clerkId]);
    const row = found.rows[0];
    if (row && row.bannedAt) {
      throw new PromoteError(`The account for ${email} is banned. Nothing was changed. Un-ban it first.`);
    }
    let outcome;
    if (!row) {
      await client.query(
        'INSERT INTO "User" ("id", "clerkId", "role", "sessionResetTime", "updatedAt") VALUES ($1, $2, \'internal\', $3, $4)',
        [crypto.randomUUID(), clerkId, new Date(now.getTime() + FREE_WINDOW_MS), now],
      );
      outcome = "created";
    } else if (row.role === "internal") {
      outcome = "already";
    } else {
      await client.query('UPDATE "User" SET "role" = \'internal\', "updatedAt" = $2 WHERE "clerkId" = $1', [clerkId, now]);
      outcome = "promoted";
    }
    if (outcome !== "already") {
      await client.query(
        'INSERT INTO "AdminAuditLog" ("actorId", "action", "targetType", "targetId", "details") VALUES (\'cli\', \'promote_to_internal\', \'User\', $1, $2)',
        [clerkId, JSON.stringify({ email, previousRole: row ? row.role : null, via: "scripts/promote_to_internal.js" })],
      );
    }
    await client.query("COMMIT");
    return { outcome, previousRole: row ? row.role : null };
  } catch (error) {
    await client.query("ROLLBACK").catch(() => {});
    throw error;
  }
}

/** Turn a database error into a message that says what to do. */
function explainDbError(error) {
  const code = error && error.code;
  if (code === "ECONNREFUSED" || code === "ECONNRESET" || code === "ETIMEDOUT" || code === "ENOTFOUND") {
    return "Could not reach the database. For local development start it first:  npx prisma dev --detach --name caip-local";
  }
  if (code === "28P01" || code === "28000") return "The database rejected the login in DATABASE_URL (frontend/.env).";
  if (code === "3D000") return "The database named in DATABASE_URL does not exist.";
  if (code === "42P01") return "The tables do not exist yet. Create them first:  npx prisma migrate deploy";
  return `Database error${code ? ` (${code})` : ""}: ${String(error && error.message).slice(0, 160)}`;
}

/** Line-by-line prompt that also works for piped and closed input. null = input ended. */
function createAsker(input, output) {
  const rl = readline.createInterface({ input, output, terminal: false });
  const queue = [];
  const waiting = [];
  let closed = false;
  rl.on("line", (line) => (waiting.length ? waiting.shift()(line) : queue.push(line)));
  rl.on("close", () => {
    closed = true;
    while (waiting.length) waiting.shift()(null);
  });
  rl.on("SIGINT", () => rl.close());
  return {
    ask(question) {
      output.write(question);
      if (queue.length) return Promise.resolve(queue.shift());
      if (closed) return Promise.resolve(null);
      return new Promise((resolve) => waiting.push(resolve));
    },
    close: () => rl.close(),
  };
}

/** Ask for the email, re-asking on a malformed one. Returns { email } or { exitCode }. */
async function askForEmail(input, output) {
  const out = (message = "") => output.write(`${message}\n`);
  const asker = createAsker(input, output);
  try {
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      const raw = await asker.ask("Enter the email address of the account to promote to internal: ");
      if (raw === null) {
        out("\nCancelled. Nothing was changed.");
        return { exitCode: 130 };
      }
      const candidate = cleanEmail(raw);
      if (isValidEmail(candidate)) return { email: candidate };
      out(`❌ "${raw.trim()}" is not a valid email address.${attempt < MAX_ATTEMPTS ? " Please try again." : ""}`);
    }
  } finally {
    asker.close();
  }
  out("Too many invalid attempts. Nothing was changed.");
  return { exitCode: 2 };
}

/**
 * The whole flow. Injectable for tests:
 *   input / output   streams            envPath   .env file to read
 *   env              environment map    fetchFn   fetch replacement
 *   connect          () => pg client    (default: new pg client from DATABASE_URL)
 * Returns the process exit code.
 */
async function run(options = {}) {
  const input = options.input ?? process.stdin;
  const output = options.output ?? process.stdout;
  const envPath = options.envPath ?? ENV_PATH;
  const env = options.env ?? process.env;
  const out = (message = "") => output.write(`${message}\n`);

  const secretKey = readSetting("CLERK_SECRET_KEY", envPath, env);
  const databaseUrl = readSetting("DATABASE_URL", envPath, env);
  if (!/^sk_(test|live)_/.test(secretKey)) {
    out("❌ CLERK_SECRET_KEY is missing from frontend/.env. Run:  node scripts/setup_clerk.js");
    return 1;
  }
  if (!databaseUrl && !options.connect) {
    out("❌ DATABASE_URL is missing from frontend/.env.");
    return 1;
  }

  const asked = await askForEmail(input, output);
  if (!asked.email) return asked.exitCode;
  const email = asked.email;

  let clerkUser;
  try {
    clerkUser = await findClerkUser(email, secretKey, options.fetchFn);
  } catch (error) {
    out(`❌ ${error.message}`);
    return error.exitCode ?? 1;
  }
  if (!clerkUser) {
    out(`❌ No account with the email ${email} exists in your Clerk app.`);
    out("   Sign up with that email at /sign-up first (using the same Clerk keys), then run this again.");
    return 1;
  }

  let client;
  try {
    if (options.connect) {
      client = await options.connect();
    } else {
      client = new (require("pg").Client)({ connectionString: databaseUrl, connectionTimeoutMillis: 10000 });
      await client.connect();
    }
    const result = await promoteInDatabase(client, clerkUser.id, email);
    const tail = "Hard refresh your browser (Ctrl+F5) and visit /admin or /internal.";
    if (result.outcome === "already") {
      out(`✅ User ${email} is already an internal staff member. ${tail}`);
    } else {
      out(`✅ Success! User ${email} is now an internal staff member. ${tail}`);
      if (result.outcome === "created") out("   (This account had not opened the app yet, so its database record was created.)");
    }
    return 0;
  } catch (error) {
    out(`❌ ${error instanceof PromoteError ? error.message : explainDbError(error)}`);
    return error.exitCode ?? 1;
  } finally {
    // Closing must never hide the real error, whatever state the connection is in.
    try {
      if (client && typeof client.end === "function") await client.end();
    } catch {
      // ignore: nothing useful to do if closing fails
    }
  }
}

module.exports = { cleanEmail, isValidEmail, readSetting, findClerkUser, promoteInDatabase, explainDbError, run, PromoteError };

if (require.main === module) {
  run().then(
    (code) => process.exit(code),
    (error) => {
      console.error("Unexpected error:", error && error.message ? error.message : error);
      process.exit(1);
    },
  );
}
