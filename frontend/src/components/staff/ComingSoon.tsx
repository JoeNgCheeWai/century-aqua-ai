import type { ReactNode } from "react";

interface ComingSoonProps {
  title: string;
  description: string;
  /** What will be built here. */
  planned: string[];
  /** Which phase delivers it, e.g. "Phase 2". */
  phase: string;
  children?: ReactNode;
}

/** Honest placeholder for a section whose feature is not built yet (no fake data). */
export default function ComingSoon({ title, description, planned, phase, children }: ComingSoonProps) {
  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-10">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold text-white sm:text-3xl">{title}</h1>
        <span className="rounded-full border border-amber-300/40 bg-amber-300/10 px-3 py-0.5 text-xs font-medium text-amber-200">
          Not built yet · {phase}
        </span>
      </div>
      <p className="mt-3 max-w-2xl text-white/70">{description}</p>

      <div className="mt-8 rounded-2xl border border-white/10 bg-white/5 p-6">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#00C9A7]">Planned for this page</p>
        <ul className="mt-3 space-y-2 text-sm text-white/80">
          {planned.map((item) => (
            <li key={item} className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[#00C9A7]" />
              {item}
            </li>
          ))}
        </ul>
      </div>
      {children}
    </div>
  );
}
