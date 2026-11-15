/**
 * Who sits at a Boardroom session (pure functions: no React, no network, so plain node can test them).
 *
 * The CEO decides who attends. Any combination of 2 to 4 of the four executives is allowed, and nobody is added on the
 * human's behalf. The only rule is the backend's: a council needs at least two different voices.
 */

export type SeatId = "cao" | "coo" | "cto" | "cmo";
export const SEATS: SeatId[] = ["cao", "coo", "cto", "cmo"];
export const MIN_SEATS = 2;
/** What is ticked when the page opens: the CAO and COO, the table the Boardroom has always opened with. */
export const DEFAULT_SEATS: SeatId[] = ["cao", "coo"];

/** Tick or untick one seat. The result is always in the fixed CAO, COO, CTO, CMO order, without duplicates. */
export function toggleSeat(current: SeatId[], seat: SeatId): SeatId[] {
  const next = new Set(current);
  if (next.has(seat)) next.delete(seat);
  else next.add(seat);
  return SEATS.filter((s) => next.has(s));
}

/** Can the Boardroom be convened with this table? */
export function canConvene(seats: SeatId[]): boolean {
  return new Set(seats.filter((s) => SEATS.includes(s))).size >= MIN_SEATS;
}

/** A line saying what is wrong with the table, or null when it is fine. */
export function seatProblem(seats: SeatId[]): string | null {
  const n = new Set(seats.filter((s) => SEATS.includes(s))).size;
  return n >= MIN_SEATS ? null : `Choose at least ${MIN_SEATS} executives (${n} chosen).`;
}

/**
 * Who chairs and who writes the options, as the backend decides it (agents/council.py assign_roles). The screen uses this
 * to say, before you convene, who stands in when the COO or the CAO is not at the table. Keep it in step with the backend:
 * the chair is the first seated of COO, CTO, CMO, CAO; the synthesizer is the CAO unless the CAO chairs, otherwise the first
 * other seated agent.
 */
export function assignRoles(seats: SeatId[]): { chair: SeatId; synthesizer: SeatId } {
  const chair = (["coo", "cto", "cmo", "cao"] as SeatId[]).find((s) => seats.includes(s)) ?? "coo";
  if (seats.includes("cao") && chair !== "cao") return { chair, synthesizer: "cao" };
  const synthesizer = SEATS.find((s) => seats.includes(s) && s !== chair) ?? chair;
  return { chair, synthesizer };
}

/** One sentence for the human about who chairs and synthesizes, empty when it is the usual COO and CAO. */
export function roleNote(seats: SeatId[]): string {
  if (!canConvene(seats)) return "";
  const { chair, synthesizer } = assignRoles(seats);
  const parts: string[] = [];
  if (chair !== "coo") parts.push(`${chair.toUpperCase()} will chair and run the red team (the COO is not at the table)`);
  if (synthesizer !== "cao") parts.push(`${synthesizer.toUpperCase()} will write the options (the CAO is not at the table)`);
  return parts.join("; ");
}

/**
 * What a RUNNING session is doing, in words for the CEO. The backend reports the phase; it is in memory on one server, so it can be
 * missing (null) after a restart, and then the generic line is used. A longer wait is explained, never left as a bare spinner.
 */
export const PHASE_MESSAGE: Record<string, string> = {
  initial_positions: "The executives are each forming their initial position…",
  cross_response: "Agents are reviewing each other's proposals and deliberating… this takes a little longer, and it is where the debate becomes a decision.",
  moderation: "The chair is mapping where the executives agree and disagree…",
  revisions: "The executives are answering the chair's follow-up question…",
  synthesis: "The options are being written for you…",
  red_team: "The chair is stress-testing each option to find how it could fail…",
};
export const GENERIC_PHASE_MESSAGE = "The Boardroom is in session. The executives are debating, which can take a minute or two.";

export function phaseMessage(phase: string | null | undefined): string {
  return (phase && PHASE_MESSAGE[phase]) || GENERIC_PHASE_MESSAGE;
}

/** The number the backend stores cross-response turns under. Rounds 1 to 6 keep their old meaning, so old sessions read the same. */
export const CROSS_ROUND = 7;

/** Rounds in the order the debate happened: the cross-response (7) comes right after the initial positions (1). */
export function roundOrder(rounds: number[]): number[] {
  const rank = (r: number) => (r === CROSS_ROUND ? 1.5 : r);
  return [...new Set(rounds)].sort((a, b) => rank(a) - rank(b));
}
