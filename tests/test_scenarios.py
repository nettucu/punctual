"""Edge scenarios across modules: DST and midnight shifts, leave rules, data isolation."""

from datetime import date, timedelta

import pytest
from test_leave import login, next_monday

from punctual import db
from punctual.clock import today
from punctual.reports import build_report


def seed_shift(emp: int, t_in: str, t_out: str, site: int = 1) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sites (id, name, lat, lon, radius_m) VALUES (1, 'T', 44.4, 26.1, 100)"
        )
        for kind, ts in (("in", t_in), ("out", t_out)):
            conn.execute(
                "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
                "VALUES (?, ?, ?, ?, 44.4, 26.1, 5, 1)",
                (emp, site, kind, ts),
            )


def test_shift_across_spring_forward_loses_an_hour(client):
    # 2026-03-29 03:00 Bucharest jumps to 04:00: 22:00 local to 06:00 local is 7 real hours.
    seed_shift(1, "2026-03-28T20:00:00Z", "2026-03-29T03:00:00Z")
    r = build_report(date(2026, 3, 28), date(2026, 3, 29))
    assert [d.hours for d in r.days] == [7.0]


def test_shift_across_fall_back_gains_an_hour(client):
    # 2026-10-25 04:00 Bucharest falls back to 03:00: 22:00 local to 06:00 local is 9 real hours.
    seed_shift(1, "2026-10-24T19:00:00Z", "2026-10-25T04:00:00Z")
    r = build_report(date(2026, 10, 24), date(2026, 10, 25))
    assert [d.hours for d in r.days] == [9.0]


def test_midnight_shift_counts_on_start_day(client):
    seed_shift(
        1, "2026-03-10T20:00:00Z", "2026-03-11T02:00:00Z"
    )  # 22:00 -> 04:00 local
    r = build_report(date(2026, 3, 10), date(2026, 3, 11))
    assert [(d.day, d.hours) for d in r.days] == [("2026-03-10", 6.0)]


def test_employee_cannot_read_other_employees_data(client):
    seed_shift(2, "2026-03-10T06:00:00Z", "2026-03-10T14:00:00Z")
    login(client, "ana@example.com")
    r = client.get("/api/reports/hours?from=2026-03-01&to=2026-03-31")
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert "Boss" not in r.text
    assert (
        client.get("/api/corrections/punches?from=2026-03-01&to=2026-03-31").status_code
        == 403
    )
    assert client.get("/api/leave").status_code == 403


def test_leave_over_a_weekend_only_is_refused(client):
    login(client, "ana@example.com")
    sat = next_monday() - timedelta(days=2)
    r = client.post(
        "/api/leave",
        json={
            "kind": "co",
            "date_from": sat.isoformat(),
            "date_to": (sat + timedelta(days=1)).isoformat(),
        },
    )
    assert r.status_code == 422


def test_overlapping_leave_refused_but_allowed_after_rejection(client):
    login(client, "ana@example.com")
    d = next_monday().isoformat()
    body = {"kind": "co", "date_from": d, "date_to": d}
    first = client.post("/api/leave", json=body)
    assert first.status_code == 201
    assert client.post("/api/leave", json=body).status_code == 409
    client.post("/auth/logout")
    login(client, "boss@example.com")
    assert (
        client.post(
            f"/api/leave/{first.json()['id']}/reject", json={"reason": "nu se poate"}
        ).status_code
        == 200
    )
    client.post("/auth/logout")
    login(client, "ana@example.com")
    assert client.post("/api/leave", json=body).status_code == 201


@pytest.mark.parametrize(
    ("offset_from", "offset_to", "status"),
    [(0, 61, 422), (-400, -399, 422), (3000, 3001, 422), (3, 1, 422)],
)
def test_leave_bad_ranges(client, offset_from, offset_to, status):
    login(client, "ana@example.com")
    t = today()
    r = client.post(
        "/api/leave",
        json={
            "kind": "co",
            "date_from": (t + timedelta(days=offset_from)).isoformat(),
            "date_to": (t + timedelta(days=offset_to)).isoformat(),
        },
    )
    assert r.status_code == status


def test_leave_decision_is_final(client):
    login(client, "ana@example.com")
    d = next_monday().isoformat()
    rid = client.post(
        "/api/leave", json={"kind": "zl", "date_from": d, "date_to": d}
    ).json()["id"]
    client.post("/auth/logout")
    login(client, "boss@example.com")
    assert client.post(f"/api/leave/{rid}/approve", json={}).status_code == 200
    assert client.post(
        f"/api/leave/{rid}/reject", json={"reason": "changed my mind"}
    ).status_code in (404, 409)
