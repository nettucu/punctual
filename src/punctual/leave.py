"""Vacation, medical, unpaid leave and free-day requests: an employee asks for days off, an admin approves or rejects.

Only working days (no weekends, no public holidays) are covered by an approved request; reports mark them with the
request's code.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from punctual.auth import Me, current_user, require_admin
from punctual.clock import MAX_RANGE_DAYS, today, utc_str
from punctual.db import connect
from punctual.holidays import holidays_between, working_days
from punctual.i18n import t

logger: logging.Logger = logging.getLogger(__name__)

MAX_PENDING_PER_EMPLOYEE: int = 5
MAX_AGE_DAYS: int = 14
MAX_AGE_DAYS_MEDICAL: int = 60  # a medical certificate often arrives after the fact
MAX_AHEAD_DAYS: int = 730  # Easter is only exact to 2099 (see holidays.orthodox_easter)

router: APIRouter = APIRouter(prefix="/api", tags=["leave"])


class LeaveIn(BaseModel):
    """What the employee asks for: `kind` co = vacation, cm = medical leave, zl = free day, cfp = unpaid leave; dates are inclusive local days."""

    kind: Literal["co", "cm", "zl", "cfp"]
    date_from: date
    date_to: date
    note: str = Field(default="", max_length=500)


class AdminLeaveIn(BaseModel):
    """An admin marks days as leave for an employee; approved at once, so `note` (the reason) is required."""

    employee_id: int
    kind: Literal["co", "cm", "zl", "cfp"]
    date_from: date
    date_to: date
    note: str = Field(min_length=3, max_length=500)


class DecisionBody(BaseModel):
    """Admin decision; `reason` is required to reject."""

    reason: str = Field(default="", max_length=500)


class LeaveOut(BaseModel):
    """A leave request as shown in the UI. `days` is the number of working days it covers."""

    id: int
    employee_id: int
    employee: str
    kind: str
    code: str
    date_from: str
    date_to: str
    days: int
    note: str
    status: str
    created_at: str
    decision_note: str | None


class LeaveDay(BaseModel):
    """One working day an employee is off on an approved request."""

    employee_id: int
    employee: str
    day: str
    code: str


def _out(r) -> LeaveOut:
    return LeaveOut(
        id=r["id"],
        employee_id=r["employee_id"],
        employee=r["employee"],
        kind=r["kind"],
        code=t(f"leave.code.{r['kind']}"),
        date_from=r["date_from"],
        date_to=r["date_to"],
        days=len(
            working_days(
                date.fromisoformat(r["date_from"]), date.fromisoformat(r["date_to"])
            )
        ),
        note=r["note"],
        status=r["status"],
        created_at=r["created_at"],
        decision_note=r["decision_note"],
    )


def _rows(conn, where: str, args: tuple) -> list[LeaveOut]:
    return [
        _out(r)
        for r in conn.execute(
            "SELECT l.*, e.name AS employee FROM leave_requests l JOIN employees e ON e.id = l.employee_id "
            f"WHERE {where} ORDER BY l.date_from DESC, l.id DESC",
            args,
        )
    ]


def leave_days(
    conn, d_from: date, d_to: date, employee_id: int | None = None
) -> list[LeaveDay]:
    """Working days in [d_from, d_to] covered by approved requests, one row per employee and day.

    Args:
        conn: Open connection.
        d_from: First local day.
        d_to: Last local day (inclusive).
        employee_id: Restrict to one employee.
    """
    sql: str = (
        "SELECT l.*, e.name AS employee FROM leave_requests l JOIN employees e ON e.id = l.employee_id "
        "WHERE l.status = 'approved' AND l.date_to >= ? AND l.date_from <= ?"
    )
    args: list[object] = [d_from.isoformat(), d_to.isoformat()]
    if employee_id is not None:
        sql += " AND l.employee_id = ?"
        args.append(employee_id)
    out: dict[tuple[int, date], LeaveDay] = {}
    for r in conn.execute(sql + " ORDER BY l.id", args):
        lo: date = max(d_from, date.fromisoformat(r["date_from"]))
        hi: date = min(d_to, date.fromisoformat(r["date_to"]))
        for d in working_days(lo, hi):
            out[(r["employee_id"], d)] = LeaveDay(
                employee_id=r["employee_id"],
                employee=r["employee"],
                day=d.isoformat(),
                code=t(f"leave.code.{r['kind']}"),
            )
    return sorted(out.values(), key=lambda x: (x.employee, x.day))


@router.get("/holidays")
def holidays(
    _: Annotated[Me, Depends(current_user)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> dict[str, str]:
    """Public holidays in a date range, ISO day to name."""
    if date_to < date_from or (date_to - date_from).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=t("err.invalid_range"))
    return {d.isoformat(): n for d, n in holidays_between(date_from, date_to).items()}


@router.post("/leave", status_code=201)
def create_leave(
    body: LeaveIn, user: Annotated[Me, Depends(current_user)]
) -> dict[str, int]:
    """Employee asks for vacation, medical leave or a free day.

    Raises:
        HTTPException: 422 for a bad range, a range without a working day or one starting too far back, 409 when it
            overlaps another pending/approved request, 429 when too many are pending.
    """
    if body.date_to < body.date_from or (body.date_to - body.date_from).days > 60:
        raise HTTPException(status_code=422, detail=t("err.leave_range_max"))
    if body.date_to > today() + timedelta(days=MAX_AHEAD_DAYS):
        raise HTTPException(status_code=422, detail=t("err.leave_too_far_ahead"))
    max_age: int = MAX_AGE_DAYS_MEDICAL if body.kind == "cm" else MAX_AGE_DAYS
    if body.date_from < today() - timedelta(days=max_age):
        raise HTTPException(
            status_code=422,
            detail=t("err.leave_too_old", days=max_age),
        )
    if not working_days(body.date_from, body.date_to):
        raise HTTPException(
            status_code=422,
            detail=t("err.leave_no_working_days"),
        )
    with connect() as conn:
        if conn.execute(
            "SELECT 1 FROM leave_requests WHERE employee_id = ? AND status <> 'rejected' "
            "AND date_to >= ? AND date_from <= ?",
            (user.id, body.date_from.isoformat(), body.date_to.isoformat()),
        ).fetchone():
            raise HTTPException(
                status_code=409,
                detail=t("err.leave_overlap"),
            )
        pending: int = conn.execute(
            "SELECT COUNT(*) FROM leave_requests WHERE employee_id = ? AND status = 'pending'",
            (user.id,),
        ).fetchone()[0]
        if pending >= MAX_PENDING_PER_EMPLOYEE:
            raise HTTPException(status_code=429, detail=t("err.too_many_pending"))
        cur = conn.execute(
            "INSERT INTO leave_requests (employee_id, kind, date_from, date_to, note) VALUES (?, ?, ?, ?, ?)",
            (
                user.id,
                body.kind,
                body.date_from.isoformat(),
                body.date_to.isoformat(),
                body.note.strip(),
            ),
        )
    logger.warning(f"leave request {cur.lastrowid} by employee={user.id}")
    return {"id": int(cur.lastrowid or 0)}


@router.post("/leave/admin", status_code=201)
def create_leave_admin(
    body: AdminLeaveIn, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Admin records leave for an employee, already approved (no age limit: this is how a past day is corrected).

    Days that already have leave are overridden, not refused: `leave_days` lets the newest approved row win, and the
    older row stays as history (leave is append-only).

    Raises:
        HTTPException: 422 for an unknown employee, a bad range or one without a working day.
    """
    if body.date_to < body.date_from or (body.date_to - body.date_from).days > 60:
        raise HTTPException(status_code=422, detail=t("err.leave_range_max"))
    if not working_days(body.date_from, body.date_to):
        raise HTTPException(status_code=422, detail=t("err.leave_no_working_days"))
    with connect() as conn:
        if conn.execute("SELECT 1 FROM employees WHERE id = ?", (body.employee_id,)).fetchone() is None:
            raise HTTPException(status_code=422, detail=t("err.unknown_employee_or_site"))
        cur = conn.execute(
            "INSERT INTO leave_requests (employee_id, kind, date_from, date_to, note, status, decided_by, decided_at) "
            "VALUES (?, ?, ?, ?, ?, 'approved', ?, ?)",
            (
                body.employee_id,
                body.kind,
                body.date_from.isoformat(),
                body.date_to.isoformat(),
                body.note.strip(),
                admin.id,
                utc_str(datetime.now(UTC)),
            ),
        )
    logger.warning(f"leave {cur.lastrowid} for employee={body.employee_id} set by admin={admin.id}")
    return {"id": int(cur.lastrowid or 0)}


