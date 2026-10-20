"""Employee correction requests: an employee asks for a missing/wrong punch, an admin approves or rejects."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from punctual.auth import Me, current_user, require_admin
from punctual.clock import (
    day_bounds,
    local_date,
    local_hm,
    local_str,
    parse_local,
    parse_utc,
    utc_str,
)
from punctual.corrections import insert_manual_punch
from punctual.db import connect
from punctual.i18n import t
from punctual.punch import forgotten_message
from punctual.state import ShiftState, effective_state

logger: logging.Logger = logging.getLogger(__name__)

MAX_PENDING_PER_EMPLOYEE: int = 5
MAX_AGE_DAYS: int = 14

router: APIRouter = APIRouter(prefix="/api/requests", tags=["requests"])


class RequestIn(BaseModel):
    """What the employee asks for. `local_time` is local wall time."""

    site_id: int
    kind: Literal["in", "out"]
    local_time: str
    note: str = Field(min_length=3, max_length=500)


class DecisionBody(BaseModel):
    """Admin decision. `reason` is required to reject; `local_time` optionally overrides the requested time."""

    reason: str = Field(default="", max_length=500)
    local_time: str | None = None


class RequestOut(BaseModel):
    """A correction request as shown in the UI."""

    id: int
    employee_id: int
    employee: str
    site: str
    kind: str
    local_time: str
    note: str
    status: str
    created_at: str
    decision_note: str | None
    day_punches: list[str] = []


def _rows(conn, where: str, args: tuple) -> list[RequestOut]:
    rows = conn.execute(
        "SELECT r.*, e.name AS employee, s.name AS site FROM correction_requests r "
        "JOIN employees e ON e.id = r.employee_id JOIN sites s ON s.id = r.site_id "
        f"WHERE {where} ORDER BY r.id DESC",
        args,
    ).fetchall()
    out: list[RequestOut] = []
    for r in rows:
        day = local_date(r["ts_utc"])
        start, end = day_bounds(day, day)
        same_day = conn.execute(
            "SELECT kind, ts_utc FROM live_punches WHERE employee_id = ? AND ts_utc >= ? AND ts_utc < ? "
            "ORDER BY ts_utc",
            (r["employee_id"], utc_str(start), utc_str(end)),
        ).fetchall()
        out.append(
            RequestOut(
                id=r["id"],
                employee_id=r["employee_id"],
                employee=r["employee"],
                site=r["site"],
                kind=r["kind"],
                local_time=local_str(r["ts_utc"]),
                note=r["note"],
                status=r["status"],
                created_at=r["created_at"],
                decision_note=r["decision_note"],
                day_punches=[
                    f"{t('kind.' + p['kind'])} {local_hm(p['ts_utc'])}"
                    for p in same_day
                ],
            )
        )
    return out


@router.post("", status_code=201)
def create_request(
    body: RequestIn, user: Annotated[Me, Depends(current_user)]
) -> dict[str, int]:
    """Employee asks for a missing or wrong punch to be added.

    A pending request counts as a provisional punch (see `effective_state`): declaring the departure of a
    forgotten shift unblocks clocking, and declaring a forgotten arrival opens the shift. The departure of the
    current forgotten shift is accepted however old it is.

    Raises:
        HTTPException: 422 for a future/too old time or an unassigned site, 409 for an arrival while already at
            work (or while a forgotten shift is undeclared), 429 when too many are pending.
    """
    ts: datetime = parse_local(body.local_time)
    with connect() as conn:
        st: ShiftState = effective_state(conn, user.id)
        newer: bool = st.open_since is None or ts > st.open_since
        closes_forgotten: bool = (
            st.state == "forgotten" and body.kind == "out" and newer
        )
        if st.state == "forgotten" and body.kind == "out" and not newer:
            raise HTTPException(status_code=422, detail=t("err.out_before_in"))
        if not closes_forgotten and ts < datetime.now(UTC) - timedelta(
            days=MAX_AGE_DAYS
        ):
            raise HTTPException(
                status_code=422,
                detail=t("err.correction_too_old", days=MAX_AGE_DAYS),
            )
        if body.kind == "in" and st.state != "closed" and newer:
            raise HTTPException(
                status_code=409,
                detail=forgotten_message(st)
                if st.state == "forgotten"
                else t("err.already_working", since=local_hm(st.open_since)),
            )
        if (
            conn.execute(
                "SELECT 1 FROM employee_sites WHERE employee_id = ? AND site_id = ?",
                (user.id, body.site_id),
            ).fetchone()
            is None
        ):
            raise HTTPException(status_code=422, detail=t("err.site_not_assigned"))
        pending: int = conn.execute(
            "SELECT COUNT(*) FROM correction_requests WHERE employee_id = ? AND status = 'pending'",
            (user.id,),
        ).fetchone()[0]
        if pending >= MAX_PENDING_PER_EMPLOYEE:
            raise HTTPException(status_code=429, detail=t("err.too_many_pending"))
        cur = conn.execute(
            "INSERT INTO correction_requests (employee_id, site_id, kind, ts_utc, note) VALUES (?, ?, ?, ?, ?)",
            (user.id, body.site_id, body.kind, utc_str(ts), body.note.strip()),
        )
    logger.warning(f"correction request {cur.lastrowid} by employee={user.id}")
    return {"id": int(cur.lastrowid or 0)}


@router.get("/mine", response_model=list[RequestOut])
def my_requests(user: Annotated[Me, Depends(current_user)]) -> list[RequestOut]:
    """The employee's own requests, newest first."""
    with connect() as conn:
        return _rows(conn, "r.employee_id = ?", (user.id,))[:20]


