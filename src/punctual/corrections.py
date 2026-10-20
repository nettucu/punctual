"""Admin corrections: add a missing punch, void a wrong one, or edit one (void + linked replacement).

Nothing is ever updated or deleted in place.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from punctual.auth import Me, require_admin
from punctual.clock import day_bounds, local_str, parse_local, utc_str
from punctual.db import connect
from punctual.i18n import t

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/corrections", tags=["corrections"])


class ManualPunch(BaseModel):
    """Admin-entered punch. `local_time` is local wall time, e.g. 2026-03-10T08:00."""

    employee_id: int
    site_id: int
    kind: Literal["in", "out"]
    local_time: str
    note: str = Field(min_length=3, max_length=500)


class EditBody(BaseModel):
    """Replacement values for an edited punch. `local_time` is local wall time."""

    site_id: int
    kind: Literal["in", "out"]
    local_time: str
    reason: str = Field(min_length=3, max_length=500)


class VoidBody(BaseModel):
    """Reason for voiding a punch."""

    reason: str = Field(min_length=3, max_length=500)


class PunchRow(BaseModel):
    """A punch as shown to admins."""

    id: int
    employee_id: int
    employee: str
    site_id: int = 0
    site: str
    kind: str
    local_time: str
    source: str
    corrects_id: int | None = None
    was: str | None = None
    note: str | None
    created_by: str | None
    voided: bool
    void_reason: str | None
    distance_m: float | None = None
    accuracy_m: float | None = None
    flagged: bool = False
    approved: bool = False
    approve_reason: str | None = None
    lat: float | None = None
    lon: float | None = None
    site_lat: float | None = None
    site_lon: float | None = None


@router.get("/punches", response_model=list[PunchRow])
def list_punches(
    _: Annotated[Me, Depends(require_admin)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    employee_id: int | None = None,
) -> list[PunchRow]:
    """List punches (including voided ones) in a local date range."""
    start, end = day_bounds(date_from, date_to)
    sql: str = (
        "SELECT p.id, p.employee_id, e.name AS employee, p.site_id, s.name AS site, p.kind, p.ts_utc, p.source, p.note, "
        "p.corrects_id, o.kind AS was_kind, o.ts_utc AS was_ts, so.name AS was_site, "
        "c.name AS created_by, v.reason AS void_reason, p.distance_m, p.accuracy_m, p.inside, p.lat, p.lon, s.lat AS site_lat, s.lon AS site_lon, "
        "a.reason AS approve_reason FROM punches p "
        "JOIN employees e ON e.id = p.employee_id JOIN sites s ON s.id = p.site_id "
        "LEFT JOIN employees c ON c.id = p.created_by LEFT JOIN punch_voids v ON v.punch_id = p.id "
        "LEFT JOIN punch_approvals a ON a.punch_id = p.id "
        "LEFT JOIN punches o ON o.id = p.corrects_id LEFT JOIN sites so ON so.id = o.site_id "
        "WHERE p.ts_utc >= ? AND p.ts_utc < ?"
    )
    args: list[object] = [utc_str(start), utc_str(end)]
    if employee_id is not None:
        sql += " AND p.employee_id = ?"
        args.append(employee_id)
    sql += " ORDER BY e.name, p.ts_utc, p.id"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        PunchRow(
            id=r["id"],
            employee_id=r["employee_id"],
            employee=r["employee"],
            site_id=r["site_id"],
            site=r["site"],
            kind=r["kind"],
            local_time=local_str(r["ts_utc"]),
            source=r["source"],
            corrects_id=r["corrects_id"],
            was=describe(r["was_kind"], r["was_ts"], r["was_site"])
            if r["corrects_id"]
            else None,
            note=r["note"],
            created_by=r["created_by"],
            voided=r["void_reason"] is not None,
            void_reason=r["void_reason"],
            distance_m=r["distance_m"],
            accuracy_m=r["accuracy_m"],
            flagged=r["source"] == "device"
            and (not r["inside"] or r["accuracy_m"] > 100)
            and r["approve_reason"] is None,
            approved=r["approve_reason"] is not None,
            approve_reason=r["approve_reason"],
            lat=r["lat"] if r["source"] == "device" else None,
            lon=r["lon"] if r["source"] == "device" else None,
            site_lat=r["site_lat"],
            site_lon=r["site_lon"],
        )
        for r in rows
    ]


def describe(kind: str, ts_utc: str, site: str) -> str:
    """Human summary of a punch, e.g. `2026-09-21 15:44 in · Main clinic`."""
    return f"{local_str(ts_utc)} {t('kind.' + kind)} · {site}"


def insert_manual_punch(
    conn: sqlite3.Connection,
    employee_id: int,
    site_id: int,
    kind: str,
    ts: datetime,
    note: str,
    admin_id: int,
    corrects_id: int | None = None,
) -> int:
    """Insert an admin-sourced punch (append-only) and return its id.

    Args:
        conn: Open connection (the caller commits).
        employee_id: Employee the punch belongs to.
        site_id: Site of the punch.
        kind: `in` or `out`.
        ts: Punch time (UTC).
        note: Reason, shown in the audit trail.
        admin_id: Admin who entered it.
        corrects_id: The voided punch this one replaces (admin edit), if any.

    Returns:
        The new punch id.

    Raises:
        HTTPException: 422 for an unknown employee or site.
    """
    site = conn.execute(
        "SELECT lat, lon FROM sites WHERE id = ?", (site_id,)
    ).fetchone()
    emp = conn.execute(
        "SELECT 1 FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()
    if site is None or emp is None:
        raise HTTPException(status_code=422, detail=t("err.unknown_employee_or_site"))
    cur = conn.execute(
        "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside, note, source, "
        "created_by, corrects_id, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 'admin', ?, ?, ?)",
        (
            employee_id,
            site_id,
            kind,
            utc_str(ts),
            site["lat"],
            site["lon"],
            note,
            admin_id,
            corrects_id,
            utc_str(datetime.now(UTC)),
        ),
    )
    logger.warning(
        f"manual punch id={cur.lastrowid} employee={employee_id} by admin={admin_id}"
    )
    return int(cur.lastrowid or 0)


@router.post("/punches", status_code=201)
def add_punch(
    body: ManualPunch, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Insert a punch on behalf of an employee (marked source=admin, with the admin and note recorded).

    Raises:
        HTTPException: 422 for a bad time, a future time or unknown employee/site.
    """
    ts: datetime = parse_local(body.local_time)
    with connect() as conn:
        pid: int = insert_manual_punch(
            conn, body.employee_id, body.site_id, body.kind, ts, body.note, admin.id
        )
    return {"id": pid}


