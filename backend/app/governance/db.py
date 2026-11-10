"""
Database access for the governance layer (pg8000, pure Python).

The backend reads GOVERNANCE_DATABASE_URL, falling back to DATABASE_URL. Use the
same database the frontend uses (Neon in production). The driver is pure Python
on purpose: no native file for Windows Application Control to block.

Every function here is synchronous and short; async callers run them with
``asyncio.to_thread`` (see ``run``). Each call opens its own connection, because
the backend runs on a small instance and these writes are rare (one per executive
answer). A transaction is all-or-nothing: ``transaction()`` commits on success and
rolls back on any exception.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import ssl
from collections.abc import Callable, Iterator
from typing import Any, TypeVar
from urllib.parse import parse_qs, unquote, urlsplit

logger = logging.getLogger(__name__)

T = TypeVar("T")
CONNECT_TIMEOUT_SECONDS = 10.0


class GovernanceDBError(Exception):
    """The governance database is not configured or a query failed. Never carries the URL."""


def database_url() -> str:
    return os.getenv("GOVERNANCE_DATABASE_URL") or os.getenv("DATABASE_URL") or ""


def is_configured() -> bool:
    return bool(database_url())


def parse_url(url: str) -> dict[str, Any]:
    """Split a postgres:// URL into pg8000 connect arguments (no network access)."""
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise GovernanceDBError("The database URL must start with postgresql:// and include a host.")
    query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    sslmode = (query.get("sslmode") or "").lower()
    host = parts.hostname
    local = host in ("localhost", "127.0.0.1", "::1")
    # sslmode=disable (the local `prisma dev` server) must skip the SSL handshake, or pg8000
    # waits forever. Everything else, including every hosted database, uses verified TLS.
    use_tls = not (sslmode == "disable" or (local and sslmode == ""))
    return {
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "host": host,
        "port": parts.port or 5432,
        "database": unquote(parts.path.lstrip("/")) or "postgres",
        "tls": use_tls,
    }


def connect():
    """Open a new connection. Raises GovernanceDBError (without secrets) on any failure."""
    url = database_url()
    if not url:
        raise GovernanceDBError("GOVERNANCE_DATABASE_URL / DATABASE_URL is not configured.")
    args = parse_url(url)
    tls = args.pop("tls")
    try:
        import pg8000.dbapi as pg

        return pg.connect(
            **args,
            timeout=CONNECT_TIMEOUT_SECONDS,
            ssl_context=ssl.create_default_context() if tls else False,
        )
    except GovernanceDBError:
        raise
    except Exception as exc:
        raise GovernanceDBError(f"Could not connect to the governance database ({type(exc).__name__}).") from exc


@contextlib.contextmanager
def transaction(connection_factory: Callable[[], Any] = connect) -> Iterator[Any]:
    """One database transaction: commits if the block finishes, rolls back on any error."""
    conn = connection_factory()
    try:
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def naive_utc(moment: Any) -> Any:
    """
    Prisma's DateTime columns are TIMESTAMP(3) WITHOUT time zone and are always read back as UTC.
    Passing a timezone-aware value would let the session time zone shift what is stored, so
    convert to naive UTC before writing.
    """
    from datetime import timezone

    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def json_param(value: Any) -> str:
    """JSON text for a jsonb column (use with CAST(%s AS jsonb))."""
    return json.dumps(value, ensure_ascii=False, default=str)


def text_array_param(values: list[str]) -> str:
    """
    A Postgres text[] literal such as {"a","b"} (use with CAST(%s AS text[])). Sent as text
    so an EMPTY list works: a Python [] gives the driver no element type to infer.
    """

    def quote(item: str) -> str:
        return '"' + str(item).replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "{" + ",".join(quote(item) for item in values) + "}"


def fetch_all(cursor: Any) -> list[dict[str, Any]]:
    names = [column[0] for column in cursor.description or []]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


async def run(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking database function without blocking the event loop."""
    return await asyncio.to_thread(function, *args, **kwargs)


_SAFE_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def valid_agent_id(agent_id: str) -> bool:
    return bool(_SAFE_AGENT_ID.match(agent_id or ""))