@router.get("/pending-count")
def pending_count(_: Annotated[Me, Depends(require_admin)]) -> dict[str, int]:
    """Number of requests waiting for a decision (for the nav badge)."""
    with connect() as conn:
        return {
            "count": conn.execute(
                "SELECT COUNT(*) FROM correction_requests WHERE status = 'pending'"
            ).fetchone()[0]
        }


@router.get("", response_model=list[RequestOut])
def list_requests(
    _: Annotated[Me, Depends(require_admin)],
    status: Literal["pending", "approved", "rejected"] = "pending",
) -> list[RequestOut]:
    """Requests by status, with the employee's other punches that day for context."""
    with connect() as conn:
        return _rows(conn, "r.status = ?", (status,))


@router.post("/{request_id}/approve")
def approve(
    request_id: int, body: DecisionBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Create the requested punch (source=admin, note keeps the employee's words) and close the request.

    Raises:
        HTTPException: 404 if missing, 409 if already decided.
    """
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM correction_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail=t("err.request_not_found"))
        if r["status"] != "pending":
            raise HTTPException(status_code=409, detail=t("err.already_decided"))
        ts: datetime = (
            parse_local(body.local_time) if body.local_time else parse_utc(r["ts_utc"])
        )
        note: str = t("note.employee_request", note=r["note"]) + (
            t("note.adjusted_time", reason=body.reason)
            if body.local_time and body.reason
            else ""
        )
        pid: int = insert_manual_punch(
            conn, r["employee_id"], r["site_id"], r["kind"], ts, note, admin.id
        )
        conn.execute(
            "UPDATE correction_requests SET status = 'approved', decided_by = ?, decided_at = ?, punch_id = ?, "
            "decision_note = ? WHERE id = ?",
            (
                admin.id,
                utc_str(datetime.now(UTC)),
                pid,
                body.reason or None,
                request_id,
            ),
        )
    logger.warning(f"request {request_id} approved by admin={admin.id} punch={pid}")
    return {"id": request_id, "punch_id": pid}


@router.post("/{request_id}/reject")
def reject(
    request_id: int, body: DecisionBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Reject a request; a reason is required and shown to the employee.

    Raises:
        HTTPException: 404 if missing, 409 if already decided, 422 without a reason.
    """
    if len(body.reason.strip()) < 3:
        raise HTTPException(status_code=422, detail=t("err.reject_reason_required"))
    with connect() as conn:
        r = conn.execute(
            "SELECT status FROM correction_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail=t("err.request_not_found"))
        if r["status"] != "pending":
            raise HTTPException(status_code=409, detail=t("err.already_decided"))
        conn.execute(
            "UPDATE correction_requests SET status = 'rejected', decided_by = ?, decided_at = ?, decision_note = ? "
            "WHERE id = ?",
            (
                admin.id,
                utc_str(datetime.now(UTC)),
                body.reason.strip(),
                request_id,
            ),
        )
    logger.warning(f"request {request_id} rejected by admin={admin.id}")
    return {"id": request_id}