@router.post("/punches/{punch_id}/void", status_code=201)
def void_punch(
    punch_id: int, body: VoidBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Void a punch (kept in the DB, excluded from reports).

    Raises:
        HTTPException: 404 if missing, 409 if already voided.
    """
    with connect() as conn:
        if (
            conn.execute("SELECT 1 FROM punches WHERE id = ?", (punch_id,)).fetchone()
            is None
        ):
            raise HTTPException(status_code=404, detail=t("err.punch_not_found"))
        if conn.execute(
            "SELECT 1 FROM punch_voids WHERE punch_id = ?", (punch_id,)
        ).fetchone():
            raise HTTPException(status_code=409, detail=t("err.already_voided"))
        conn.execute(
            "INSERT INTO punch_voids (punch_id, voided_by, reason) VALUES (?, ?, ?)",
            (punch_id, admin.id, body.reason),
        )
    logger.warning(f"punch {punch_id} voided by admin={admin.id}")
    return {"id": punch_id}


@router.post("/punches/{punch_id}/edit", status_code=201)
def edit_punch(
    punch_id: int, body: EditBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Edit a punch: void the original and insert the replacement linked by `corrects_id`, atomically.

    Raises:
        HTTPException: 404 if missing, 409 if already voided (edit the current version instead), 422 for a bad
            time or unknown site, or when nothing changes.
    """
    ts: datetime = parse_local(body.local_time)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute(
            "SELECT employee_id, site_id, kind, ts_utc FROM punches WHERE id = ?",
            (punch_id,),
        ).fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail=t("err.punch_not_found"))
        if conn.execute(
            "SELECT 1 FROM punch_voids WHERE punch_id = ?", (punch_id,)
        ).fetchone():
            raise HTTPException(status_code=409, detail=t("err.punch_already_changed"))
        if (old["site_id"], old["kind"], old["ts_utc"]) == (
            body.site_id,
            body.kind,
            utc_str(ts),
        ):
            raise HTTPException(status_code=422, detail=t("err.nothing_changed"))
        reason: str = body.reason.strip()
        conn.execute(
            "INSERT INTO punch_voids (punch_id, voided_by, reason) VALUES (?, ?, ?)",
            (punch_id, admin.id, t("note.edited", reason=reason)),
        )
        pid: int = insert_manual_punch(
            conn,
            old["employee_id"],
            body.site_id,
            body.kind,
            ts,
            reason,
            admin.id,
            punch_id,
        )
    logger.warning(f"punch {punch_id} edited into {pid} by admin={admin.id}")
    return {"id": pid}


