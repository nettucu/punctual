"""Time in Punctual: UTC storage format, configured-timezone wall clock (`PUNCTUAL_TIMEZONE`), local days and the forgotten-shift cutoff.

Timestamps are stored as UTC strings in `ISO` format; everything shown to people or typed by them is local
(DST-aware). No other module should need `ZoneInfo` or the storage format.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from punctual.settings import settings

TZ: ZoneInfo = ZoneInfo(settings.timezone)
ISO: str = "%Y-%m-%dT%H:%M:%SZ"
MAX_RANGE_DAYS: int = 366
FUTURE_SLACK: timedelta = timedelta(minutes=5)


class TimeInputError(ValueError):
    """A time or date range typed by a person is unusable; routes answer it with 422 (see `main`)."""


def parse_utc(ts_utc: str) -> datetime:
    """Stored UTC string to an aware datetime."""
    return datetime.strptime(ts_utc, ISO).replace(tzinfo=UTC)


def utc_str(dt: datetime) -> str:
    """Aware datetime to the stored UTC string."""
    return dt.astimezone(UTC).strftime(ISO)


def local_str(ts: datetime | str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """A stored UTC string or aware datetime as local wall-clock text (default `YYYY-MM-DD HH:MM`)."""
    dt: datetime = parse_utc(ts) if isinstance(ts, str) else ts
    return dt.astimezone(TZ).strftime(fmt)


def local_hm(ts: datetime | str | None) -> str:
    """local `HH:MM`, empty for None."""
    return local_str(ts, "%H:%M") if ts else ""


def local_date(ts: datetime | str) -> date:
    """local calendar day of a stored UTC string or aware datetime."""
    dt: datetime = parse_utc(ts) if isinstance(ts, str) else ts
    return dt.astimezone(TZ).date()


def today() -> date:
    """Current local calendar day."""
    return datetime.now(TZ).date()


def parse_local(local_time: str) -> datetime:
    """Parse a local wall-clock ISO string (e.g. `2026-03-10T08:00`) into UTC.

    Raises:
        TimeInputError: invalid string, or a time more than a few minutes in the future.
    """
    try:
        local: datetime = datetime.fromisoformat(local_time)
    except ValueError as exc:
        raise TimeInputError("invalid local_time") from exc
    ts: datetime = local.replace(tzinfo=TZ).astimezone(UTC)
    if ts > datetime.now(UTC) + FUTURE_SLACK:
        raise TimeInputError("time is in the future")
    return ts


def day_bounds(d_from: date, d_to: date) -> tuple[datetime, datetime]:
    """UTC instants covering local days [d_from, d_to]: start of `d_from` to start of the day after `d_to`.

    Raises:
        TimeInputError: `d_to` before `d_from`, or a range over `MAX_RANGE_DAYS`.
    """
    if d_to < d_from:
        raise TimeInputError("'to' is before 'from'")
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        raise TimeInputError(f"range too large (max {MAX_RANGE_DAYS} days)")
    start: datetime = datetime.combine(d_from, time.min, tzinfo=TZ).astimezone(UTC)
    end: datetime = datetime.combine(
        d_to + timedelta(days=1), time.min, tzinfo=TZ
    ).astimezone(UTC)
    return start, end


def cutoff_after(ts: datetime) -> datetime:
    """First local `forgotten_cutoff_hour` strictly after `ts`, as UTC: when an open shift counts as forgotten.

    Args:
        ts: Aware datetime (any zone).
    """
    local: datetime = ts.astimezone(TZ)
    at: time = time(settings.forgotten_cutoff_hour)
    cut: datetime = datetime.combine(local.date(), at, tzinfo=TZ)
    if cut <= local:
        cut = datetime.combine(local.date() + timedelta(days=1), at, tzinfo=TZ)
    return cut.astimezone(UTC)
