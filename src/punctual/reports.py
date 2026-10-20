"""Hours reports: pair in/out punches into shifts, aggregate per day and employee, export CSV."""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel

from punctual.auth import Me, current_user, require_admin
from punctual.clock import (
    cutoff_after,
    day_bounds,
    local_date,
    local_hm,
    parse_utc,
    utc_str,
)
from punctual.db import connect
from punctual.holidays import holidays_between
from punctual.i18n import t
from punctual.leave import LeaveDay, leave_days
from punctual.settings import settings

logger: logging.Logger = logging.getLogger(__name__)


router: APIRouter = APIRouter(prefix="/api/reports", tags=["reports"])


@dataclass(frozen=True)
class Punch:
    """A stored punch (UTC)."""

    employee_id: int
    site_name: str
    kind: str
    ts: datetime
    source: str = "device"


@dataclass(frozen=True)
class Shift:
    """One paired shift. `end` is None when the shift is open or the out punch is missing."""

    employee_id: int
    site_name: str
    start: datetime
    end: datetime | None
    anomaly: str | None = None
    manual: bool = False

    @property
    def hours(self) -> float | None:
        """Shift length in hours, or None if not closed."""
        if self.end is None:
            return None
        return round((self.end - self.start).total_seconds() / 3600, 2)

    @property
    def local_day(self) -> date:
        """Local calendar day the shift started on."""
        return local_date(self.start)


def pair_shifts(punches: list[Punch], now: datetime | None = None) -> list[Shift]:
    """Pair chronological in/out punches per employee into shifts.

    An `in` while a shift is open marks the earlier shift as `missing out`, as does a still-open shift past its
    cutoff (see `cutoff_after`). An `out` without an open shift yields an `orphan out` anomaly row (zero-length,
    no hours). A closed shift longer than `settings.long_shift_h` is kept but marked `long shift`.

    Args:
        punches: Punches of any employees, any order.
        now: Current time (UTC), injectable for tests.

    Returns:
        Shifts sorted by employee and start.
    """
    now = now or datetime.now(UTC)
    long_s: float = settings.long_shift_h * 3600
    shifts: list[Shift] = []
    open_by_emp: dict[int, Punch] = {}
    for p in sorted(punches, key=lambda x: (x.employee_id, x.ts)):
        current: Punch | None = open_by_emp.get(p.employee_id)
        if p.kind == "in":
            if current is not None:
                shifts.append(
                    Shift(
                        p.employee_id,
                        current.site_name,
                        current.ts,
                        None,
                        "missing out",
                        current.source == "admin",
                    )
                )
            open_by_emp[p.employee_id] = p
        elif current is not None:
            shifts.append(
                Shift(
                    p.employee_id,
                    current.site_name,
                    current.ts,
                    p.ts,
                    "long shift"
                    if (p.ts - current.ts).total_seconds() > long_s
                    else None,
                    "admin" in (p.source, current.source),
                )
            )
            del open_by_emp[p.employee_id]
        else:
            shifts.append(
                Shift(
                    p.employee_id,
                    p.site_name,
                    p.ts,
                    None,
                    "orphan out",
                    p.source == "admin",
                )
            )
    for cur in open_by_emp.values():
        shifts.append(
            Shift(
                cur.employee_id,
                cur.site_name,
                cur.ts,
                None,
                "missing out" if now >= cutoff_after(cur.ts) else "open",
                cur.source == "admin",
            )
        )
    return sorted(shifts, key=lambda s: (s.employee_id, s.start))


class DayRow(BaseModel):
    """Hours worked by one employee on one local day."""

    employee_id: int
    employee: str
    day: str
    first_in: str
    last_out: str | None
    hours: float
    norma_h: int
    overtime_h: float
    over_norma: bool
    anomalies: list[str]
    sites: list[str]
    manual: bool = False
    location_flag: bool = False
    reviewed: bool = False


class Report(BaseModel):
    """Report for a date range.

    `leave` lists the working days covered by approved vacation / medical / free-day requests; `holidays` maps each
    public holiday in the range (ISO day) to its name. Both count as no expected hours.
    """

    date_from: str
    date_to: str
    days: list[DayRow]
    totals: dict[str, float]
    leave: list[LeaveDay] = []
    holidays: dict[str, str] = {}


