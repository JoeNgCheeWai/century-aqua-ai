/**
 * End-to-end check of Mission Control over real HTTP (a RUNNING frontend and backend). Not a unit test: it needs both
 * servers up, so it is run by hand:
 *
 *     MC_URL=http://localhost:3000 MC_PIN=<ADMIN_PIN> node scripts/mission_control_flow.check.mjs
 *
 * It checks the login flow (no cookie, wrong PIN, right PIN, cookie flags, logout, forged cookie, open redirect) and that
 * every /api/mc/* route needs the cookie and reaches the backend once it has one. It never prints the PIN or the cookie.
 * It makes NO model call unless MC_ALLOW_MODEL_CALLS=1 (then it also starts a real council session).
 */
const BASE = (process.env.MC_URL ?? "http://localhost:3000").replace(/\/+$/, "");
const PIN = process.env.MC_PIN;
if (!PIN) {
  console.error("Set MC_PIN to the ADMIN_PIN of the server under test.");
  process.exit(2);
}

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
  if (!ok) failures += 1;
}

async function call(path, { method = "GET", cookie, body, raw } = {}) {
  const headers = {};
  if (cookie) headers.cookie = cookie;
  if (body !== undefined || raw !== undefined) headers["content-type"] = "application/json";
  const payload = raw !== undefined ? raw : body === undefined ? undefined : JSON.stringify(body);
  const response = await fetch(BASE + path, { method, headers, body: payload, redirect: "manual" });
  let json = null;
  try {
    json = await response.clone().json();
  } catch {
    // not JSON
  }
  return { status: response.status, json, headers: response.headers, location: response.headers.get("location") };
}

const sessionCookie = (response) => (response.headers.getSetCookie?.() ?? []).find((c) => c.startsWith("mc_session="));
const PAGES = ["/mission-control/dashboard", "/mission-control/audit", "/mission-control/authorizations", "/mission-control/council", "/mission-control/agents/cao"];

// ---- 1. everything private is closed without a cookie
for (const path of ["/mission-control", ...PAGES]) {
  const r = await call(path);
  check(`page ${path} redirects to login without a cookie`, r.status >= 300 && r.status < 400 && (r.location ?? "").includes("/mission-control/login"), `${r.status}`);
}
const PRIVATE_APIS = [["GET", "/api/mc/overview"], ["GET", "/api/mc/audit-logs"], ["GET", "/api/mc/council/sessions"],
  ["GET", "/api/mc/council/00000000-0000-4000-8000-000000000000"], ["POST", "/api/mc/council"], ["POST", "/api/mc/agents/cao"],
  ["POST", "/api/mc/authorization/1/approve"], ["POST", "/api/mc/authorization/1/reject"]];
for (const [method, path] of PRIVATE_APIS) {
  const r = await call(path, { method, body: method === "POST" ? {} : undefined });
  check(`${method} ${path} is 401 without a cookie`, r.status === 401, String(r.status));
}
const forged = await call("/api/mc/overview", { cookie: "mc_session=v1.9999999999.aaaaaaaaaaaaaaaaaaaaaaaa." + "0".repeat(64) });
check("a forged cookie is refused", forged.status === 401, String(forged.status));
check("the login page is public", (await call("/mission-control/login")).status === 200);

// ---- 2. login
const wrong = await call("/api/mc/login", { method: "POST", body: { pin: "wrong-pin-000" } });
check("a wrong PIN is 401 and sets no cookie", wrong.status === 401 && !sessionCookie(wrong), String(wrong.status));
check("a missing PIN is 401", (await call("/api/mc/login", { method: "POST", body: {} })).status === 401);
const broken = await call("/api/mc/login", { method: "POST", raw: "not json" });
check("a broken body is 400, not a 500", broken.status === 400, String(broken.status));

