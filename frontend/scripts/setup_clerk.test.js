/* eslint-disable @typescript-eslint/no-require-imports -- plain CommonJS test file */
/**
 * Tests for scripts/setup_clerk.js. Run from the frontend folder:
 *     node --test scripts/
 *
 * Uses temporary files and fake input streams only, never the real frontend/.env.
 * The keys below are made-up strings that merely have the right shape.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { PassThrough } = require("node:stream");

const setup = require("./setup_clerk.js");

// Obviously fake on purpose: words separated by underscores, so secret scanners
// (which look for long unbroken runs of letters and digits) do not mistake them
// for real keys. They still pass setup_clerk.js validation: correct prefix, longer
// than 20 characters, and none of the words it treats as placeholders.
const PK = "pk_test_DUMMY_FAKE_FOR_TESTS_ONLY_0001";
const SK = "sk_test_DUMMY_FAKE_FOR_TESTS_ONLY_0002";
const PK_LIVE = "pk_live_DUMMY_FAKE_FOR_TESTS_ONLY_0003";
const SK_LIVE = "sk_live_DUMMY_FAKE_FOR_TESTS_ONLY_0004";
const [PK_SPEC, SK_SPEC] = setup.KEYS;

function tmpEnv(content) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clerk-setup-"));
  const file = path.join(dir, ".env");
  if (content !== undefined) fs.writeFileSync(file, content);
  return { dir, file, cleanup: () => fs.rmSync(dir, { recursive: true, force: true }) };
}

/** Run the flow with scripted answers; returns { code, text, env }. */
async function runWith(answers, content, extra = {}) {
  const env = tmpEnv(content);
  const input = new PassThrough();
  const output = new PassThrough();
  let text = "";
  output.on("data", (chunk) => (text += chunk));
  const promise = setup.run({ input, output, envPath: env.file, ...extra });
  for (const answer of answers) input.write(answer + "\n");
  input.end();
  const code = await promise;
  return { code, text, env };
}

test("publishable key validation", () => {
  assert.equal(setup.validateKey(PK, PK_SPEC), null);
  assert.equal(setup.validateKey(PK_LIVE, PK_SPEC), null);
  assert.match(setup.validateKey("", PK_SPEC), /Nothing/);
  assert.match(setup.validateKey("abc_test_DUMMY_FAKE_FOR_TESTS_ONLY_9999", PK_SPEC), /must start with pk_test_ or pk_live_/);
  assert.match(setup.validateKey("pk_test_short", PK_SPEC), /too short/);
  assert.match(setup.validateKey(SK, PK_SPEC), /SECRET key/);
});

test("secret key validation", () => {
  assert.equal(setup.validateKey(SK, SK_SPEC), null);
  assert.equal(setup.validateKey(SK_LIVE, SK_SPEC), null);
  assert.match(setup.validateKey("sk_test_short", SK_SPEC), /too short/);
  assert.match(setup.validateKey(PK, SK_SPEC), /PUBLISHABLE key/);
  assert.match(setup.validateKey("whatever_DUMMY_FAKE_FOR_TESTS_ONLY_9999", SK_SPEC), /must start with sk_test_ or sk_live_/);
});

test("length rule is 'longer than 20 characters'", () => {
  const exactly20 = "pk_test_" + "a1".repeat(6); // 8 + 12 = 20
  assert.equal(exactly20.length, 20);
  assert.match(setup.validateKey(exactly20, PK_SPEC), /too short/);
  assert.equal(setup.validateKey(exactly20 + "b", PK_SPEC), null);
});

test("placeholders and junk are rejected", () => {
  for (const bad of [
    "pk_test_your_publishable_key_here",
    "pk_test_" + "x".repeat(24), // built at runtime so no key-shaped literal is stored in the file
    "pk_test_<paste-here-and-more-text>",
    "pk_test_has spaces inside the key value",
  ]) {
    assert.notEqual(setup.validateKey(bad, PK_SPEC), null, bad);
  }
});

test("cleanInput strips whitespace, quotes and a pasted NAME= prefix", () => {
  assert.equal(setup.cleanInput(`  ${PK}  `, PK_SPEC.env), PK);
  assert.equal(setup.cleanInput(`"${PK}"`, PK_SPEC.env), PK);
  assert.equal(setup.cleanInput(`'${PK}'`, PK_SPEC.env), PK);
  assert.equal(setup.cleanInput(`${PK_SPEC.env}=${PK}`, PK_SPEC.env), PK);
  assert.equal(setup.cleanInput(`${PK_SPEC.env}="${PK}"`, PK_SPEC.env), PK);
  assert.equal(setup.cleanInput(`${PK}\r`, PK_SPEC.env), PK);
  assert.equal(setup.cleanInput(undefined, PK_SPEC.env), "");
});

