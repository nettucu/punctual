"""Admin dashboard data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from punctual.auth import Me, require_admin
from punctual.clock import local_str, today
from punctual.db import connect
from punctual.employees import PENDING_SQL
from punctual.holidays import holiday_name
from punctual.leave import leave_days
from punctual.reports import build_report
from punctual.state import ShiftState, all_states

router: APIRouter = APIRouter(prefix="/api/admin", tags=["admin"])


class Overview(BaseModel):
    """Dashboard numbers.

    `clocked_in` rows carry `pending` (at work on an arrival request not yet approved). `forgotten_open` rows carry
    `punch_id`, None when the forgotten shift rests on a pending arrival request rather than a punch.
    """

    clocked_in: list[dict[str, object]]
    forgotten_open: list[dict[str, object]] = []
    employees: int
    sites: int
    flagged_days: list[dict[str, object]]
    pending_requests: int = 0
    rejected_7d: int = 0
    pending_leave: int = 0
    today_holiday: str | None = None
    on_leave_today: list[dict[str, str]] = []


@router.get("/pending-counts")
def pending_counts(_: Annotated[Me, Depends(require_admin)]) -> dict[str, int]:
    """Requests and self-registered accounts waiting for an admin, for the nav badges."""
    with connect() as conn:
        counts: dict[str, int] = {
            t: conn.execute(
                f"SELECT COUNT(*) FROM {t}_requests WHERE status = 'pending'"
            ).fetchone()[0]
            for t in ("correction", "leave")
        }
        counts["accounts"] = conn.execute(
            f"SELECT COUNT(*) FROM employees WHERE {PENDING_SQL}"
        ).fetchone()[0]
        return counts


@router.get("/overview", response_model=Overview)
def overview(_: Annotated[Me, Depends(require_admin)]) -> Overview:
    """Who is at work now, who is blocked on a forgotten shift (any age), entity counts, flagged days (14 days).

    Both lists come from `all_states`, the same rules the clock page applies: a forgotten shift whose departure the
    employee already declared is not listed (it waits among the pending requests instead).
    """
    now: datetime = datetime.now(UTC)
    with connect() as conn:
        states: dict[int, ShiftState] = all_states(conn, now)
        names: dict[int, str] = {
            r["id"]: r["name"]
            for r in conn.execute("SELECT id, name FROM employees WHERE active = 1")
        }
        site_names: dict[int, str] = {
            r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM sites")
        }
        rejected: int = conn.execute(
            "SELECT COUNT(*) FROM punch_attempts WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')"
        ).fetchone()[0]
        pending: int = conn.execute(
            "SELECT COUNT(*) FROM correction_requests WHERE status = 'pending'"
        ).fetchone()[0]
        pending_leave: int = conn.execute(
            "SELECT COUNT(*) FROM leave_requests WHERE status = 'pending'"
        ).fetchone()[0]
        day = today()
        on_leave = leave_days(conn, day, day)
    clocked: list[dict[str, object]] = []
    forgotten: list[dict[str, object]] = []
    for emp, st in sorted(
        ((e, s) for e, s in states.items() if e in names), key=lambda es: names[es[0]]
    ):
        row: dict[str, object] = {
            "name": names[emp],
            "site": site_names.get(st.open_site_id or 0, ""),
            "since": local_str(st.open_since) if st.open_since else "",
        }
        if st.state == "forgotten":
            forgotten.append(
                {
                    **row,
                    "employee_id": emp,
                    "site_id": st.open_site_id,
                    "punch_id": st.open_punch_id,
                }
            )
        else:
            clocked.append({**row, "pending": st.state == "pending_in"})
    rep = build_report(day - timedelta(days=14), day)
    flagged: list[dict[str, object]] = [
        {
            "employee": d.employee,
            "employee_id": d.employee_id,
            "day": d.day,
            "notes": notes,
        }
        for d in rep.days
        # A shift still running is not a problem yet: it turns into `missing out` after the cutoff.
        if (notes := [a for a in d.anomalies if a != "open"] + (["location"] if d.location_flag else []))
        and not d.reviewed
    ]
    return Overview(
        clocked_in=clocked,
        forgotten_open=forgotten,
        employees=len(names),
        sites=len(site_names),
        flagged_days=flagged,
        pending_requests=pending,
        rejected_7d=rejected,
        pending_leave=pending_leave,
        today_holiday=holiday_name(day),
        on_leave_today=[{"name": x.employee, "code": x.code} for x in on_leave],
    )
