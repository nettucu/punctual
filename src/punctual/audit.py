"""Change history for admins and labour inspections: every manual punch, edit, void, location approval and decided
correction request, derived from the append-only tables (nothing here is stored separately)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel

from punctual.auth import Me, require_admin
from punctual.clock import day_bounds, local_str, utc_str
from punctual.corrections import describe
from punctual.db import connect
from punctual.i18n import t
from punctual.reports import _csv_safe

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/audit", tags=["audit"])

Action = Literal[
    "added",
    "edited",
    "voided",
    "location_approved",
    "request_approved",
    "request_rejected",
    "reviewed",
    "leave_approved",
    "leave_rejected",
]


class AuditEvent(BaseModel):
    """One change. `at` is when it was made (local), None for rows entered before this was recorded."""

    at: str | None
    admin: str
    employee: str
    day: str
    action: Action
    before: str | None
    after: str | None
    reason: str | None


# Each query yields: at_utc, admin, employee, affected ts_utc (for the range filter), before, after, reason.
# `before`/`after` are (kind, ts_utc, site) triples flattened with a prefix.
_QUERIES: dict[str, str] = {
    "edited": (
        "SELECT v.voided_at AS at, a.name AS admin, e.name AS employee, o.employee_id, o.ts_utc AS ts, "
        "o.kind AS b_kind, o.ts_utc AS b_ts, so.name AS b_site, n.kind AS a_kind, n.ts_utc AS a_ts, "
        "sn.name AS a_site, n.note AS reason FROM punches n JOIN punches o ON o.id = n.corrects_id "
        "JOIN punch_voids v ON v.punch_id = o.id JOIN employees a ON a.id = v.voided_by "
        "JOIN employees e ON e.id = o.employee_id JOIN sites so ON so.id = o.site_id JOIN sites sn ON sn.id = n.site_id"
    ),
    "added": (
        "SELECT p.created_at AS at, a.name AS admin, e.name AS employee, p.employee_id, p.ts_utc AS ts, "
        "NULL AS b_kind, NULL AS b_ts, NULL AS b_site, p.kind AS a_kind, p.ts_utc AS a_ts, s.name AS a_site, "
        "p.note AS reason FROM punches p JOIN employees a ON a.id = p.created_by "
        "JOIN employees e ON e.id = p.employee_id JOIN sites s ON s.id = p.site_id "
        "WHERE p.source = 'admin' AND p.corrects_id IS NULL "
        "AND p.id NOT IN (SELECT punch_id FROM correction_requests WHERE punch_id IS NOT NULL)"
    ),
    "voided": (
        "SELECT v.voided_at AS at, a.name AS admin, e.name AS employee, p.employee_id, p.ts_utc AS ts, "
        "p.kind AS b_kind, p.ts_utc AS b_ts, s.name AS b_site, NULL AS a_kind, NULL AS a_ts, NULL AS a_site, "
        "v.reason FROM punch_voids v JOIN punches p ON p.id = v.punch_id JOIN employees a ON a.id = v.voided_by "
        "JOIN employees e ON e.id = p.employee_id JOIN sites s ON s.id = p.site_id "
        "WHERE v.punch_id NOT IN (SELECT corrects_id FROM punches WHERE corrects_id IS NOT NULL)"
    ),
    "location_approved": (
        "SELECT ap.approved_at AS at, a.name AS admin, e.name AS employee, p.employee_id, p.ts_utc AS ts, "
        "p.kind AS b_kind, p.ts_utc AS b_ts, s.name AS b_site, NULL AS a_kind, NULL AS a_ts, NULL AS a_site, "
        "ap.reason FROM punch_approvals ap JOIN punches p ON p.id = ap.punch_id "
        "JOIN employees a ON a.id = ap.approved_by JOIN employees e ON e.id = p.employee_id "
        "JOIN sites s ON s.id = p.site_id"
    ),
    "request_approved": (
        "SELECT r.decided_at AS at, a.name AS admin, e.name AS employee, r.employee_id, r.ts_utc AS ts, "
        "r.kind AS b_kind, r.ts_utc AS b_ts, s.name AS b_site, p.kind AS a_kind, p.ts_utc AS a_ts, "
        "sp.name AS a_site, r.note || COALESCE(' / ' || r.decision_note, '') AS reason "
        "FROM correction_requests r JOIN employees a ON a.id = r.decided_by "
        "JOIN employees e ON e.id = r.employee_id JOIN sites s ON s.id = r.site_id "
        "JOIN punches p ON p.id = r.punch_id JOIN sites sp ON sp.id = p.site_id WHERE r.status = 'approved'"
    ),
    "reviewed": (
        "SELECT r.reviewed_at AS at, a.name AS admin, e.name AS employee, r.employee_id, "
        "strftime('%Y-%m-%dT%H:%M:%SZ', r.day || ' 12:00:00') AS ts, "
        "NULL AS b_kind, NULL AS b_ts, NULL AS b_site, NULL AS a_kind, NULL AS a_ts, NULL AS a_site, "
        "r.reason || ' [' || r.notes || ']' AS reason FROM day_reviews r "
        "JOIN employees a ON a.id = r.reviewed_by JOIN employees e ON e.id = r.employee_id"
    ),
    "leave_approved": (
        "SELECT l.decided_at AS at, a.name AS admin, e.name AS employee, l.employee_id, "
        "l.date_from || 'T12:00:00Z' AS ts, NULL AS b_kind, NULL AS b_ts, NULL AS b_site, NULL AS a_kind, NULL AS a_ts, "
        "NULL AS a_site, l.note || COALESCE(' / ' || l.decision_note, '') AS reason, "
        "l.kind AS lv_kind, l.date_from AS lv_from, l.date_to AS lv_to FROM leave_requests l "
        "JOIN employees a ON a.id = l.decided_by JOIN employees e ON e.id = l.employee_id WHERE l.status = 'approved'"
    ),
    "leave_rejected": (
        "SELECT l.decided_at AS at, a.name AS admin, e.name AS employee, l.employee_id, "
        "l.date_from || 'T12:00:00Z' AS ts, NULL AS b_kind, NULL AS b_ts, NULL AS b_site, NULL AS a_kind, NULL AS a_ts, "
        "NULL AS a_site, l.note || COALESCE(' / ' || l.decision_note, '') AS reason, "
        "l.kind AS lv_kind, l.date_from AS lv_from, l.date_to AS lv_to FROM leave_requests l "
        "JOIN employees a ON a.id = l.decided_by JOIN employees e ON e.id = l.employee_id WHERE l.status = 'rejected'"
    ),
    "request_rejected": (
        "SELECT r.decided_at AS at, a.name AS admin, e.name AS employee, r.employee_id, r.ts_utc AS ts, "
        "r.kind AS b_kind, r.ts_utc AS b_ts, s.name AS b_site, NULL AS a_kind, NULL AS a_ts, NULL AS a_site, "
        "r.note || COALESCE(' / ' || r.decision_note, '') AS reason "
        "FROM correction_requests r JOIN employees a ON a.id = r.decided_by "
        "JOIN employees e ON e.id = r.employee_id JOIN sites s ON s.id = r.site_id WHERE r.status = 'rejected'"
    ),
}


def _leave_text(r) -> str:
    """`Unpaid leave 2026-07-15 – 2026-07-16` for a leave decision row."""
    period: str = r["lv_from"] if r["lv_from"] == r["lv_to"] else f"{r['lv_from']} – {r['lv_to']}"
    return f"{t('leave.kind.' + r['lv_kind'])} {period}"


def history(
    d_from: date, d_to: date, employee_id: int | None = None
) -> list[AuditEvent]:
    """All changes affecting punches on local days [d_from, d_to], newest change first.

    Args:
        d_from: First local day of the affected punches.
        d_to: Last local day (inclusive).
        employee_id: Restrict to one employee.

    Returns:
        Events sorted by change time, newest first (undated legacy rows last).
    """
    start, end = day_bounds(d_from, d_to)
    events: list[AuditEvent] = []
    with connect() as conn:
        for action, sql in _QUERIES.items():
            q: str = f"SELECT * FROM ({sql}) WHERE ts >= ? AND ts < ?"
            args: list[object] = [utc_str(start), utc_str(end)]
            if employee_id is not None:
                q += " AND employee_id = ?"
                args.append(employee_id)
            for r in conn.execute(q, args).fetchall():
                events.append(
                    AuditEvent(
                        at=local_str(r["at"]) if r["at"] else None,
                        admin=r["admin"],
                        employee=r["employee"],
                        day=local_str(r["ts"])[:10],
                        action=action,  # type: ignore[arg-type]
                        before=describe(r["b_kind"], r["b_ts"], r["b_site"])
                        if r["b_ts"]
                        else None,
                        after=describe(r["a_kind"], r["a_ts"], r["a_site"])
                        if r["a_ts"]
                        else _leave_text(r)
                        if "lv_kind" in r.keys()
                        else None,
                        reason=r["reason"],
                    )
                )
    events.sort(key=lambda e: (e.at is not None, e.at or ""), reverse=True)
    logger.debug(f"audit {d_from}..{d_to} employee={employee_id} events={len(events)}")
    return events


@router.get("", response_model=list[AuditEvent])
def audit(
    _: Annotated[Me, Depends(require_admin)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    employee_id: int | None = None,
) -> list[AuditEvent]:
    """Change history for punches in a local date range (admin only)."""
    return history(date_from, date_to, employee_id)


@router.get("/export.csv")
def audit_csv(
    _: Annotated[Me, Depends(require_admin)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    employee_id: int | None = None,
) -> Response:
    """CSV export of the change history (`;`-separated, Romanian Excel default)."""
    buf: io.StringIO = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            t(f"audit.csv.{c}")
            for c in (
                "at",
                "admin",
                "employee",
                "day",
                "action",
                "before",
                "after",
                "reason",
            )
        ]
    )
    for e in history(date_from, date_to, employee_id):
        w.writerow(
            [
                e.at or "",
                _csv_safe(e.admin),
                _csv_safe(e.employee),
                e.day,
                t(f"audit.action.{e.action}"),
                _csv_safe(e.before or ""),
                _csv_safe(e.after or ""),
                _csv_safe(e.reason or ""),
            ]
        )
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="history_{date_from}_{date_to}.csv"'
        },
    )
