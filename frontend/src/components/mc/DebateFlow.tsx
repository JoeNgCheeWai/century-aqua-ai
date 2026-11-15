import { AgentChip, Badge, Card, Json, SectionTitle } from "@/components/mc/ui";
import { roundOrder } from "@/lib/mc-boardroom";
import type { CouncilResponse, CouncilSession, CouncilTurn } from "@/lib/mc-types";

/**
 * The protocol's rounds, as the backend numbers them. Round 7 is the cross-response (stored last so older sessions keep their numbers)
 * but happened right after round 1; roundOrder() shows it there. Rounds 3 and 4 are targeted revisions (at most two).
 */
const ROUND_LABEL: Record<number, string> = {
  1: "Initial Positions (each executive, in parallel)",
  7: "Cross-Response & Deliberation (each executive answers the others)",
  2: "Chair's moderation",
  3: "Revision",
  4: "Second revision",
  5: "Synthesis of the options",
  6: "Red team",
};

const VERDICT: Record<CouncilResponse["verdict"], { text: string; tone: "green" | "amber" | "slate" }> = {
  agree: { text: "agrees and adds", tone: "green" },
  partly_agree: { text: "partly agrees", tone: "slate" },
  disagree: { text: "disagrees, offers an alternative", tone: "amber" },
};

/** What this executive said to each of the others (cross-response round only). */
function Responses({ responses }: { responses: CouncilResponse[] }) {
  return (
    <ul className="mt-2 space-y-2">
      {responses.map((r, i) => (
        <li key={i} className="rounded-md border border-slate-800 bg-slate-950/40 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] uppercase tracking-wider text-slate-500">to</span>
            <AgentChip id={r.to} />
            <Badge tone={VERDICT[r.verdict]?.tone ?? "slate"}>{VERDICT[r.verdict]?.text ?? r.verdict}</Badge>
          </div>
          <p className="mt-1 text-xs italic text-slate-500">Their point: {r.their_point}</p>
          {r.evaluation && <p className="mt-1 text-sm text-slate-300">{r.evaluation}</p>}
          <p className="mt-1 text-sm text-slate-200">
            <span className="text-cyan-300">{r.verdict === "disagree" ? "Alternative: " : "Adds: "}</span>
            {r.adds_or_alternative}
          </p>
        </li>
      ))}
    </ul>
  );
}

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
      {turn.responses && turn.responses.length > 0 && <Responses responses={turn.responses} />}
      {turn.reasoning && (
        <details className="group mt-2 rounded-md border border-slate-800 bg-black/20">
          <summary className="cursor-pointer select-none list-none rounded-md px-2 py-1 text-[11px] text-cyan-300 hover:bg-cyan-400/5 [&::-webkit-details-marker]:hidden">
            <span aria-hidden="true" className="mr-1 inline-block transition-transform group-open:rotate-90">▶</span>
            Show internal reasoning
          </summary>
          <p className="whitespace-pre-wrap border-t border-slate-800 px-2 py-2 text-xs text-slate-400">{turn.reasoning}</p>
        </details>
      )}
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
 * How the debate went: the participants and why, each round's turns, the chair's moderation notes, the conflicts (and
 * whether they resolved), the hard-constraint vetoes, and the dissent kept on the record.
 */
export default function DebateFlow({ session }: { session: CouncilSession }) {
  const turns = session.turns ?? [];
  const rounds = roundOrder(turns.map((t) => t.round));
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
            {round === 7 && (
              <p className="mb-2 text-xs text-slate-500">
                Each executive read the others&apos; initial positions, then answered each one: agreeing and adding something new, or naming the part
                they disagree with and offering an alternative.
              </p>
            )}
            <ul className="space-y-2">
              {turns.filter((t) => t.round === round).map((t, i) => (
                <TurnRow key={`${round}-${i}`} turn={t} />
              ))}
            </ul>
            {round === 2 && session.coo_moderation_notes && (
              <div className="mt-3 rounded-lg border border-cyan-400/20 bg-cyan-400/5 p-3">
                <p className="text-[11px] uppercase tracking-wider text-cyan-300/80">Chair&apos;s moderation notes</p>
                <p className="mt-1 text-sm text-slate-200">{session.coo_moderation_notes}</p>
              </div>
            )}
          </Card>
        ))
      )}

      {rounds.length > 0 && !rounds.includes(2) && session.coo_moderation_notes && (
        <Card>
          <SectionTitle>Chair&apos;s moderation notes</SectionTitle>
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
