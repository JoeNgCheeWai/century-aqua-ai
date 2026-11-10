import { Suspense } from "react";
import LoginForm from "./LoginForm";

export const metadata = { title: "Sign in | Mission Control", robots: { index: false, follow: false } };

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[radial-gradient(ellipse_at_top,_#0b1b3a_0%,_#020617_65%)] px-6">
      <div className="w-full max-w-sm rounded-2xl border border-cyan-400/20 bg-slate-950/80 p-8 text-center shadow-[0_0_60px_-20px_rgba(34,211,238,0.45)]">
        <span className="mx-auto mb-4 block h-3 w-3 rounded-full bg-cyan-400 shadow-[0_0_16px_#22d3ee]" />
        <h1 className="text-xl font-semibold tracking-wide text-white">
          MISSION <span className="font-light text-cyan-300">CONTROL</span>
        </h1>
        <p className="mt-1 text-xs uppercase tracking-[0.2em] text-slate-500">Century Aqua · Executive team</p>
        {/* useSearchParams reads the URL at request time, so the form sits behind Suspense (Cache Components). */}
        <Suspense fallback={<p className="mt-8 text-sm text-slate-500">Loading…</p>}>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
