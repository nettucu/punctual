"""Clock in/out with server-side geofence enforcement."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from punctual.attempts import record_attempt
from punctual.auth import Me, current_user
from punctual.clock import day_bounds, local_date, local_hm, local_str, parse_utc, utc_str
from punctual.db import connect
from punctual.geofence import Site, distance_m, is_inside
from punctual.i18n import t
from punctual.settings import settings
from punctual.state import ShiftState, effective_state

logger: logging.Logger = logging.getLogger(__name__)

MAX_ACCURACY_M: float = 100.0

router: APIRouter = APIRouter(prefix="/api/punch", tags=["punch"])


class PunchIn(BaseModel):
    """Client-reported intent and position. The server decides the time."""

    site_id: int
    kind: Literal["in", "out"]
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(ge=0)


class GeoFailure(BaseModel):
    """A punch the browser could not even start: the phone gave no position."""

    site_id: int
    kind: Literal["in", "out"]
    reason: Literal["geo_denied", "geo_unavailable", "geo_timeout", "geo_unsupported"]
    detail: str = Field(default="", max_length=400)


class PunchOut(BaseModel):
    """A recorded punch."""

    id: int
    site_id: int
    kind: Literal["in", "out"]
    ts_utc: str
    flagged: bool = False
    warning: str | None = None


class Status(BaseModel):
    """Current state for the employee UI."""

    next_kind: Literal["in", "out"]
    last: PunchOut | None
    sites: list[dict[str, object]]
    state: Literal["closed", "open", "pending_in", "forgotten"] = "closed"
    open_since: str | None = None
    open_site_id: int | None = None
    rejected_note: str | None = None
    late_in_hour: int = 14
    open_minutes: int | None = None
    early_out_minutes: int = 5


def forgotten_message(st: ShiftState) -> str:
    """Blocking message for a shift left open past the cutoff."""
    day: str = local_str(st.open_since, "%d.%m") if st.open_since else ""
    return t("punch.forgotten", day=day, since=local_hm(st.open_since))


def _too_frequent(conn, employee_id: int, kind: str, st: ShiftState) -> tuple[str, str] | None:
    """Refusal for punches that follow the previous one too closely or pile up within a day (taps, tests, retries)."""
    now: datetime = datetime.now(UTC)
    if kind == "out" and st.open_punch_id and st.open_since:
        mins: int = int((now - st.open_since).total_seconds() // 60)
        if mins < settings.early_out_minutes:
            return ("too_soon_out", t("punch.too_soon_out", n=mins))
    last = conn.execute(
        "SELECT kind, ts_utc FROM live_punches WHERE employee_id = ? ORDER BY ts_utc DESC, id DESC LIMIT 1",
        (employee_id,),
    ).fetchone()
    if kind == "in" and last and last["kind"] == "out":
        mins = int((now - parse_utc(last["ts_utc"])).total_seconds() // 60)
        if mins < settings.min_break_minutes:
            return ("too_soon_in", t("punch.too_soon_in", at=local_hm(last["ts_utc"])))
    if settings.max_punches_per_day:
        day = local_date(now)
        start, end = day_bounds(day, day)
        n: int = conn.execute(
            "SELECT COUNT(*) FROM live_punches WHERE employee_id = ? AND ts_utc >= ? AND ts_utc < ?",
            (employee_id, utc_str(start), utc_str(end)),
        ).fetchone()[0]
        if n >= settings.max_punches_per_day:
            return ("daily_limit", t("punch.daily_limit", n=n))
    return None


@router.get("/status", response_model=Status)
def status(user: Annotated[Me, Depends(current_user)]) -> Status:
    """Return the next expected punch kind and the employee's assigned sites."""
    with connect() as conn:
        last = conn.execute(
            "SELECT id, site_id, kind, ts_utc FROM live_punches WHERE employee_id = ? "
            "ORDER BY ts_utc DESC, id DESC LIMIT 1",
            (user.id,),
        ).fetchone()
        st: ShiftState = effective_state(conn, user.id)
        sites = conn.execute(
            "SELECT s.id, s.name FROM sites s JOIN employee_sites es ON es.site_id = s.id "
            "WHERE es.employee_id = ? ORDER BY s.name",
            (user.id,),
        ).fetchall()
    return Status(
        next_kind="out" if st.state in ("open", "pending_in") else "in",
        last=PunchOut(**dict(last)) if last else None,
        sites=[dict(s) for s in sites],
        state=st.state,
        open_since=local_str(st.open_since, "%Y-%m-%dT%H:%M")
        if st.open_since
        else None,
        open_site_id=st.open_site_id,
        rejected_note=st.rejected_note,
        late_in_hour=settings.late_in_hour,
        open_minutes=int((datetime.now(UTC) - st.open_since).total_seconds() // 60)
        if st.open_since
        else None,
        early_out_minutes=settings.early_out_minutes,
    )


@router.get("/site/{key}")
def site_by_key(
    key: str, user: Annotated[Me, Depends(current_user)]
) -> dict[str, object]:
    """Resolve a scanned QR key to one of the employee's assigned sites.

    Raises:
        HTTPException: 404 for an unknown key or a site not assigned to the employee (same answer for both).
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT s.id, s.name FROM sites s JOIN employee_sites es ON es.site_id = s.id "
            "WHERE s.qr_key = ? AND es.employee_id = ?",
            (key, user.id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("err.qr_unknown"))
    return dict(row)


@router.post("/failure", status_code=204)
def geo_failure(body: GeoFailure, user: Annotated[Me, Depends(current_user)]) -> None:
    """Log a punch that failed on the phone (no GPS fix), so admins can diagnose it in the attempts list."""
    logger.warning(
        f"punch geo failure employee={user.id} reason={body.reason} detail={body.detail}"
    )
    record_attempt(
        user.id, body.site_id, body.kind, 0, 0, 0, body.reason, detail=body.detail
    )


@router.post("", response_model=PunchOut, status_code=201)
def punch(body: PunchIn, user: Annotated[Me, Depends(current_user)]) -> PunchOut:
    """Record an explicit clock in or out at an assigned site.

    In `flag` mode (default) a punch outside the zone or with poor GPS is stored and flagged for admin review;
    in `block` mode it is rejected. The optional hard limits `geofence_block_over_m` and `geofence_max_accuracy_m`
    (off by default) reject in either mode.

    Args:
        body: Site and reported position.
        user: Authenticated employee.

    Returns:
        The stored punch.

    Raises:
        HTTPException: 403 if the site is not assigned (or, in block mode, the position is outside the geofence),
            422 in block mode if the GPS fix is too inaccurate, 409 when the kind does not fit the current state
            (see `effective_state`), including any punch while a forgotten shift is still undeclared, and when the
            punch is too soon after the previous one or over the daily cap (`_too_frequent`).
    """

    def reject(
        status: int, reason: str, detail: str, dist: float | None = None
    ) -> NoReturn:
        record_attempt(
            user.id,
            body.site_id,
            body.kind,
            body.lat,
            body.lon,
            body.accuracy_m,
            reason,
            dist,
        )
        raise HTTPException(status_code=status, detail=detail)

    if body.lat == 0 and body.lon == 0:
        reject(422, "bad_location", t("punch.bad_location"))
    hard: float = settings.geofence_max_accuracy_m
    if hard > 0 and body.accuracy_m > hard:
        reject(
            422,
            "low_accuracy",
            t("punch.low_accuracy", m=f"{body.accuracy_m:.0f}"),
        )
    low_accuracy: bool = body.accuracy_m > MAX_ACCURACY_M
    if low_accuracy and settings.geofence_mode == "block":
        reject(422, "low_accuracy", t("punch.low_accuracy", m=f"{body.accuracy_m:.0f}"))
    with connect() as conn:
        row = conn.execute(
            "SELECT s.* FROM sites s JOIN employee_sites es ON es.site_id = s.id "
            "WHERE es.employee_id = ? AND s.id = ?",
            (user.id, body.site_id),
        ).fetchone()
        if row is None:
            reject(403, "not_assigned", t("err.site_not_assigned"))
        site: Site = Site(
            name=row["name"], lat=row["lat"], lon=row["lon"], radius_m=row["radius_m"]
        )
        inside: bool = is_inside(site, body.lat, body.lon, body.accuracy_m)
        dist: float = distance_m(site.lat, site.lon, body.lat, body.lon)
        limit: float = settings.geofence_block_over_m
        if limit > 0 and dist > limit:
            far: str = f"{dist / 1000:.1f} km" if dist >= 1000 else f"{dist:.0f} m"
            logger.warning(
                f"punch rejected too far employee={user.id} site={site.name} distance={far}"
            )
            reject(
                403,
                "too_far",
                t("punch.too_far", site=site.name, far=far),
                dist,
            )
        if not inside and settings.geofence_mode == "block":
            logger.warning(
                f"punch rejected outside geofence employee={user.id} site={site.name}"
            )
            reject(403, "outside_zone", t("punch.outside_zone"), dist)
        conn.execute("BEGIN IMMEDIATE")  # serialize check+insert (double tap)
        st: ShiftState = effective_state(conn, user.id)
        kind: str = body.kind
        refusal: tuple[str, str] | None = None
        if st.state == "forgotten":
            refusal = ("forgotten_out", forgotten_message(st))
        elif kind == "out" and st.state == "closed":
            refusal = (
                "no_open_shift",
                t("punch.no_open_shift"),
            )
        elif kind == "in" and st.state != "closed":
            refusal = (
                "already_in",
                t("punch.already_in", since=local_hm(st.open_since)),
            )
        if refusal is None:
            refusal = _too_frequent(conn, user.id, kind, st)
        if refusal:
            conn.rollback()  # release the write lock before logging the attempt
            reject(409, refusal[0], refusal[1], dist)
        cur = conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, lat, lon, accuracy_m, inside, distance_m) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user.id,
                body.site_id,
                kind,
                body.lat,
                body.lon,
                body.accuracy_m,
                int(inside),
                round(dist, 1),
            ),
        )
        saved = conn.execute(
            "SELECT id, site_id, kind, ts_utc FROM punches WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    flagged: bool = (not inside) or low_accuracy
    if flagged:
        logger.warning(
            f"punch flagged employee={user.id} site={site.name} distance={dist:.0f}m accuracy={body.accuracy_m:.0f}m"
        )
    warning: str | None = None
    if not inside:
        far: str = f"{dist / 1000:.1f} km" if dist >= 1000 else f"{dist:.0f} m"
        warning = t("punch.warn_outside", far=far)
    elif low_accuracy:
        warning = t("punch.warn_gps")
    return PunchOut(**dict(saved), flagged=flagged, warning=warning)