def build_report(d_from: date, d_to: date, employee_id: int | None = None) -> Report:
    """Compute the report for local dates [d_from, d_to].

    Punches are fetched with one day of slack on each side so shifts crossing the range edge still pair; a shift
    counts on the local day it started.

    Args:
        d_from: First local day.
        d_to: Last local day (inclusive).
        employee_id: Restrict to one employee.

    Returns:
        Per employee/day rows and per employee totals.
    """
    start, end = day_bounds(d_from, d_to)
    lo: str = utc_str(start - timedelta(days=1))
    hi: str = utc_str(end + timedelta(days=1))
    sql: str = (
        "SELECT p.employee_id, e.name AS employee, e.norma_h, s.name AS site, p.kind, p.ts_utc, p.source, (p.source = 'device' AND (p.inside = 0 OR p.accuracy_m > 100) AND p.id NOT IN (SELECT punch_id FROM punch_approvals)) AS flag FROM live_punches p "
        "JOIN employees e ON e.id = p.employee_id JOIN sites s ON s.id = p.site_id "
        "WHERE p.ts_utc >= ? AND p.ts_utc < ?"
    )
    args: list[object] = [lo, hi]
    if employee_id is not None:
        sql += " AND p.employee_id = ?"
        args.append(employee_id)
    rsql: str = (
        "SELECT employee_id, day, notes FROM day_reviews WHERE day >= ? AND day <= ?"
    )
    rargs: list[object] = [d_from.isoformat(), d_to.isoformat()]
    if employee_id is not None:
        rsql += " AND employee_id = ?"
        rargs.append(employee_id)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        reviews = conn.execute(rsql, rargs).fetchall()
        leave: list[LeaveDay] = leave_days(conn, d_from, d_to, employee_id)
    reviewed_sets: dict[tuple[int, str], list[set[str]]] = {}
    for r in reviews:
        reviewed_sets.setdefault((r["employee_id"], r["day"]), []).append(
            set(r["notes"].split(","))
        )
    names: dict[int, str] = {r["employee_id"]: r["employee"] for r in rows}
    norma: dict[int, int] = {r["employee_id"]: r["norma_h"] for r in rows}
    punches: list[Punch] = [
        Punch(
            r["employee_id"],
            r["site"],
            r["kind"],
            parse_utc(r["ts_utc"]),
            r["source"],
        )
        for r in rows
    ]
    flag_days: set[tuple[int, date]] = {
        (r["employee_id"], local_date(r["ts_utc"])) for r in rows if r["flag"]
    }
    grouped: dict[tuple[int, date], list[Shift]] = {}
    for sh in pair_shifts(punches):
        if d_from <= sh.local_day <= d_to:
            grouped.setdefault((sh.employee_id, sh.local_day), []).append(sh)

    days: list[DayRow] = []
    totals: dict[str, float] = {}
    for (emp, day), shs in sorted(
        grouped.items(), key=lambda kv: (names[kv[0][0]], kv[0][1])
    ):
        hours: float = round(sum(s.hours or 0.0 for s in shs), 2)
        ends: list[datetime] = [s.end for s in shs if s.end is not None]
        notes: set[str] = {s.anomaly for s in shs if s.anomaly}
        if (emp, day) in flag_days:
            notes.add("location")
        days.append(
            DayRow(
                employee_id=emp,
                employee=names[emp],
                day=day.isoformat(),
                first_in=local_hm(min(s.start for s in shs)),
                last_out=local_hm(max(ends)) if ends else None,
                hours=hours,
                norma_h=norma[emp],
                overtime_h=round(max(hours - norma[emp], 0.0), 2),
                over_norma=hours > norma[emp],
                anomalies=sorted({s.anomaly for s in shs if s.anomaly}),
                sites=sorted({s.site_name for s in shs}),
                manual=any(s.manual for s in shs),
                location_flag=(emp, day) in flag_days,
                reviewed=bool(notes)
                and any(
                    notes <= done
                    for done in reviewed_sets.get((emp, day.isoformat()), [])
                ),
            )
        )
        totals[names[emp]] = round(totals.get(names[emp], 0.0) + hours, 2)
    logger.debug(f"report {d_from}..{d_to} employee={employee_id} rows={len(days)}")
    return Report(
        date_from=d_from.isoformat(),
        date_to=d_to.isoformat(),
        days=days,
        totals=totals,
        leave=leave,
        holidays={d.isoformat(): n for d, n in holidays_between(d_from, d_to).items()},
    )


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection in text cells."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(report: Report) -> str:
    """Render a report as `;`-separated CSV (Romanian Excel default).

    Args:
        report: Report to render.

    Returns:
        CSV text.
    """
    buf: io.StringIO = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            t(f"report.csv.{c}")
            for c in (
                "employee",
                "date",
                "first_in",
                "last_out",
                "hours",
                "norm",
                "overtime",
                "anomalies",
                "manual",
                "location_flag",
                "reviewed",
                "sites",
                "code",
            )
        ]
    )
    for d in report.days:
        w.writerow(
            [
                _csv_safe(d.employee),
                d.day,
                d.first_in,
                d.last_out or "",
                f"{d.hours:.2f}".replace(".", ","),
                d.norma_h,
                f"{d.overtime_h:.2f}".replace(".", ","),
                ", ".join(t(f"anomaly.{a.replace(' ', '_')}") for a in d.anomalies),
                t("yes") if d.manual else "",
                t("yes") if d.location_flag else "",
                t("yes") if d.reviewed else "",
                _csv_safe(", ".join(d.sites)),
                t("report.code.holiday") if d.day in report.holidays else "",
            ]
        )
    for x in report.leave:
        w.writerow(
            [
                _csv_safe(x.employee),
                x.day,
                "",
                "",
                "0,00",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                x.code,
            ]
        )
    w.writerow([])
    for name, total in sorted(report.totals.items()):
        w.writerow(
            [
                _csv_safe(name),
                t("report.csv.total"),
                "",
                "",
                f"{total:.2f}".replace(".", ","),
            ]
        )
    if report.holidays:
        w.writerow([])
        for day, name in sorted(report.holidays.items()):
            w.writerow([t("report.csv.holiday"), day, name])
    return buf.getvalue()


def _scope(user: Me, employee_id: int | None) -> int | None:
    """Employees may only see themselves; admins may pick anyone or all."""
    return employee_id if user.role == "admin" else user.id


@router.get("/hours", response_model=Report)
def hours(
    user: Annotated[Me, Depends(current_user)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    employee_id: int | None = None,
) -> Report:
    """Hours report (admins: all or one employee; employees: self only)."""
    return build_report(date_from, date_to, _scope(user, employee_id))


@router.get("/hours.csv")
def hours_csv(
    admin: Annotated[Me, Depends(require_admin)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    employee_id: int | None = None,
) -> Response:
    """CSV export (admin only)."""
    body: str = "﻿" + to_csv(build_report(date_from, date_to, employee_id))
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="pontaj_{date_from}_{date_to}.csv"'
        },
    )
