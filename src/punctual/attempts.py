"""Rejected punch attempts: an append-only log for admins, purged automatically after the retention period."""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from punctual.auth import Me, require_admin
from punctual.clock import ISO, day_bounds, local_str, utc_str
from punctual.db import connect
from punctual.i18n import has, t
from punctual.settings import settings

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/attempts", tags=["attempts"])


def reason_label(reason: str) -> str:
    """Admin-facing label of a rejection reason code (the raw code when it has none)."""
    key: str = f"attempt.{reason}"
    return t(key) if has(key) else reason


def record_attempt(
    employee_id: int,
    site_id: int,
    kind: str,
    lat: float,
    lon: float,
    accuracy_m: float,
    reason: str,
    distance_m: float | None = None,
) -> None:
    """Store a rejected attempt (own connection, so it survives the caller's rollback) and purge old rows.

    Args:
        employee_id: Who tried.
        site_id: Site they targeted (stored as NULL if it does not exist).
        kind: `in` or `out`.
        lat: Reported latitude.
        lon: Reported longitude.
        accuracy_m: Reported GPS accuracy.
        reason: Rejection reason code (see the `attempt.*` messages).
        distance_m: Distance from the site, when known.
    """
    try:
        with connect() as conn:
            known = conn.execute(
                "SELECT 1 FROM sites WHERE id = ?", (site_id,)
            ).fetchone()
            conn.execute(
                "INSERT INTO punch_attempts (employee_id, site_id, kind, lat, lon, accuracy_m, distance_m, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    employee_id,
                    site_id if known else None,
                    kind,
                    lat,
                    lon,
                    accuracy_m,
                    None if distance_m is None else round(distance_m, 1),
                    reason,
                ),
            )
        purge_old()
    except Exception:  # never let logging break the (already failing) punch response
        logger.exception("could not record punch attempt")


def purge_old() -> int:
    """Delete attempts older than the retention period (the DB refuses deleting anything younger than 30 days).

    Returns:
        Number of rows deleted.
    """
    days: int = max(settings.attempts_retention_days, 30)
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM punch_attempts WHERE ts_utc < strftime(?, 'now', ?)",
            (ISO, f"-{days} days"),
        )
    if cur.rowcount:
        logger.info(f"purged {cur.rowcount} punch attempts older than {days} days")
    return cur.rowcount


class AttemptRow(BaseModel):
    """A rejected attempt as shown to admins."""

    id: int
    local_time: str
    employee_id: int
    employee: str
    site: str | None
    kind: str
    reason: str
    reason_label: str
    distance_m: float | None
    accuracy_m: float
    lat: float
    lon: float
    site_lat: float | None
    site_lon: float | None


@router.get("", response_model=list[AttemptRow])
def list_attempts(
    _: Annotated[Me, Depends(require_admin)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    employee_id: int | None = None,
) -> list[AttemptRow]:
    """Rejected attempts in a local date range, newest first."""
    start, end = day_bounds(date_from, date_to)
    sql: str = (
        "SELECT a.*, e.name AS employee, s.name AS site, s.lat AS site_lat, s.lon AS site_lon "
        "FROM punch_attempts a JOIN employees e ON e.id = a.employee_id LEFT JOIN sites s ON s.id = a.site_id "
        "WHERE a.ts_utc >= ? AND a.ts_utc < ?"
    )
    args: list[object] = [utc_str(start), utc_str(end)]
    if employee_id is not None:
        sql += " AND a.employee_id = ?"
        args.append(employee_id)
    sql += " ORDER BY a.ts_utc DESC, a.id DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        AttemptRow(
            id=r["id"],
            local_time=local_str(r["ts_utc"]),
            employee_id=r["employee_id"],
            employee=r["employee"],
            site=r["site"],
            kind=r["kind"],
            reason=r["reason"],
            reason_label=reason_label(r["reason"]),
            distance_m=r["distance_m"],
            accuracy_m=r["accuracy_m"],
            lat=r["lat"],
            lon=r["lon"],
            site_lat=r["site_lat"],
            site_lon=r["site_lon"],
        )
        for r in rows
    ]
