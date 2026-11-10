import { AgentChip, Badge, Card, List, SectionTitle } from "@/components/mc/ui";
import type { CouncilOption, CouncilSession } from "@/lib/mc-types";

/** One synthesized option: what it is, who backs it, who dissents, its assumptions, and the COO's red-team failure mode. */
function OptionCard({ option, failureMode }: { option: CouncilOption; failureMode: string | undefined }) {
  const dissent = option.dissent_from ?? [];
  const support = option.supported_by ?? [];
  const failure = option.most_likely_failure_mode || failureMode;
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-mono text-xs text-cyan-300">OPTION {option.option_id}</p>
          <h3 className="text-base font-semibold text-slate-100">{option.title}</h3>
        </div>
        <Badge tone={dissent.length ? "amber" : "green"}>{dissent.length ? "contested" : "no dissent"}</Badge>
      </div>
      <p className="text-sm text-slate-300">{option.summary}</p>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
        <span className="flex items-center gap-1.5">
          Supported by {support.length ? support.map((a) => <AgentChip key={a} id={a} />) : <em>no one</em>}
        </span>
        <span className="flex items-center gap-1.5">
          Dissent from {dissent.length ? dissent.map((a) => <AgentChip key={a} id={a} />) : <em>none</em>}
        </span>
      </div>

      <div>
        <SectionTitle>Stated assumptions</SectionTitle>
        <List items={option.assumptions ?? []} empty="No assumptions were stated." />
      </div>

      {option.unresolved_objection && (
        <div className="rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
          <SectionTitle>Unresolved objection</SectionTitle>
          <p className="text-sm text-amber-100/90">{option.unresolved_objection}</p>
        </div>
      )}

      <div className="rounded-lg border border-red-400/25 bg-red-400/5 p-3">
        <SectionTitle>COO red team: most likely failure mode</SectionTitle>
        <p className="text-sm text-red-100/90">{failure || "The red team recorded no failure mode for this option."}</p>
      </div>

      {option.proposed_action && (
        <p className="text-xs text-slate-500">
          Proposed action: {option.proposed_action.action_summary} ·{" "}
          <span className="text-amber-300">pending human approval, not executed</span>
        </p>
      )}
    </Card>
  );
}

/**
 * The outcome of a finished council session: the options (or why there are none), the open questions and the dissent.
 * Used by the session viewer and by the agent page's council mode.
 */
export default function CouncilOptions({ session }: { session: CouncilSession }) {
  const options = session.options ?? [];
  const redTeam = new Map((session.red_team_findings ?? []).map((f) => [f.option_id, f.most_likely_failure_mode]));
  const critical = session.ceo_questions ?? [];
  const open = session.open_questions ?? [];

  return (
    <div className="space-y-4">
      {session.status === "FAILED" && (
        <div role="alert" className="rounded-lg border border-red-400/30 bg-red-500/10 p-3 text-sm text-red-200">
          The session failed{session.escalation_reason ? `: ${session.escalation_reason}` : "."}
        </div>
      )}
      {session.status === "ESCALATED_TO_CEO" && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-500/10 p-3 text-sm text-amber-100">
          The council could not settle this and escalated it to the CEO{session.escalation_reason ? `: ${session.escalation_reason}` : "."}
        </div>
      )}
      {session.status === "NEEDS_CEO_INPUT" && (
        <Card>
          <SectionTitle>The CEO must answer before the council can proceed</SectionTitle>
          <List items={critical} empty="No questions were recorded." />
        </Card>
      )}

      {options.length > 0 && (
        <div className="grid gap-4 xl:grid-cols-3 lg:grid-cols-2">
          {options.map((o) => (
            <OptionCard key={o.option_id} option={o} failureMode={redTeam.get(o.option_id)} />
          ))}
        </div>
      )}

      {open.length > 0 && (
        <Card>
          <SectionTitle>Open questions (flagged, ruled non-critical; stated as assumptions in the options)</SectionTitle>
          <List items={open} />
        </Card>
      )}
    </div>
  );
}
