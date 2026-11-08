import Link from "next/link";

/** Shown instead of Clerk's pages when the Clerk keys are not set. */
export default function AuthNotConfigured() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#0A2342] px-6 text-center">
      <div className="max-w-md rounded-2xl border border-white/10 bg-white/5 p-8">
        <span className="mx-auto mb-4 block h-3 w-3 rounded-full bg-amber-300 shadow-[0_0_14px_#fcd34d]" />
        <h1 className="text-xl font-semibold text-white">Sign-in isn&apos;t set up yet</h1>
        <p className="mt-3 text-sm text-white/60">
          The Clerk keys (NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY) are missing from this
          environment, so accounts are unavailable. The rest of the site still works.
        </p>
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
