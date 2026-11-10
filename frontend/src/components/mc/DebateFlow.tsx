import { AgentChip, Badge, Card, Json, SectionTitle } from "@/components/mc/ui";
import type { CouncilSession, CouncilTurn } from "@/lib/mc-types";

/** The protocol's rounds, as the backend numbers them. Rounds 3 and 4 are targeted revisions (at most two). */
const ROUND_LABEL: Record<number, string> = {
  1: "Round 1 · Proposals (in parallel)",
  2: "Round 2 · COO moderation",
  3: "Round 3 · Revision",
  4: "Round 3 · Second revision",
  5: "Round 4 · CAO synthesis",
  6: "Round 5 · COO red team",
};

function TurnRow({ turn }: { turn: CouncilTurn }) {
  const system = turn.position === "synthesis" || turn.position === "red team";
  return (
    <li className="rounded-lg border border-slate-800 bg-black/20 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <AgentChip id={turn.agent_id} />
        {turn.confidence != null && <Badge tone="slate">confidence {turn.confidence}</Badge>}
        {turn.tokens_used != null && <span className="text-[11px] text-slate-500">{turn.tokens_used.toLocaleString()} tokens</span>}
      </div>
      <p className={`mt-2 text-sm ${system ? "italic text-slate-500" : "text-slate-200"}`}>
        {system ? `${turn.position} turn (the result is shown in the options)` : turn.position}
      </p>
      {turn.hard_constraints && turn.hard_constraints.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-slate-400">
          {turn.hard_constraints.map((c, i) => (
            <li key={i}>
              <span className="text-amber-300">hard constraint:</span> {c.constraint} <span className="text-slate-600">({c.source})</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * How the debate went: the participants and why, each round's turns, the COO's moderation notes, the conflicts (and
 * whether they resolved), the hard-constraint vetoes, and the dissent kept on the record.
 */
export default function DebateFlow({ session }: { session: CouncilSession }) {
  const turns = session.turns ?? [];
  const rounds = [...new Set(turns.map((t) => t.round))].sort((a, b) => a - b);
  const vetoes = session.hard_constraint_vetoes ?? [];
  const conflicts = session.conflict_matrix ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle>Participants</SectionTitle>
        <div className="flex flex-wrap items-center gap-2">
          {(session.participating_agents ?? []).map((a) => (
            <AgentChip key={a} id={a} />
          ))}
        </div>
        {session.selection_rationale && <p className="mt-2 text-sm text-slate-400">{session.selection_rationale}</p>}
      </Card>

      {rounds.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-400">No turns were recorded for this session.</p>
        </Card>
      ) : (
        rounds.map((round) => (
          <Card key={round}>
            <SectionTitle>{ROUND_LABEL[round] ?? `Round ${round}`}</SectionTitle>
            <ul className="space-y-2">
              {turns.filter((t) => t.round === round).map((t, i) => (
                <TurnRow key={`${round}-${i}`} turn={t} />
              ))}
            </ul>
            {round === 2 && session.coo_moderation_notes && (
              <div className="mt-3 rounded-lg border border-cyan-400/20 bg-cyan-400/5 p-3">
                <p className="text-[11px] uppercase tracking-wider text-cyan-300/80">COO moderation notes</p>
                <p className="mt-1 text-sm text-slate-200">{session.coo_moderation_notes}</p>
              </div>
            )}
          </Card>
        ))
      )}

      {rounds.length > 0 && !rounds.includes(2) && session.coo_moderation_notes && (
        <Card>
          <SectionTitle>COO moderation notes</SectionTitle>
          <p className="text-sm text-slate-200">{session.coo_moderation_notes}</p>
        </Card>
      )}

      {conflicts.length > 0 && (
        <Card>
          <SectionTitle>Conflict matrix</SectionTitle>
          <ul className="space-y-2">
            {conflicts.map((c, i) => (
              <li key={i} className="rounded-lg border border-slate-800 bg-black/20 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-slate-100">{c.topic}</span>
                  {c.agents.map((a) => (
                    <AgentChip key={a} id={a} />
                  ))}
                  <Badge tone={c.resolved ? "green" : "amber"}>{c.resolved ? "resolved" : "unresolved"}</Badge>
                </div>
                <p className="mt-1 text-sm text-slate-400">{c.positions}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {vetoes.length > 0 && (
        <Card>
          <SectionTitle>Hard-constraint vetoes (a law, a budget cap or a security floor outranks a preference)</SectionTitle>
          <ul className="space-y-2">
            {vetoes.map((v, i) => (
              <li key={i} className="text-sm text-slate-200">
                <AgentChip id={v.agent_id} /> <span className="font-medium">{v.constraint}</span>{" "}
                <span className="text-slate-500">({v.source})</span>
                {v.overrides.length > 0 && (
                  <span className="text-slate-400">
                    {" "}
                    overrides {v.overrides.map((a) => a.toUpperCase()).join(", ")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {(session.dissent_record?.length ?? 0) > 0 && (
        <Card>
          <SectionTitle>Dissent record (kept on purpose)</SectionTitle>
          <Json value={session.dissent_record} />
        </Card>
      )}
    </div>
  );
}
