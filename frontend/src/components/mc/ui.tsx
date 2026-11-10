import type { ReactNode } from "react";
import type { CouncilStatus, HumanAction } from "@/lib/mc-types";

/** Small, dependency-free building blocks for Mission Control (dark theme: slate base, cyan and blue accents). */

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-cyan-400/15 bg-gradient-to-b from-slate-900/80 to-slate-950/90 p-4 shadow-[0_0_24px_-12px_rgba(34,211,238,0.35)] ${className}`}>
      {children}
    </section>
  );
}

export function PageTitle({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="bg-gradient-to-r from-cyan-300 to-blue-400 bg-clip-text text-2xl font-semibold tracking-tight text-transparent">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="mb-2 text-xs font-medium uppercase tracking-[0.18em] text-cyan-300/80">{children}</h2>;
}

const TONES = {
  cyan: "border-cyan-400/40 bg-cyan-400/10 text-cyan-200",
  green: "border-emerald-400/40 bg-emerald-400/10 text-emerald-200",
  amber: "border-amber-400/40 bg-amber-400/10 text-amber-200",
  red: "border-red-400/40 bg-red-400/10 text-red-200",
  slate: "border-slate-400/30 bg-slate-400/10 text-slate-300",
  blue: "border-blue-400/40 bg-blue-400/10 text-blue-200",
};
export type Tone = keyof typeof TONES;

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONES[tone]}`}>{children}</span>;
}

const STATUS_TONE: Record<CouncilStatus, Tone> = { RUNNING: "cyan", RESOLVED: "green", ESCALATED_TO_CEO: "amber", NEEDS_CEO_INPUT: "amber", FAILED: "red" };
const STATUS_LABEL: Record<CouncilStatus, string> = {
  RUNNING: "Running",
  RESOLVED: "Resolved",
  ESCALATED_TO_CEO: "Escalated to CEO",
  NEEDS_CEO_INPUT: "Needs CEO input",
  FAILED: "Failed",
};
export function StatusBadge({ status }: { status: CouncilStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>;
}

const ACTION_TONE: Record<HumanAction, Tone> = { Pending: "amber", Approved: "green", Rejected: "red", Modified: "blue" };
export function ActionBadge({ action }: { action: HumanAction }) {
  return <Badge tone={ACTION_TONE[action]}>{action}</Badge>;
}

const RISK_TONE: Record<string, Tone> = { low: "green", medium: "amber", high: "red", critical: "red" };
export function RiskBadge({ level }: { level: string }) {
  return <Badge tone={RISK_TONE[level] ?? "slate"}>{level} risk</Badge>;
}

export function AgentChip({ id }: { id: string }) {
  return <span className="inline-block rounded border border-blue-400/30 bg-blue-500/10 px-1.5 py-0.5 font-mono text-[11px] uppercase text-blue-200">{id}</span>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-cyan-200">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-cyan-400/30 border-t-cyan-300" aria-hidden="true" />
      {label}
    </span>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
      <span>{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="rounded border border-red-300/40 px-2 py-0.5 text-xs hover:bg-red-400/10">
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-dashed border-slate-600/50 px-3 py-6 text-center text-sm text-slate-400">{children}</p>;
}

export const buttonPrimary =
  "rounded-lg bg-gradient-to-r from-cyan-500 to-blue-500 px-4 py-2 text-sm font-medium text-slate-950 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40";
export const buttonGhost =
  "rounded-lg border border-cyan-400/30 px-3 py-1.5 text-sm text-cyan-100 transition hover:bg-cyan-400/10 disabled:cursor-not-allowed disabled:opacity-40";
export const inputClass =
  "w-full rounded-lg border border-slate-600/60 bg-slate-950/70 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-cyan-400/60 focus:outline-none focus:ring-1 focus:ring-cyan-400/40";

/** Pretty JSON for the "raw record" views. */
export function Json({ value }: { value: unknown }) {
  return <pre className="max-h-96 overflow-auto rounded-lg bg-black/40 p-3 text-xs leading-relaxed text-slate-300">{JSON.stringify(value, null, 2)}</pre>;
}

/** A preformatted block for code diffs and commands (never rendered as HTML). */
export function Code({ children }: { children: string }) {
  return <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-700 bg-black/50 p-3 font-mono text-xs leading-relaxed text-emerald-100">{children}</pre>;
}

export function List({ items, empty = "None." }: { items: string[]; empty?: string }) {
  if (!items.length) return <p className="text-sm text-slate-500">{empty}</p>;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300 marker:text-cyan-400/60">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}
