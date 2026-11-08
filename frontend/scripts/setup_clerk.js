#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports -- plain CommonJS Node script */
/**
 * Interactive setup for the Clerk keys in frontend/.env.
 *
 * Run from the frontend folder:
 *     node scripts/setup_clerk.js
 *
 * Get the keys from https://dashboard.clerk.com -> your app -> API keys.
 * You are asked for two values and each is checked before anything is saved:
 *   NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY   pk_test_... or pk_live_...
 *   CLERK_SECRET_KEY                    sk_test_... or sk_live_...
 *
 * Then frontend/.env is rewritten: every existing line starting with
 * NEXT_PUBLIC_CLERK_ or CLERK_SECRET_KEY (old keys, placeholders, duplicates) is
 * removed and the two new lines are appended. All other lines are kept. A missing
 * .env is created. Nothing is written unless both keys are valid.
 *
 * Typing is VISIBLE (so pasting works in every Windows terminal). Do not run
 * this while sharing your screen. The keys are never printed back in full.
 *
 * Exit codes: 0 saved, 1 could not read/write .env, 2 too many invalid
 * attempts, 130 cancelled (Ctrl+C / Ctrl+D). Nothing is changed unless 0.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");

const ENV_PATH = path.join(__dirname, "..", ".env");
const MARKER = "# Clerk keys (written by scripts/setup_clerk.js)";
const MAX_ATTEMPTS = 5;
const MIN_EXCLUSIVE = 20; // keys must be LONGER than 20 characters

const KEYS = [
  {
    env: "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
    label: "publishable key",
    prefixes: ["pk_test_", "pk_live_"],
    wrongPrefix: "sk_",
    wrongHint: "That looks like the SECRET key (sk_...). Paste the PUBLISHABLE key (pk_...) here.",
  },
  {
    env: "CLERK_SECRET_KEY",
    label: "secret key",
    prefixes: ["sk_test_", "sk_live_"],
    wrongPrefix: "pk_",
    wrongHint: "That looks like the PUBLISHABLE key (pk_...). Paste the SECRET key (sk_...) here.",
  },
];

/** Trim, drop surrounding quotes, and drop a pasted "NAME=" prefix. */
function cleanInput(raw, envName) {
  let value = String(raw ?? "").trim();
  if (value.toLowerCase().startsWith(`${envName.toLowerCase()}=`)) {
    value = value.slice(envName.length + 1).trim();
  }
  return value.replace(/^(["'`])(.*)\1$/, "$2").trim();
}

/** Returns a problem description, or null when the key looks valid. */
function validateKey(value, spec) {
  if (!value) return "Nothing was entered.";
  if (value.startsWith(spec.wrongPrefix)) return spec.wrongHint;
  const prefix = spec.prefixes.find((p) => value.startsWith(p));
  if (!prefix) return `The ${spec.label} must start with ${spec.prefixes.join(" or ")}.`;
  if (value.length <= MIN_EXCLUSIVE) {
    return `That is too short to be a real ${spec.label} (it must be longer than ${MIN_EXCLUSIVE} characters). Part of it may have been cut off.`;
  }
  const body = value.slice(prefix.length);
  if (!/^[A-Za-z0-9+/=_-]+$/.test(body)) {
    return "It contains spaces or unusual characters. Copy only the key itself.";
  }
  if (/(^|_)(your|here|placeholder|example|key)(_|$)/i.test(body) || /x{6,}/i.test(body)) {
    return "That looks like placeholder text, not a real key. Copy the real key from the Clerk dashboard.";
  }
  return null;
}

const modeOf = (key) => (key.includes("_live_") ? "live" : "test");
const mask = (key) => `${key.slice(0, 8)}...${key.slice(-4)}`;

// Any line starting with these names is replaced (commented-out lines are kept).
const MANAGED_LINE = /^\s*(?:export\s+)?(NEXT_PUBLIC_CLERK_\w*|CLERK_SECRET_KEY\w*)/;

/**
 * Pure function: the new .env text, plus the names of the variables removed.
 * Keeps all other lines, the file's line endings, and strips a UTF-8 BOM
 * (PowerShell likes to add one, and it breaks parsers).
 */
function updateEnvText(existing, values) {
  const text = String(existing ?? "").replace(/^\uFEFF/, "");
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const kept = [];
  const removed = [];
  for (const line of text.split(/\r?\n/)) {
    const match = MANAGED_LINE.exec(line);
    if (match) {
      removed.push(match[1]);
    } else if (line.trim() !== MARKER) {
      kept.push(line);
    }
  }
  while (kept.length > 0 && kept[kept.length - 1].trim() === "") kept.pop();

  const out = [...kept];
  if (out.length > 0) out.push("");
  out.push(MARKER);
  for (const spec of KEYS) out.push(`${spec.env}=${values[spec.env]}`);
  return { text: out.join(eol) + eol, removed };
}

/** Write via a temp file so a crash cannot leave a half-written .env. */
function writeEnvFile(envPath, text) {
  const tmp = `${envPath}.tmp-${process.pid}`;
  try {
    fs.writeFileSync(tmp, text, { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tmp, envPath);
  } catch {
    fs.rmSync(tmp, { force: true });
    fs.writeFileSync(envPath, text, "utf8"); // fallback if the rename is blocked
  }
}

/**
 * Line-by-line prompt that works for a terminal, piped input and closed input.
 * Resolves to the next line, or null when input ended (Ctrl+D / Ctrl+C / EOF).
 */
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
  // Ctrl+C: readline only emits SIGINT when the stream is a TTY.
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

async function askForKey(asker, spec, out) {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const raw = await asker.ask(`Paste your ${spec.env} (${spec.prefixes.join(" or ")}...): `);
    if (raw === null) return { cancelled: true };
    const value = cleanInput(raw, spec.env);
    const problem = validateKey(value, spec);
    if (!problem) return { value };
    out(`❌ ${problem}`);
    if (attempt < MAX_ATTEMPTS) out("   Please try again (Ctrl+C to cancel).");
  }
  return { tooMany: true };
}

/**
 * The whole interactive flow. Injectable for testing:
 *   options.input / options.output   streams (default stdin / stdout)
 *   options.envPath                  file to update (default frontend/.env)
 * Returns the process exit code.
 */
async function run(options = {}) {
  const input = options.input ?? process.stdin;
  const output = options.output ?? process.stdout;
  const envPath = options.envPath ?? ENV_PATH;
  const out = (message = "") => output.write(`${message}\n`);

  out("Century Aqua AI: Clerk setup");
  out("Get both keys from https://dashboard.clerk.com -> your app -> API keys.");
  out("Note: what you paste is shown on screen. Don't run this while sharing your screen.");
  out("");

  // Read the current file FIRST, so a problem is found before asking anything.
  let existing = "";
  try {
    existing = fs.readFileSync(envPath, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") {
      out(`❌ Could not read ${envPath}: ${error.code ?? error.message}`);
      return 1;
    }
    out("No .env file found, so a new one will be created.");
  }

  const asker = createAsker(input, output);
  const values = {};
  try {
    for (const spec of KEYS) {
      const result = await askForKey(asker, spec, out);
      if (result.cancelled) {
        out("\nCancelled. Nothing was changed.");
        return 130;
      }
      if (result.tooMany) {
        out("Too many invalid attempts. Nothing was changed.");
        return 2;
      }
      values[spec.env] = result.value;
    }
  } finally {
    asker.close();
  }

  const pkMode = modeOf(values.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
  const skMode = modeOf(values.CLERK_SECRET_KEY);
  if (pkMode !== skMode) {
    out(`⚠️  Warning: the publishable key is a ${pkMode} key but the secret key is a ${skMode} key.`);
    out("   They must come from the same Clerk instance or sign-in will fail. Saving anyway.");
  }

  const { text, removed } = updateEnvText(existing, values);
  try {
    if (existing && !options.noBackup) {
      fs.writeFileSync(`${envPath}.bak`, existing, { encoding: "utf8", mode: 0o600 });
    }
    writeEnvFile(envPath, text);
  } catch (error) {
    out(`❌ Could not write ${envPath}: ${error.code ?? error.message}`);
    return 1;
  }

  if (removed.length > 0) out(`Replaced ${removed.length} old Clerk line(s): ${[...new Set(removed)].join(", ")}`);
  out(`Saved: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${mask(values.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY)} (${pkMode})`);
  out(`Saved: CLERK_SECRET_KEY=${mask(values.CLERK_SECRET_KEY)} (${skMode})`);
  out("");
  out("✅ Clerk keys successfully saved to frontend/.env!");
  out("Please restart your dev server (Ctrl+C, then npm run dev) and hard refresh your browser (Ctrl+F5) to see the sign-in page.");
  if (existing && !options.noBackup) out("(Your previous .env was kept as frontend/.env.bak; delete it when you no longer need it.)");
  return 0;
}

module.exports = { cleanInput, validateKey, updateEnvText, writeEnvFile, run, KEYS, MARKER };

if (require.main === module) {
  run().then(
    (code) => process.exit(code),
    (error) => {
      console.error("Unexpected error:", error && error.message ? error.message : error);
      process.exit(1);
    },
  );
}
