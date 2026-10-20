from datetime import UTC, date, datetime

from punctual import db
from punctual.reports import Punch, build_report, pair_shifts, to_csv

PW = "Correct-Horse-9-battery"


def P(kind, h, m=0, day=10, emp=1):
    return Punch(emp, "S", kind, datetime(2026, 3, day, h, m, tzinfo=UTC))


def test_pair_simple():
    (s,) = pair_shifts([P("in", 6), P("out", 14, 30)])
    assert s.hours == 8.5 and s.anomaly is None


def test_open_shift():
    # 06:00Z = 08:00 local; open until the 22:00 local cutoff (20:00Z), then a missing out
    (s,) = pair_shifts([P("in", 6)], now=datetime(2026, 3, 10, 19, 59, tzinfo=UTC))
    assert s.hours is None and s.anomaly == "open"
    (s,) = pair_shifts([P("in", 6)], now=datetime(2026, 3, 10, 20, 0, tzinfo=UTC))
    assert s.anomaly == "missing out"


def test_long_shift_counted_but_marked():
    (s,) = pair_shifts([P("in", 6), P("out", 18, 1)])
    assert s.hours == 12.02 and s.anomaly == "long shift"
    (s,) = pair_shifts([P("in", 6), P("out", 18)])
    assert s.anomaly is None


def test_missing_out_then_new_in():
    a, b = pair_shifts([P("in", 6), P("in", 6, day=11), P("out", 10, day=11)])
    assert a.anomaly == "missing out" and b.hours == 4.0


def test_orphan_out():
    (s,) = pair_shifts([P("out", 10)])
    assert s.anomaly == "orphan out" and s.hours is None


def test_shift_counts_on_start_day_in_bucharest_time():
    # 22:30Z on Mar 10 = 00:30 local Mar 11 (EET, UTC+2) -> counts on the 11th
    (s,) = pair_shifts([P("in", 22, 30), P("out", 23, 30)])
    assert s.local_day == date(2026, 3, 11)


def test_two_employees_dont_mix():
    shifts = pair_shifts(
        [P("in", 6, emp=1), P("in", 7, emp=2), P("out", 14, emp=1), P("out", 15, emp=2)]
    )
    assert [s.hours for s in shifts] == [8.0, 8.0]


def seed(client):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sites (name, lat, lon, radius_m) VALUES ('T', 44.4, 26.1, 100)"
        )
        for kind, ts in [
            ("in", "2026-03-10T06:00:00Z"),
            ("out", "2026-03-10T15:00:00Z"),
        ]:
            conn.execute(
                "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
                "VALUES (1, 1, ?, ?, 44.4, 26.1, 5, 1)",
                (kind, ts),
            )


def test_build_report_and_csv(client):
    seed(client)
    r = build_report(date(2026, 3, 1), date(2026, 3, 31))
    (d,) = r.days
    assert (
        d.hours == 9.0
        and d.over_norma
        and d.first_in == "08:00"
        and d.last_out == "17:00"
    )
    assert r.totals == {"Ana": 9.0}
    csv_text = to_csv(r)
    assert "Ana;2026-03-10;08:00;17:00;9,00;8;1,00" in csv_text


def test_csv_formula_injection_neutralized(client):
    with db.connect() as conn:
        conn.execute("UPDATE employees SET name = '=cmd|calc' WHERE id = 1")
    seed(client)
    assert "'=cmd|calc" in to_csv(build_report(date(2026, 3, 1), date(2026, 3, 31)))


def test_api_permissions(client):
    seed(client)
    q = "?from=2026-03-01&to=2026-03-31"
    assert client.get("/api/reports/hours" + q).status_code == 401
    client.post("/auth/login", json={"email": "ana@example.com", "password": PW})
    assert (
        client.get("/api/reports/hours" + q + "&employee_id=2").json()["days"][0][
            "employee"
        ]
        == "Ana"
    )  # forced to self
    assert client.get("/api/reports/hours.csv" + q).status_code == 403
    client.post("/auth/logout")
    client.post("/auth/login", json={"email": "boss@example.com", "password": PW})
    r = client.get("/api/reports/hours.csv" + q)
    assert r.status_code == 200 and r.text.startswith("﻿Employee")


def test_range_validation(client):
    client.post("/auth/login", json={"email": "boss@example.com", "password": PW})
    assert (
        client.get("/api/reports/hours?from=2026-03-31&to=2026-03-01").status_code
        == 422
    )
    assert (
        client.get("/api/reports/hours?from=2020-01-01&to=2026-03-01").status_code
        == 422
    )


def test_norma_drives_overtime(client):
    seed(client)
    with db.connect() as conn:
        conn.execute("UPDATE employees SET norma_h = 4 WHERE id = 1")
    (d,) = build_report(date(2026, 3, 1), date(2026, 3, 31)).days
    assert d.norma_h == 4 and d.overtime_h == 5.0 and d.over_norma
    with db.connect() as conn:
        conn.execute("UPDATE employees SET norma_h = 8 WHERE id = 1")
    assert build_report(date(2026, 3, 1), date(2026, 3, 31)).days[0].overtime_h == 1.0


def test_norma_validation(client):
    client.post("/auth/login", json={"email": "boss@example.com", "password": PW})
    base = {"name": "N", "email": "n@example.com", "password": PW}
    assert client.post("/api/employees", json={**base, "norma_h": 5}).status_code == 422
    r = client.post("/api/employees", json={**base, "norma_h": 6})
    assert r.status_code == 201 and r.json()["norma_h"] == 6