test("updateEnvText removes old Clerk lines, keeps the rest, appends the keys", () => {
  const before = [
    'DATABASE_URL="postgres://x"',
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_PLACEHOLDER",
    "CLERK_SECRET_KEY=sk_test_PLACEHOLDER",
    "NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in",
    "export CLERK_SECRET_KEY=old",
    "CLERK_SECRET_KEY_OLD=zzz",
    "BACKEND_URL=http://localhost:8000",
    "# CLERK_SECRET_KEY=commented out stays",
    "NOT_CLERK=1",
    "",
  ].join("\n");
  const { text, removed } = setup.updateEnvText(before, { [PK_SPEC.env]: PK, [SK_SPEC.env]: SK });
  const lines = text.split("\n");
  assert.deepEqual(removed, [
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY", "NEXT_PUBLIC_CLERK_SIGN_IN_URL",
    "CLERK_SECRET_KEY", "CLERK_SECRET_KEY_OLD",
  ]);
  assert.ok(!text.includes("PLACEHOLDER"));
  assert.ok(lines.includes('DATABASE_URL="postgres://x"'));
  assert.ok(lines.includes("BACKEND_URL=http://localhost:8000"));
  assert.ok(lines.includes("# CLERK_SECRET_KEY=commented out stays"));
  assert.ok(lines.includes("NOT_CLERK=1"));
  assert.equal(lines.filter((l) => l.startsWith("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=")).length, 1);
  assert.equal(lines.filter((l) => l.startsWith("CLERK_SECRET_KEY=")).length, 1);
  assert.equal(lines[lines.length - 3], `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${PK}`);
  assert.equal(lines[lines.length - 2], `CLERK_SECRET_KEY=${SK}`);
  assert.equal(lines[lines.length - 1], "");
});

test("updateEnvText: empty input, BOM, CRLF and idempotence", () => {
  const values = { [PK_SPEC.env]: PK, [SK_SPEC.env]: SK };
  const fresh = setup.updateEnvText("", values).text;
  assert.equal(fresh, `${setup.MARKER}\nNEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${PK}\nCLERK_SECRET_KEY=${SK}\n`);
  assert.equal(setup.updateEnvText(undefined, values).text, fresh);

  const bom = setup.updateEnvText("\uFEFFA=1\r\nB=2\r\n", values).text;
  assert.ok(!bom.includes("\uFEFF"));
  assert.ok(bom.startsWith("A=1\r\nB=2\r\n"));
  assert.ok(bom.includes(`CLERK_SECRET_KEY=${SK}\r\n`));

  const once = setup.updateEnvText("A=1\n", values).text;
  assert.equal(setup.updateEnvText(once, values).text, once);
  assert.equal(once.split(setup.MARKER).length, 2); // exactly one marker
});

test("full flow: replaces placeholders, keeps other settings, prints the exact messages", async () => {
  const before = 'DATABASE_URL="postgres://x"\nNEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_PLACEHOLDER\nCLERK_SECRET_KEY=sk_test_PLACEHOLDER\n';
  const { code, text, env } = await runWith([PK, SK], before);
  try {
    assert.equal(code, 0);
    const saved = fs.readFileSync(env.file, "utf8");
    assert.ok(saved.includes('DATABASE_URL="postgres://x"'));
    assert.ok(!saved.includes("PLACEHOLDER"));
    assert.ok(saved.endsWith(`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${PK}\nCLERK_SECRET_KEY=${SK}\n`));
    assert.ok(text.includes("✅ Clerk keys successfully saved to frontend/.env!"));
    assert.ok(text.includes("Please restart your dev server (Ctrl+C, then npm run dev) and hard refresh your browser (Ctrl+F5) to see the sign-in page."));
    assert.ok(!text.includes(PK) && !text.includes(SK), "full keys must never be printed back");
    assert.equal(fs.readFileSync(env.file + ".bak", "utf8"), before); // backup of the old file
  } finally {
    env.cleanup();
  }
});

test("full flow: bad answers are rejected and asked again", async () => {
  // Publishable prompt: empty, too short, the secret key, no prefix, then valid.
  // Secret prompt: no prefix, the publishable key, then valid.
  const { code, text, env } = await runWith(["", "pk_test_short", SK, "not a key", PK, "not a key either", PK, SK], "A=1\n");
  try {
    assert.equal(code, 0);
    assert.match(text, /Nothing was entered/);
    assert.match(text, /too short/);
    assert.match(text, /looks like the SECRET key/);
    assert.match(text, /must start with pk_test_ or pk_live_/);
    assert.match(text, /looks like the PUBLISHABLE key/); // PK given for the secret prompt
    assert.match(text, /must start with sk_test_ or sk_live_/);
    const saved = fs.readFileSync(env.file, "utf8");
    assert.ok(saved.includes(`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${PK}`) && saved.includes(`CLERK_SECRET_KEY=${SK}`));
  } finally {
    env.cleanup();
  }
});

test("full flow: a missing .env is created", async () => {
  const { code, text, env } = await runWith([PK, SK], undefined);
  try {
    assert.equal(code, 0);
    assert.match(text, /No \.env file found/);
    assert.equal(fs.readFileSync(env.file, "utf8"), `${setup.MARKER}\nNEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${PK}\nCLERK_SECRET_KEY=${SK}\n`);
    assert.equal(fs.existsSync(env.file + ".bak"), false); // nothing to back up
  } finally {
    env.cleanup();
  }
});

