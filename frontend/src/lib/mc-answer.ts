/**
 * Separates an agent's reply text into what a reader needs and the bookkeeping the BACKEND appends to it.
 *
 * Every executive agent builds its stored reply as the model's recommendation followed by notes that code adds (see
 * cao_agent.py, cmo_agent.py and the others, and rag/retrieval.py):
 *   - "\n\n---\nKnowledge base documents retrieved for this answer:\n- file (type, p. 3)" : the documents the search returned;
 *   - "\n\nAUTHORIZATION REQUEST (pending human approval; ...)\n- Action: ..." : a plain-text copy of the authorization request.
 * The first is internal detail. The second repeats what the authorization card already shows. Neither belongs in the first
 * thing a reader sees.
 *
 * ONLY those two exact, code-written shapes are recognised. The model's own prose is never parsed or cut: its
 * "Recommendation for human review:" line, its confidence statement and the "[Response reached the length limit...]" notice all stay
 * in the visible text, because the human needs them. If a shape does not match (an older stored answer, a changed backend),
 * nothing is removed and the text is shown whole: the worst case is today's behaviour, never lost text.
 */

export interface SplitResponse {
  /** The text to show by default. */
  body: string;
  /** The knowledge-base documents the note listed (one string per document), empty when the note was absent. */
  documents: string[];
}

// Written by the backend as the LAST thing appended, so it is anchored to the end of the text.
const DOCUMENTS_NOTE = /(?:\n\n|^)---\nKnowledge base documents retrieved for this answer:((?:\n- [^\n]*)*)\s*$/;
// A heading line, then only the "- ..." lines and the four-space-indented lines the backend writes under it.
const AUTHORIZATION_COPY = /(?:\n\n|^)AUTHORIZATION REQUEST \(pending human approval[^\n]*(?:\n(?:- |    )[^\n]*)*/;

/**
 * @param response  the agent's full reply text (AgentAnswer.response)
 * @param hasAuthorizationCard  true when the answer carries a structured authorization request, which is shown as a card.
 *   The plain-text copy is removed only then: with no card, it is the only place the request appears, so it stays.
 */
export function splitResponse(response: string, hasAuthorizationCard: boolean): SplitResponse {
  let text = response;
  let documents: string[] = [];

  const note = DOCUMENTS_NOTE.exec(text);
  if (note) {
    documents = note[1]
      .split("\n")
      .map((line) => line.replace(/^- /, "").trim())
      .filter(Boolean);
    text = text.slice(0, note.index);
  }
  if (hasAuthorizationCard) text = text.replace(AUTHORIZATION_COPY, "");

  const body = text.replace(/\n{3,}/g, "\n\n").trim();
  // Never return an empty body: if stripping left nothing, the original text is all there is to show.
  return { body: body || response.trim(), documents: body ? documents : [] };
}
