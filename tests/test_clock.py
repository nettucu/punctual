from datetime import UTC, date, datetime, timedelta

import pytest

from punctual.clock import (
    TZ,
    TimeInputError,
    day_bounds,
    local_date,
    local_hm,
    local_str,
    parse_local,
    parse_utc,
    utc_str,
)


def test_utc_round_trip_and_local_text():
    ts = parse_utc("2026-07-01T05:30:00Z")
    assert utc_str(ts) == "2026-07-01T05:30:00Z"
    assert local_str("2026-07-01T05:30:00Z") == "2026-07-01 08:30"  # EEST, UTC+3
    assert local_hm(ts) == "08:30" and local_hm(None) == ""
    assert local_date("2026-07-01T22:30:00Z") == date(2026, 7, 2)


def test_day_bounds_follow_dst():
    # spring forward: a 23-hour local day
    start, end = day_bounds(date(2026, 3, 29), date(2026, 3, 29))
    assert start == datetime(2026, 3, 28, 22, tzinfo=UTC)
    assert end - start == timedelta(hours=23)
    # fall back: a 25-hour local day
    start, end = day_bounds(date(2026, 10, 25), date(2026, 10, 25))
    assert start == datetime(2026, 10, 24, 21, tzinfo=UTC)
    assert end - start == timedelta(hours=25)


def test_bad_input_raises_value_error_not_http():
    with pytest.raises(TimeInputError):
        day_bounds(date(2026, 3, 2), date(2026, 3, 1))
    with pytest.raises(TimeInputError):
        day_bounds(date(2025, 1, 1), date(2026, 3, 1))
    with pytest.raises(ValueError):
        parse_local("not a time")
    future = (datetime.now(TZ) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    with pytest.raises(TimeInputError, match="future"):
        parse_local(future)
    assert parse_local("2026-03-10T08:00") == datetime(2026, 3, 10, 6, tzinfo=UTC)


def test_bad_range_is_422_through_the_routes(client):
    client.post(
        "/auth/login",
        json={"email": "boss@example.com", "password": "Correct-Horse-9-battery"},
    )
    r = client.get(
        "/api/reports/hours", params={"from": "2026-03-02", "to": "2026-03-01"}
    )
    assert r.status_code == 422 and r.json()["detail"] == "'to' is before 'from'"
