"""Romanian public holidays and working days; names come from the `holiday.*` messages. Computed, so no yearly upkeep or dependency."""

from __future__ import annotations

from datetime import date, timedelta
from functools import cache

from punctual.i18n import t

# (month, day) -> name; the movable ones (Orthodox Easter cycle) are added by `holidays_in_year`.
FIXED: dict[tuple[int, int], str] = {
    (1, 1): "new_year",
    (1, 2): "new_year",
    (1, 6): "epiphany",
    (1, 7): "st_john",
    (1, 24): "unification",
    (5, 1): "labour_day",
    (6, 1): "childrens_day",
    (8, 15): "dormition",
    (11, 30): "st_andrew",
    (12, 1): "national_day",
    (12, 25): "christmas",
    (12, 26): "christmas",
}


def orthodox_easter(year: int) -> date:
    """Orthodox Easter Sunday in the Gregorian calendar (Meeus' Julian algorithm, valid 1900-2099)."""
    a, b, c = year % 4, year % 7, year % 19
    d: int = (19 * c + 15) % 30
    e: int = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


@cache
def holidays_in_year(year: int) -> dict[date, str]:
    """All public holidays of a year: date to message key (Good Friday, Easter, Pentecost and their Mondays included)."""
    out: dict[date, str] = {date(year, m, d): n for (m, d), n in FIXED.items()}
    easter: date = orthodox_easter(year)
    pentecost: date = easter + timedelta(days=49)
    out[easter - timedelta(days=2)] = "good_friday"
    out[easter] = "easter"
    out[easter + timedelta(days=1)] = "easter"
    out[pentecost] = "pentecost"
    out[pentecost + timedelta(days=1)] = "pentecost"
    return out


def holiday_name(day: date) -> str | None:
    """Name of the public holiday on `day`, None for an ordinary day."""
    key: str | None = holidays_in_year(day.year).get(day)
    return None if key is None else t(f"holiday.{key}")


def holidays_between(d_from: date, d_to: date) -> dict[date, str]:
    """Public holidays in [d_from, d_to], date to localized name."""
    return {
        d: t(f"holiday.{key}")
        for y in range(d_from.year, d_to.year + 1)
        for d, key in holidays_in_year(y).items()
        if d_from <= d <= d_to
    }


def working_days(d_from: date, d_to: date) -> list[date]:
    """Days in [d_from, d_to] that are neither a weekend nor a public holiday."""
    hol: dict[date, str] = holidays_between(d_from, d_to)
    days: list[date] = [
        d_from + timedelta(days=i) for i in range((d_to - d_from).days + 1)
    ]
    return [d for d in days if d.weekday() < 5 and d not in hol]