const ok = await call("/api/mc/login", { method: "POST", body: { pin: PIN, next: "https://evil.example/steal" } });
const setCookie = sessionCookie(ok) ?? "";
check("the right PIN is 200", ok.status === 200, String(ok.status));
check("the cookie is HttpOnly, SameSite=Strict, Path=/ and expires", /httponly/i.test(setCookie) && /samesite=strict/i.test(setCookie) && /path=\//i.test(setCookie) && /max-age=\d+/i.test(setCookie));
check("an outside 'next' becomes the dashboard (no open redirect)", ok.json?.next === "/mission-control/dashboard", String(ok.json?.next));
const cookie = setCookie.split(";")[0];

// ---- 3. with the cookie the pages open
for (const path of PAGES) {
  const r = await call(path, { cookie });
  check(`page ${path} opens with the cookie`, r.status === 200, String(r.status));
}

// ---- 4. with the cookie the API reaches the backend
const overview = await call("/api/mc/overview", { cookie });
check("overview: 200, backend reachable, four agents", overview.status === 200 && overview.json?.backend_reachable === true && overview.json?.agents?.length === 4, String(overview.status));
check("overview: every agent's audit data loaded", overview.json?.agents?.every((a) => a.available) === true);
check("overview: the pending count is a number", typeof overview.json?.pending_authorizations === "number", String(overview.json?.pending_authorizations));
check("overview: recent sessions are a list", Array.isArray(overview.json?.recent_sessions?.items));
check("overview never carries a backend key", !/Internal-.*-Key|INTERNAL_/i.test(JSON.stringify(overview.json)));

const audit = await call("/api/mc/audit-logs?limit=5", { cookie });
check("audit-logs: 200 with items and a total", audit.status === 200 && Array.isArray(audit.json?.items) && typeof audit.json?.total === "number", String(audit.status));
const filtered = await call("/api/mc/audit-logs?agent_id=cto&has_authorization=true&human_action=Pending&limit=3", { cookie });
check("audit-logs: filters pass through", filtered.status === 200 && filtered.json.items.every((i) => i.agent_id === "cto" && i.authorization_request && i.human_action === "Pending"));
check("audit-logs: a bad filter is the backend's 422, not a 500", (await call("/api/mc/audit-logs?limit=9999", { cookie })).status === 422);

const sessions = await call("/api/mc/council/sessions?limit=5", { cookie });
check("council/sessions: 200 with items", sessions.status === 200 && Array.isArray(sessions.json?.items), String(sessions.status));
check("council/sessions: a bad status is 422", (await call("/api/mc/council/sessions?status=NOPE", { cookie })).status === 422);
check("council/{id}: an unknown id is 404", (await call("/api/mc/council/00000000-0000-4000-8000-000000000000", { cookie })).status === 404);
check("council/{id}: a malformed id is 404", (await call("/api/mc/council/not-a-uuid", { cookie })).status === 404);

// input checks in the route handlers (none of these reach a model)
const post = (path, body) => call(path, { method: "POST", cookie, body });
check("council: an empty query is 400", (await post("/api/mc/council", { query: "  " })).status === 400);
check("council: an unknown agent is 400", (await post("/api/mc/council", { query: "q", agents: ["nobody"] })).status === 400);
check("agents: an unknown agent is 404", (await post("/api/mc/agents/zzz", { message: "hi" })).status === 404);
check("agents: an empty message is 400", (await post("/api/mc/agents/cao", { message: "" })).status === 400);
check("agents: a bad history is 400", (await post("/api/mc/agents/cao", { message: "hi", history: [{ role: "system", content: "x" }] })).status === 400);
check("authorization: a non-numeric id is 400", (await post("/api/mc/authorization/abc/approve")).status === 400);
check("authorization: only approve and reject exist", (await post("/api/mc/authorization/1/modify")).status === 400);
check("authorization: an unknown audit id is the backend's 404", (await post("/api/mc/authorization/2000000000/approve")).status === 404);

// ---- 5. optional: a real council session (costs model calls)
if (process.env.MC_ALLOW_MODEL_CALLS === "1") {
  const started = await post("/api/mc/council", { query: "Should we pilot a small RAS consulting service in Vietnam? Budget cap USD 150,000." });
  check("council: POST is 202 with a session_id", started.status === 202 && !!started.json?.session_id, String(started.status));
  const id = started.json?.session_id;
  let final;
  for (let i = 0; i < 80 && id; i++) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    final = (await call(`/api/mc/council/${id}`, { cookie })).json;
    if (final && final.status !== "RUNNING") break;
  }
  check("council: polling reaches a terminal status", !!final && final.status !== "RUNNING", final?.status);
}

// ---- 6. logout
const out = await call("/api/mc/logout", { method: "POST", cookie });
check("logout clears the cookie", out.status === 200 && /max-age=0/i.test(sessionCookie(out) ?? ""), String(out.status));

console.log(failures === 0 ? "\nALL CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);