test("full flow: live keys accepted; mixed test/live gives a warning but saves", async () => {
  const live = await runWith([PK_LIVE, SK_LIVE], "A=1\n");
  const mixed = await runWith([PK, SK_LIVE], "A=1\n");
  try {
    assert.equal(live.code, 0);
    assert.ok(!/Warning/.test(live.text));
    assert.equal(mixed.code, 0);
    assert.match(mixed.text, /Warning: the publishable key is a test key but the secret key is a live key/);
  } finally {
    live.env.cleanup();
    mixed.env.cleanup();
  }
});


test("cancel (input ends) before or between keys changes nothing", async () => {
  for (const answers of [[], [PK]]) {
    const { code, text, env } = await runWith(answers, "A=1\nCLERK_SECRET_KEY=sk_test_PLACEHOLDER\n");
    try {
      assert.equal(code, 130);
      assert.match(text, /Cancelled\. Nothing was changed/);
      assert.equal(fs.readFileSync(env.file, "utf8"), "A=1\nCLERK_SECRET_KEY=sk_test_PLACEHOLDER\n");
      assert.equal(fs.existsSync(env.file + ".bak"), false);
    } finally {
      env.cleanup();
    }
  }
});

test("too many invalid attempts gives up and changes nothing", async () => {
  const { code, text, env } = await runWith(Array(5).fill("nope"), "A=1\n");
  try {
    assert.equal(code, 2);
    assert.match(text, /Too many invalid attempts/);
    assert.equal(fs.readFileSync(env.file, "utf8"), "A=1\n");
  } finally {
    env.cleanup();
  }
});

test("valid publishable key then an unusable secret key still writes nothing", async () => {
  const { code, env } = await runWith([PK, ...Array(5).fill("sk_test_short")], "A=1\n");
  try {
    assert.equal(code, 2);
    assert.equal(fs.readFileSync(env.file, "utf8"), "A=1\n");
  } finally {
    env.cleanup();
  }
});

test("an unreadable .env (a directory) is reported before any prompt", async () => {
  const env = tmpEnv();
  fs.mkdirSync(env.file); // .env is a directory: reading fails with EISDIR
  try {
    const input = new PassThrough();
    const output = new PassThrough();
    let text = "";
    output.on("data", (chunk) => (text += chunk));
    const code = await setup.run({ input, output, envPath: env.file });
    assert.equal(code, 1);
    assert.match(text, /Could not read/);
    assert.ok(!/Paste your/.test(text), "must not prompt when .env cannot be read");
  } finally {
    env.cleanup();
  }
});

test("an unwritable target is reported, not thrown", async () => {
  const env = tmpEnv("A=1\n");
  try {
    // The temp path in the missing folder cannot be created, and neither can the fallback.
    const impossible = path.join(env.dir, "missing-folder", ".env");
    const input = new PassThrough();
    const output = new PassThrough();
    let text = "";
    output.on("data", (chunk) => (text += chunk));
    const promise = setup.run({ input, output, envPath: impossible });
    input.write(PK + "\n" + SK + "\n");
    input.end();
    const code = await promise;
    assert.equal(code, 1);
    assert.match(text, /Could not write/);
    assert.ok(!/successfully saved/.test(text));
  } finally {
    env.cleanup();
  }
});

test("running the script for real (child process) with piped answers", async () => {
  const { spawnSync } = require("node:child_process");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clerk-real-"));
  try {
    // Copy the script into a sandbox whose parent folder holds the .env it will edit.
    const sandbox = path.join(dir, "frontend", "scripts");
    fs.mkdirSync(sandbox, { recursive: true });
    fs.copyFileSync(path.join(__dirname, "setup_clerk.js"), path.join(sandbox, "setup_clerk.js"));
    const envFile = path.join(dir, "frontend", ".env");
    fs.writeFileSync(envFile, "KEEP=1\nCLERK_SECRET_KEY=sk_test_PLACEHOLDER\n");

    const result = spawnSync(process.execPath, ["scripts/setup_clerk.js"], {
      cwd: path.join(dir, "frontend"),
      input: `${PK}\n${SK}\n`,
      encoding: "utf8",
      timeout: 20000,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.ok(result.stdout.includes("✅ Clerk keys successfully saved to frontend/.env!"));
    const saved = fs.readFileSync(envFile, "utf8");
    assert.ok(saved.startsWith("KEEP=1\n") && !saved.includes("PLACEHOLDER"));
    assert.ok(saved.endsWith(`CLERK_SECRET_KEY=${SK}\n`));

    const closed = spawnSync(process.execPath, ["scripts/setup_clerk.js"], {
      cwd: path.join(dir, "frontend"), input: "", encoding: "utf8", timeout: 20000,
    });
    assert.equal(closed.status, 130, "closed input must exit cleanly, not hang");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

