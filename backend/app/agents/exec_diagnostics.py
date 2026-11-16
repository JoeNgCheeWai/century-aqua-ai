"""
The CAO's, COO's and CMO's SELF-DIAGNOSIS tools (READ-ONLY), mirroring the CTO's diagnose_boardroom_session:

  CAO  diagnose_recommendation        why was a protocol rejected or judged unusable, and what data was missing
  COO  diagnose_operational_routing   why did a task not reach COO review, or why did a verdict fail
  CMO  diagnose_asset_request         why was an asset rejected, and which brand rule or parser check failed

Each reads ONE audit row (the one asked about, else the latest that did not go through) and DERIVES the cause IN CODE from the stored facts and
by re-running the same code checks. The report is plain facts; no model text is put in it, so it cannot carry an instruction back. The agent
code then puts the findings at the TOP of the first reply (exec_checks.diagnostic_block), so nobody is ever offered "I can investigate".

It changes nothing: SELECT only. What it cannot do: see Render's logs or the model's private reasoning, and it says so in ``limits``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.governance import admin_store, db

logger = logging.getLogger(__name__)

LIMITS = ("This reads the stored audit record and re-runs the code checks on it. It cannot see server logs or the model's private reasoning, "
          "so a cause marked 'likely' is the best fit to the record, not proof.")
_NO_DATA = re.compile(r"(?i)\b(no records? (?:loaded|available)|not provided|not supplied|missing|unknown|no data|cannot confirm|unable to confirm)\b[^.\n]{0,80}")
_CUT_OFF = "[Response reached the length limit"


def _row_sync(role: str, audit_id: int | None) -> dict[str, Any] | None:
    cols = ('"id","agentId","query","response","constitutionalScore","sourcesCited","reasoningChain","authorizationRequest",'
            '"humanAction","humanActionBy","timestamp"')
    with db.transaction() as conn:
        cur = conn.cursor()
        if audit_id is not None:
            cur.execute(f'SELECT {cols} FROM "AgentAuditLog" WHERE "id"=%s AND "agentId"=%s', (audit_id, role))
        else:
            cur.execute(f'SELECT {cols} FROM "AgentAuditLog" WHERE "agentId"=%s ORDER BY (CASE WHEN "humanAction"=CAST(%s AS "HumanAction") THEN 0 '
                        f'WHEN "authorizationRequest" IS NULL THEN 1 ELSE 2 END), "timestamp" DESC LIMIT 1', (role, "Rejected"))
        rows = db.fetch_all(cur)
    return rows[0] if rows else None


def _report(ok: bool, role: str, steps: list[str], cause: str, fix: str, confidence: str, facts: dict[str, Any], audit_id: int | None = None) -> dict[str, Any]:
    return {"ok": ok, "role": role, "audit_id": audit_id, "steps": steps, "cause": cause, "fix": fix, "confidence": confidence, "facts": facts, "limits": LIMITS}


def _request(row: dict[str, Any]) -> dict[str, Any] | None:
    req = admin_store.parse_json(row.get("authorizationRequest"))
    return req if isinstance(req, dict) else None


def _common(row: dict[str, Any]) -> dict[str, Any]:
    response = str(row.get("response") or "")
    return {"audit_id": row["id"], "decision": str(row["humanAction"]), "decided_by": row.get("humanActionBy"), "score": row["constitutionalScore"],
            "cut_off": _CUT_OFF in response, "chain_steps": len(row.get("reasoningChain") or []), "has_request": row.get("authorizationRequest") is not None}


def derive_cao(row: dict[str, Any]) -> dict[str, Any]:
    """Why a CAO protocol was rejected or unusable, and what data was missing. Pure: no database, no model."""
    facts = _common(row)
    facts["sources_cited"] = len(row.get("sourcesCited") or [])
    missing = sorted({m.group(0).strip() for m in _NO_DATA.finditer(str(row.get("response") or ""))})[:5]
    facts["data_gaps_stated"] = missing
    steps = [f"read audit entry #{row['id']}"]
    if facts["cut_off"]:
        return _report(True, "cao", steps, "The answer was cut off at the length limit, so the protocol is incomplete and cannot be used as written.",
                       "Ask again for one protocol at a time (shorter), or raise CAO_MAX_TOKENS on Render.", "confirmed", facts, row["id"])
    if facts["chain_steps"] == 0:
        return _report(True, "cao", steps, "The answer was not in the structured format (no reasoning chain), so it was kept as plain text and no protocol could be checked.",
                       "Ask again; if it repeats, the model is not following the output format.", "likely", facts, row["id"])
    if facts["decision"] == "Rejected":
        why = ("The data gaps it stated: " + "; ".join(missing)) if missing else "The record does not say why the reviewer rejected it."
        return _report(True, "cao", steps, f"A person rejected it (by {facts['decided_by'] or 'unknown'}). {why}",
                       "Supply the missing data (records, species, life stage, units) and ask again." if missing else "Ask the reviewer for the reason, then ask again with that detail.",
                       "confirmed" if missing else "likely", facts, row["id"])
    if facts["score"] < 70:
        return _report(True, "cao", steps, f"It scored {facts['score']}, under the review threshold of 70, so it was flagged as unusable until reviewed.",
                       "Review the flagged answer; the findings are in the audit entry.", "confirmed", facts, row["id"])
    if missing:
        return _report(True, "cao", steps, "It was usable but admitted missing data: " + "; ".join(missing), "Load or supply that data, then ask again.", "likely", facts, row["id"])
    return _report(True, "cao", steps, "Nothing in the stored record shows a failure: it was not rejected, not cut off, not flagged and states no data gaps.",
                   "If it still looks wrong, say what you expected and I will compare.", "likely", facts, row["id"])


def derive_coo(row: dict[str, Any]) -> dict[str, Any]:
    """Why a task did not reach COO review, or why a COO verdict failed. Re-runs the real classifier on the stored request."""
    from app.agents import operational_approval, plan_checks, safe_to_implement

    facts = _common(row)
    steps = [f"read audit entry #{row['id']} (proposed by {row['agentId']})"]
    request = _request(row)
    if request is None:
        return _report(True, "coo", steps, "That entry has no authorization request, so there was nothing to route to review.", "Nothing to fix: only a request is reviewed.", "confirmed", facts, row["id"])
    review = request.get("operational_review") if isinstance(request.get("operational_review"), dict) else None
    facts["route"] = review.get("route") if review else None
    if review:
        coo = review.get("coo") if isinstance(review.get("coo"), dict) else {}
        reasons = [str(r)[:200] for r in (review.get("reasons") or [])][:4]
        why = coo.get("failure") or "; ".join(f"{k}: {v}" for k, v in (coo.get("reasons") or {}).items() if v)[:400]
        steps.append("read the stored COO review")
        if review.get("route") == "coo_unavailable":
            return _report(True, "coo", steps, f"The COO review itself failed: {why or 'no verdict was produced'}. The task stayed with the CEO.",
                           "Retry later; a timeout, an error or an unreadable verdict is never an approval.", "confirmed", facts, row["id"])
        if review.get("route") == "coo_declined":
            return _report(True, "coo", steps, f"The COO declined it: {why or '; '.join(reasons) or 'reason not recorded'}.", "Address that concern and propose it again, or approve it yourself.", "confirmed", facts, row["id"])
        return _report(True, "coo", steps, "It did reach COO review and was approved under delegation.", "Nothing failed.", "confirmed", facts, row["id"])
    if not operational_approval.enabled():
        return _report(True, "coo", steps, f"COO-led approval is switched OFF ({operational_approval.ENV_SWITCH} is not on), so no task goes to the COO.",
                       f"Set {operational_approval.ENV_SWITCH}=on on Render if you want delegated review.", "confirmed", facts, row["id"])
    if str(row["agentId"]) not in operational_approval.ORIGINATORS:
        return _report(True, "coo", steps, f"Tasks proposed by '{row['agentId']}' are never routed to the COO (only {', '.join(operational_approval.ORIGINATORS)}).", "None: this is by design.", "confirmed", facts, row["id"])
    plan = request.get("execution_plan")
    if not isinstance(plan, dict):
        return _report(True, "coo", steps, "It has no execution plan (no pull request), so it is a recommendation for a person, not an operational task.", "Approve it on the Authorizations page.", "confirmed", facts, row["id"])
    check = plan_checks.check_plan(plan, None)
    verdict = safe_to_implement.classify(request, check.passed)
    facts["self_check_passed"] = check.passed
    steps.append("re-ran the plan self-check and the safe-to-implement classifier on the stored request")
    if not verdict.safe:
        return _report(True, "coo", steps, "Code classified it as not one of the small safe tasks: " + "; ".join(verdict.reasons[:4]) + ".", "It needs the CEO. Split it into smaller safe tasks if you want delegated review.", "confirmed", facts, row["id"])
    return _report(True, "coo", steps, "Code says it is safe, yet it has no stored review, so it likely was not read before the existing files it touches, or the review was interrupted.",
                   "Ask the proposing agent to read the files again and re-propose.", "likely", facts, row["id"])


def derive_cmo(row: dict[str, Any]) -> dict[str, Any]:
    """Why an asset request was rejected, and which brand rule or parser check failed. Re-runs the CMO's claim and risk checks on the stored request."""
    from app.agents import cmo_authorization

    facts = _common(row)
    steps = [f"read audit entry #{row['id']}"]
    request = _request(row)
    if request is None:
        why = "the answer was cut off before the request was complete" if facts["cut_off"] else "the model's JSON did not parse, or no valid request survived the parser"
        return _report(True, "cmo", steps, f"No asset request was stored: {why}.", "Ask again with the asset, channel and audience named.", "likely", facts, row["id"])
    content = request.get("proposed_content") or ""
    steps.append("re-ran the brand and claim checks on the stored content")
    floor = cmo_authorization.risk_floor(content, request.get("deployment_commands") or [], request.get("claims_to_verify") or [])
    claims = cmo_authorization.detect_claims(content)
    rules = [name for name, group in (("a critical claim (guarantee, cure, risk-free)", cmo_authorization._CRITICAL), ("a high-risk claim (comparison, certification, money)", cmo_authorization._HIGH))
             if any(re.search(p, content, re.I) for p in group)]
    facts.update({"risk_floor": floor, "claims_found": len(claims), "rules_hit": rules, "mockups": len(request.get("mockup_urls") or [])})
    if facts["decision"] == "Rejected":
        parts = (["it matched " + " and ".join(rules)] if rules else []) + ([f"{len(claims)} factual claim(s) needed substantiation"] if claims else [])
        return _report(True, "cmo", steps, f"A person rejected it (by {facts['decided_by'] or 'unknown'}). The checks show: " + ("; ".join(parts) if parts else "no brand rule or claim check failed, so the reason was the reviewer's own") + ".",
                       "Remove or substantiate the flagged claims, then submit again." if parts else "Ask the reviewer for the reason.", "confirmed" if parts else "likely", facts, row["id"])
    if rules or claims:
        return _report(True, "cmo", steps, f"It is held for a person at {floor} risk: " + ("it matched " + " and ".join(rules) + "; " if rules else "") + f"{len(claims)} claim(s) are listed to substantiate.",
                       "Substantiate or remove the listed claims.", "confirmed", facts, row["id"])
    return _report(True, "cmo", steps, "No brand rule or parser check failed on the stored request; it is simply waiting for a decision.", "Decide it on the Authorizations page.", "likely", facts, row["id"])


