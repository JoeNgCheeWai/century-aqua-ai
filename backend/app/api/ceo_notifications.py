"""
The CEO's notifications (GET /api/ceo-notifications, POST /api/ceo-notifications/{id}/read): what Mission Control's bell and "Needs your decision"
list read. Internal only, gated by INTERNAL_COUNCIL_KEY like every other Mission Control endpoint (no agent key opens it).

    status   open (default: unread + read), unread, read, resolved, all
    limit    1-100, default 20      offset   paging

Returns {items, total, unread, limit, offset}. ``unread`` is the badge number and counts every unread notification whatever the filter. A database
problem is a 503, never an empty list: an empty list would tell the CEO that nothing needs them. Marking a notification READ is a convenience and
changes nothing about the task. Settling it happens when the task itself is decided (api/authorization.apply_decision), never from here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.security import require_actor, require_council_key
from app.governance import ceo_notifications, db

logger = logging.getLogger(__name__)
router = APIRouter()

FILTERS = ("open", "unread", "read", "resolved", "all")


@router.get("/api/ceo-notifications")
async def list_ceo_notifications(
    status: str = Query(default="open", max_length=12),
    limit: int = Query(default=ceo_notifications.DEFAULT_LIMIT, ge=1, le=ceo_notifications.MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    x_internal_council_key: str | None = Header(default=None),
):
    require_council_key(x_internal_council_key)  # before anything else
    if status not in FILTERS:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(FILTERS)}.")
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Notifications unavailable: the governance database is not configured.")
    try:
        items, total, unread = await ceo_notifications.list_notifications(None if status == "all" else status, limit, offset)
    except Exception as exc:
        logger.error("ceo-notifications: could not read (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Notifications unavailable: could not read the database.") from exc
    return {"items": items, "total": total, "unread": unread, "limit": limit, "offset": offset}


@router.post("/api/ceo-notifications/{notification_id}/read")
async def mark_read(
    notification_id: int,
    x_internal_council_key: str | None = Header(default=None),
    x_admin_id: str | None = Header(default=None),
):
    require_council_key(x_internal_council_key)
    require_actor(x_admin_id, "X-Admin-Id", required=True)  # a state change must be attributable, like every other one
    if notification_id < 1 or notification_id > 2_000_000_000:
        raise HTTPException(status_code=422, detail="notification_id is not valid.")
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Notifications unavailable: the governance database is not configured.")
    changed = await ceo_notifications.mark(notification_id, "read")
    return {"id": notification_id, "marked_read": changed}
