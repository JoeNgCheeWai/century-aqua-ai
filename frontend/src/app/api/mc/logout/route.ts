/** POST /api/mc/logout: clear the Mission Control cookie. Public on purpose (clearing a cookie needs no session). */

import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/mc-auth";
import { json } from "@/lib/mc-session";

export async function POST() {
  (await cookies()).set(SESSION_COOKIE, "", { httpOnly: true, sameSite: "strict", path: "/", maxAge: 0 });
  return json({ ok: true });
}