class ReviewBody(BaseModel):
    """Mark an employee's day as checked."""

    employee_id: int
    day: date
    reason: str = Field(min_length=3, max_length=500)


@router.post("/reviews", status_code=201)
def review_day(
    body: ReviewBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Record that an admin checked a flagged day and found it acceptable (append-only).

    The flags present right now are stored with the review; if a different one appears later the day is flagged again.

    Raises:
        HTTPException: 404 for an unknown employee, 409 if the day has nothing flagged or is already reviewed.
    """
    from punctual.reports import build_report

    day_rows = [
        d
        for d in build_report(body.day, body.day, body.employee_id).days
        if d.day == body.day.isoformat()
    ]
    if not day_rows:
        raise HTTPException(status_code=404, detail=t("err.no_punches_that_day"))
    d = day_rows[0]
    notes: list[str] = sorted(
        set(d.anomalies) | ({"location"} if d.location_flag else set())
    )
    if not notes:
        raise HTTPException(status_code=409, detail=t("err.nothing_to_review"))
    if d.reviewed:
        raise HTTPException(status_code=409, detail=t("err.day_already_reviewed"))
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO day_reviews (employee_id, day, notes, reason, reviewed_by) VALUES (?, ?, ?, ?, ?)",
            (
                body.employee_id,
                body.day.isoformat(),
                ",".join(notes),
                body.reason.strip(),
                admin.id,
            ),
        )
    logger.warning(
        f"day reviewed employee={body.employee_id} day={body.day} notes={notes} by admin={admin.id}"
    )
    return {"id": int(cur.lastrowid or 0)}


class ApproveBody(BaseModel):
    """Reason for approving a flagged location."""

    reason: str = Field(min_length=3, max_length=500)


@router.post("/punches/{punch_id}/approve-location", status_code=201)
def approve_location(
    punch_id: int, body: ApproveBody, admin: Annotated[Me, Depends(require_admin)]
) -> dict[str, int]:
    """Mark a flagged punch location as acceptable (kept, append-only).

    Raises:
        HTTPException: 404 if missing, 409 if already approved.
    """
    with connect() as conn:
        if (
            conn.execute("SELECT 1 FROM punches WHERE id = ?", (punch_id,)).fetchone()
            is None
        ):
            raise HTTPException(status_code=404, detail=t("err.punch_not_found"))
        if conn.execute(
            "SELECT 1 FROM punch_approvals WHERE punch_id = ?", (punch_id,)
        ).fetchone():
            raise HTTPException(status_code=409, detail=t("err.already_approved"))
        conn.execute(
            "INSERT INTO punch_approvals (punch_id, approved_by, reason) VALUES (?, ?, ?)",
            (punch_id, admin.id, body.reason),
        )
    logger.warning(f"punch {punch_id} location approved by admin={admin.id}")
    return {"id": punch_id}