DERIVE = {"cao": derive_cao, "coo": derive_coo, "cmo": derive_cmo}


async def diagnose(role: str, audit_id: int | None = None) -> dict[str, Any]:
    """The report for ``role``. NEVER RAISES. The role is code's: a role can only ever read its own rows."""
    try:
        if role not in DERIVE:
            return _report(False, role, [], "This role has no diagnostic.", "", "none", {})
        if not db.is_configured():
            return _report(False, role, [], "The audit database is not configured, so nothing can be diagnosed.", "Configure GOVERNANCE_DATABASE_URL.", "none", {})
        row = await db.run(_row_sync, role, audit_id)
        if row is None:
            return _report(False, role, [f"looked for {'entry #' + str(audit_id) if audit_id else 'a recent entry'} for {role}"], "No matching audit entry was found for your role.",
                           "Give the audit number shown on the answer.", "none", {})
        return DERIVE[role](row)
    except Exception as exc:
        logger.error("exec_diagnostics: %s diagnosis failed (%s)", role, type(exc).__name__)
        return _report(False, role, [], "The diagnostic could not be completed.", "", "none", {})


def render_report(report: dict[str, Any]) -> str:
    """The report as text for the tool result (facts only)."""
    return (f"DIAGNOSTIC REPORT ({report['role'].upper()}) steps: {'; '.join(report.get('steps', [])) or 'none'}\n"
            f"cause ({report.get('confidence')}): {report.get('cause')}\nfix: {report.get('fix')}\nfacts: {report.get('facts')}\nlimits: {report.get('limits')}")
