"""
READ-ONLY, ROLE-SCOPED access to the FarmRecord table (water quality, feeding, mortality, sampling, inventory, cost).

THE SCOPE IS ENFORCED HERE, ON THE SERVER, not by the model: the role comes from the caller's code (never from a tool argument), and
the SQL only ever names kinds that role owns. The CAO cannot read inventory or cost; the COO cannot read water quality, feeding,
mortality or sampling. Any other role gets nothing. There is no write path in this module: no INSERT, UPDATE or DELETE.

Bounded: at most MAX_ROWS rows, at most MAX_DAYS back, every text field cut and secret-looking text masked. Never raises:
a problem is an "ERROR: ..." line. An EMPTY table is reported as "no records loaded", never guessed around.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.execution_plan import redact_secrets
from app.governance import db

logger = logging.getLogger(__name__)

ROLE_KINDS: dict[str, tuple[str, ...]] = {
    "cao": ("water_quality", "feeding", "mortality", "sampling"),
    "coo": ("inventory", "cost"),
}
MAX_ROWS = 50
DEFAULT_DAYS, MAX_DAYS = 30, 365
MAX_FIELD_CHARS = 200


def kinds_for(role: str) -> tuple[str, ...]:
    return ROLE_KINDS.get(role, ())


def _cut(value: Any) -> str:
    text = " ".join(str(value).split()) if value is not None else ""
    return redact_secrets(text)[:MAX_FIELD_CHARS]


def _query_sync(kinds: tuple[str, ...], parameter: str | None, site: str | None, since: datetime) -> list[dict[str, Any]]:
    marks = ",".join(["%s"] * len(kinds))
    sql = ('SELECT "id","kind","site","unit","parameter","value","valueText","measuredUnit","recordedAt","source","notes" FROM "FarmRecord" '
           f'WHERE "kind" IN ({marks}) AND "recordedAt" >= %s')
    params: list[Any] = [*kinds, db.naive_utc(since)]
    if parameter:
        sql += ' AND "parameter" = %s'
        params.append(parameter)
    if site:
        sql += ' AND "site" = %s'
        params.append(site)
    sql += ' ORDER BY "recordedAt" DESC, "id" DESC LIMIT %s'
    params.append(MAX_ROWS)
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        return db.fetch_all(cur)


def _line(row: dict[str, Any]) -> str:
    value = row.get("value") if row.get("value") is not None else row.get("valueText")
    where = " / ".join(x for x in (_cut(row.get("site")), _cut(row.get("unit"))) if x)
    note = f" - {_cut(row.get('notes'))}" if row.get("notes") else ""
    return (f"[{row['id']}] {row['recordedAt']:%Y-%m-%d %H:%M} | {row['kind']} | {_cut(row.get('parameter'))} = {_cut(value)} "
            f"{_cut(row.get('measuredUnit'))} | {where or 'no site'} | source: {_cut(row.get('source')) or 'not stated'}{note}")


def read_records(role: str, kind: str | None = None, parameter: str | None = None, site: str | None = None,
                 days: int | None = None) -> tuple[str, int | None]:
    """(text for the model, row count or None on error). ``role`` is decided by CODE. A kind outside the role's scope is refused."""
    try:
        allowed = kinds_for(role)
        if not allowed:
            return "ERROR: this role has no farm-record access", None
        if kind is not None and kind not in allowed:
            return f"ERROR: '{_cut(kind)}' records are outside your scope. You may read: {', '.join(allowed)}", None
        chosen = (kind,) if kind else allowed
        span = DEFAULT_DAYS if not isinstance(days, int) or isinstance(days, bool) else max(1, min(MAX_DAYS, days))
        if not db.is_configured():
            return "ERROR: the records database is not configured, so no records can be read", None
        since = datetime.now(timezone.utc) - timedelta(days=span)
        rows = _query_sync(chosen, _cut(parameter) or None, _cut(site) or None, since)
        if not rows:
            return (f"No records loaded for {', '.join(chosen)} in the last {span} days. Say so plainly and do not estimate a figure.", 0)
        head = f"{len(rows)} record(s), newest first, kinds: {', '.join(chosen)}, last {span} days (at most {MAX_ROWS} shown):"
        return head + "\n" + "\n".join(_line(r) for r in rows), len(rows)
    except Exception as exc:  # a read tool must never take the app down
        logger.error("farm_records: read failed (%s)", type(exc).__name__)
        return "ERROR: the records could not be read", None
