import { Suspense, type ReactNode } from "react";
import Link from "next/link";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { checkStaff } from "@/lib/access";
import { clerkConfigured } from "@/lib/clerk-config";
import AuthNotConfigured from "@/app/AuthNotConfigured";

/** What a refused user sees if they somehow got past the proxy (it normally answers first). */
function Refusal({ title, detail }: { title: string; detail: string }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#0A2342] px-6 text-center">
      <div className="max-w-md rounded-2xl border border-white/10 bg-white/5 p-8">
        <h1 className="text-xl font-semibold text-white">{title}</h1>
        <p className="mt-3 text-sm text-white/70">{detail}</p>
        <Link
          href="/"
          className="mt-6 inline-block rounded-full bg-[#00C9A7] px-6 py-2 text-sm font-medium text-[#0A2342] transition hover:brightness-110"
        >
          Back to the home page
        </Link>
      </div>
    </main>
  );
}

/**
 * The SECOND lock on /internal and /admin (the proxy is the first).
 *
 * Wraps a layout's children: nothing inside is rendered unless the signed-in
 * user is staff (role 'internal' in our database, not banned). Reading the
 * session is request-time work, so it sits behind <Suspense> (Cache Components).
 *
 * This renders a refusal page rather than calling Next's experimental
 * forbidden(): the proxy already returns the real 403 status, and an
 * experimental flag is not worth enabling on a security path.
 */
async function Guard({ children, loginPath }: { children: ReactNode; loginPath: string }) {
  if (!clerkConfigured) return <AuthNotConfigured />;

  const { userId } = await auth();
  const check = await checkStaff(userId);
  if (!check.ok) {
    if (check.reason === "signed_out") redirect(`/sign-in?redirect_url=${encodeURIComponent(loginPath)}`);
    if (check.reason === "unavailable") {
      return <Refusal title="Temporarily unavailable" detail="We could not verify your access right now. Please try again in a moment." />;
    }
    return <Refusal title="403 · Staff only" detail="This area is for Century Aqua staff. Your account does not have access." />;
  }
  return <>{children}</>;
}

export default function StaffGuard({ children, loginPath }: { children: ReactNode; loginPath: string }) {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center bg-[#0A2342] text-white/60">
          <span className="animate-pulse">Checking access…</span>
        </div>
      }
    >
      <Guard loginPath={loginPath}>{children}</Guard>
    </Suspense>
  );
}