@router.get("/leave/mine", response_model=list[LeaveOut])
def my_leave(user: Annotated[Me, Depends(current_user)]) -> list[LeaveOut]:
    """The employee's own requests, newest first."""
    with connect() as conn:
        return _rows(conn, "l.employee_id = ?", (user.id,))[:20]


@router.get("/leave", response_model=list[LeaveOut])
def list_leave(
    _: Annotated[Me, Depends(require_admin)],
    status: Literal["pending", "approved", "rejected"] = "pending",
) -> list[LeaveOut]:
    """Requests by status."""
    with connect() as conn:
        return _rows(conn, "l.status = ?", (status,))


def _decide(request_id: int, status: str, admin: Me, reason: str) -> None:
    with connect() as conn:
        r = conn.execute(
            "SELECT status FROM leave_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail=t("err.request_not_found"))
        if r["status"] != "pending":
            raise HTTPException(status_code=409, detail=t("err.already_decided"))
        conn.execute(
            "UPDATE leave_requests SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? WHERE id = ?",
            (status, admin.id, utc_str(datetime.now(UTC)), reason or None, request_id),
        )
    logger.warning(f"leave request {request_id} {status} by admin={admin.id}")


@router.post("/leave/{request_id}/approve")
def approve(
    request_id: int, body: DecisionBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Approve: the covered working days are marked with the leave code in reports.

    Raises:
        HTTPException: 404 if missing, 409 if already decided.
    """
    _decide(request_id, "approved", admin, body.reason.strip())
    return {"id": request_id}


@router.post("/leave/{request_id}/reject")
def reject(
    request_id: int, body: DecisionBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Reject; a reason is required and shown to the employee.

    Raises:
        HTTPException: 404 if missing, 409 if already decided, 422 without a reason.
    """
    if len(body.reason.strip()) < 3:
        raise HTTPException(status_code=422, detail=t("err.reject_reason_required"))
    _decide(request_id, "rejected", admin, body.reason.strip())
    return {"id": request_id}
