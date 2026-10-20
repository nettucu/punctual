"""Effective clock state of an employee: punches plus pending correction requests, with forgotten-shift detection.

The single place that decides whether someone is at work, waiting on a declared arrival, or blocked on a forgotten
shift. The clock page (`effective_state`) and the admin dashboard (`all_states`) read the same implementation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from punctual.clock import cutoff_after, parse_utc

StateName = Literal["closed", "open", "pending_in", "forgotten"]


@dataclass(frozen=True)
class ShiftState:
    """Where an employee stands now.

    Attributes:
        state: `closed` (not at work), `open` (clocked in), `pending_in` (at work on a pending arrival request),
            `forgotten` (an open shift passed the cutoff hour; must declare the departure before anything else).
        open_since: Start of the open shift (UTC), None when closed.
        open_site_id: Site of the open shift, None when closed.
        open_punch_id: Arrival punch of the open shift; None when closed or when the shift rests on a pending
            arrival request instead of a punch.
        rejected_note: For `forgotten`, the admin's reason on the latest rejected request about this shift.
    """

    state: StateName
    open_since: datetime | None = None
    open_site_id: int | None = None
    open_punch_id: int | None = None
    rejected_note: str | None = None


CLOSED: ShiftState = ShiftState("closed")

# Latest event per employee: non-voided punches and pending requests (provisional events). On equal times a punch
# ranks after a request (`src DESC`), so a real punch wins over a request for the same minute.
_LATEST: str = (
    "SELECT employee_id, kind, ts_utc, site_id, src, id FROM ("
    "SELECT *, ROW_NUMBER() OVER (PARTITION BY employee_id ORDER BY ts_utc DESC, src DESC, id DESC) AS rn FROM ("
    "SELECT employee_id, kind, ts_utc, site_id, 'punch' AS src, id FROM live_punches {where} "
    "UNION ALL SELECT employee_id, kind, ts_utc, site_id, 'request' AS src, id FROM correction_requests "
    "WHERE status = 'pending' {and_where})) WHERE rn = 1"
)


def _states(
    conn: sqlite3.Connection, now: datetime, employee_id: int | None
) -> dict[int, ShiftState]:
    one: bool = employee_id is not None
    sql: str = _LATEST.format(
        where="WHERE employee_id = ?" if one else "",
        and_where="AND employee_id = ?" if one else "",
    )
    rows = conn.execute(sql, (employee_id, employee_id) if one else ()).fetchall()
    out: dict[int, ShiftState] = {}
    for last in rows:
        if last["kind"] == "out":
            continue
        since: datetime = parse_utc(last["ts_utc"])
        punch_id: int | None = last["id"] if last["src"] == "punch" else None
        if now >= cutoff_after(since):
            rejected = conn.execute(
                "SELECT decision_note FROM correction_requests WHERE employee_id = ? AND status = 'rejected' "
                "AND kind = 'out' AND ts_utc > ? ORDER BY decided_at DESC, id DESC LIMIT 1",
                (last["employee_id"], last["ts_utc"]),
            ).fetchone()
            out[last["employee_id"]] = ShiftState(
                "forgotten",
                since,
                last["site_id"],
                punch_id,
                rejected["decision_note"] if rejected else None,
            )
        else:
            out[last["employee_id"]] = ShiftState(
                "open" if punch_id else "pending_in", since, last["site_id"], punch_id
            )
    return out


def effective_state(
    conn: sqlite3.Connection, employee_id: int, now: datetime | None = None
) -> ShiftState:
    """Compute one employee's state from non-voided punches and pending requests.

    Rejected requests and voided punches drop out, so the state recomputes itself after admin decisions.

    Args:
        conn: Open connection.
        employee_id: Employee.
        now: Current time (UTC), injectable for tests.

    Returns:
        The current state.
    """
    return _states(conn, now or datetime.now(UTC), employee_id).get(employee_id, CLOSED)


def all_states(
    conn: sqlite3.Connection, now: datetime | None = None
) -> dict[int, ShiftState]:
    """States of every employee who is not closed (open, pending_in or forgotten), in one query.

    Args:
        conn: Open connection.
        now: Current time (UTC), injectable for tests.

    Returns:
        employee_id to state; an employee missing from the dict is `closed`.
    """
    return _states(conn, now or datetime.now(UTC), None)
