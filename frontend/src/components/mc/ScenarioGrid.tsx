import { List } from "@/components/mc/ui";
import type { Scenarios } from "@/lib/mc-types";

const COLUMNS = [
  { key: "optimistic", label: "Optimistic", accent: "border-emerald-400/30" },
  { key: "baseline", label: "Baseline", accent: "border-cyan-400/30" },
  { key: "pessimistic", label: "Pessimistic", accent: "border-red-400/30" },
] as const;

/** The CAO-style three scenarios. The confidence figure is the agent's own judgement, not a statistical probability. */
export default function ScenarioGrid({ scenarios }: { scenarios: Scenarios }) {
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      {COLUMNS.map(({ key, label, accent }) => {
        const s = scenarios[key];
        if (!s) return null;
        return (
          <div key={key} className={`space-y-2 rounded-lg border ${accent} bg-black/20 p-3`}>
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-semibold text-slate-100">{label}</h4>
              <span className="text-xs text-slate-400" title="The agent's own judgement, not a statistical probability">
                confidence {s.confidence_percentage}%
              </span>
            </div>
            <p className="text-sm text-slate-300">{s.description}</p>
            <p className="text-xs text-slate-400">
              <span className="text-slate-500">Timeline:</span> {s.timeline}
            </p>
            <p className="text-xs text-slate-400">
              <span className="text-slate-500">Resources:</span> {s.resource_requirements}
            </p>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-slate-500">Key assumptions</p>
              <List items={s.key_assumptions ?? []} />
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-slate-500">Risks</p>
              <List items={s.risks ?? []} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
