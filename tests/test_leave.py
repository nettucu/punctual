from datetime import date, timedelta

from punctual.clock import today
from punctual.holidays import holiday_name, orthodox_easter, working_days
from punctual.leave import leave_days
from punctual.reports import build_report, to_csv

PW = "Correct-Horse-9-battery"


def login(client, email):
    assert (
        client.post("/auth/login", json={"email": email, "password": PW}).status_code
        == 200
    )


def next_monday() -> date:
    d = today() + timedelta(days=7)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def test_easter_and_holidays():
    assert orthodox_easter(2026) == date(2026, 4, 12)
    assert orthodox_easter(2027) == date(2027, 5, 2)
    assert holiday_name(date(2026, 4, 10)) == "Good Friday"
    assert holiday_name(date(2026, 4, 13)) == "Orthodox Easter"
    assert holiday_name(date(2026, 5, 31)) == "Pentecost"
    assert holiday_name(date(2026, 12, 1)) == "National Day of Romania"
    assert holiday_name(date(2026, 3, 10)) is None
    # Mon 2026-11-30 holiday, Tue-Fri working, 2026-12-01 holiday
    assert working_days(date(2026, 11, 30), date(2026, 12, 4)) == [
        date(2026, 12, d) for d in (2, 3, 4)
    ]


def test_leave_workflow_and_report_marks(client):
    mon = next_monday()
    login(client, "ana@example.com")
    body = {
        "kind": "co",
        "date_from": mon.isoformat(),
        "date_to": (mon + timedelta(days=6)).isoformat(),
    }
    assert client.post("/api/leave", json=body).status_code == 201
    assert client.post("/api/leave", json=body).status_code == 409  # overlap
    bad = {
        "kind": "zl",
        "date_from": (mon + timedelta(days=5)).isoformat(),
        "date_to": (mon + timedelta(days=6)).isoformat(),
    }
    assert client.post("/api/leave", json=bad).status_code == 422  # weekend only
    (mine,) = client.get("/api/leave/mine").json()
    assert mine["status"] == "pending" and mine["days"] >= 1
    assert client.get("/api/leave").status_code == 403

    rep = build_report(mon, mon + timedelta(days=6), 1)
    assert rep.leave == []  # pending does not count

    client.post("/auth/logout")
    login(client, "boss@example.com")
    assert client.get("/api/admin/pending-counts").json() == {
        "correction": 0,
        "leave": 1,
        "accounts": 0,
    }
    assert client.post(f"/api/leave/{mine['id']}/reject", json={}).status_code == 422
    assert client.post(f"/api/leave/{mine['id']}/approve", json={}).status_code == 200
    assert client.post(f"/api/leave/{mine['id']}/approve", json={}).status_code == 409

    rep = build_report(mon, mon + timedelta(days=6), 1)
    assert {x.code for x in rep.leave} == {"VAC"}
    assert len(rep.leave) == mine["days"]
    assert all(date.fromisoformat(x.day).weekday() < 5 for x in rep.leave)
    assert "VAC" in to_csv(rep)


def test_report_lists_holidays(client):
    rep = build_report(date(2026, 12, 1), date(2026, 12, 31))
    assert rep.holidays == {
        "2026-12-01": "National Day of Romania",
        "2026-12-25": "Christmas",
        "2026-12-26": "Christmas",
    }
    assert "Christmas" in to_csv(rep)
    login(client, "ana@example.com")
    assert (
        client.get("/api/holidays?from=2026-12-01&to=2026-12-31").json() == rep.holidays
    )


def test_holiday_inside_leave_range_not_counted(client):
    from punctual import db

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO leave_requests (employee_id, kind, date_from, date_to, status) "
            "VALUES (1, 'co', '2026-11-30', '2026-12-04', 'approved')"
        )
        days = leave_days(conn, date(2026, 11, 1), date(2026, 12, 31), 1)
    assert [x.day for x in days] == ["2026-12-02", "2026-12-03", "2026-12-04"]
    login(client, "ana@example.com")
    far = (today() + timedelta(days=800)).isoformat()
    body = {"kind": "co", "date_from": far, "date_to": far}
    assert client.post("/api/leave", json=body).status_code == 422


def test_medical_leave_allows_older_start_than_vacation(client):
    login(client, "ana@example.com")
    d = today() - timedelta(days=30)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    body = {"date_from": d.isoformat(), "date_to": d.isoformat()}
    assert client.post("/api/leave", json={**body, "kind": "co"}).status_code == 422
    assert client.post("/api/leave", json={**body, "kind": "cm"}).status_code == 201


def test_init_db_migrates_old_leave_table_keeping_rows(tmp_path):
    import sqlite3

    from punctual import db

    path = str(tmp_path / "old.db")
    db.init_db(path)
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO employees (name, email, password_hash) VALUES ('A', 'a@example.com', 'x')"
        )
        conn.executescript(
            "DROP TRIGGER leave_decided_final; DROP TRIGGER leave_no_delete; DROP INDEX idx_leave_emp; DROP TABLE leave_requests;"
            "CREATE TABLE leave_requests (id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, "
            "kind TEXT NOT NULL CHECK (kind IN ('co', 'zl')), date_from TEXT NOT NULL, "
            "date_to TEXT NOT NULL CHECK (date_to >= date_from), note TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT 'x', "
            "decided_by INTEGER, decided_at TEXT, decision_note TEXT);"
            "INSERT INTO leave_requests (employee_id, kind, date_from, date_to) VALUES (1, 'co', '2026-01-05', '2026-01-06');"
        )
    db.init_db(path)
    with db.connect(path) as conn:
        assert (
            conn.execute("SELECT kind FROM leave_requests").fetchone()["kind"] == "co"
        )
        conn.execute(
            "INSERT INTO leave_requests (employee_id, kind, date_from, date_to) VALUES (1, 'cm', '2026-02-02', '2026-02-02')"
        )
        with __import__("pytest").raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM leave_requests")


def test_medical_leave_limit_is_60_days(client):
    login(client, "ana@example.com")

    def post(days_back):
        d = today() - timedelta(days=days_back)
        while d.weekday() >= 5:
            d -= timedelta(days=1)  # keeps the start inside the limit
        return client.post(
            "/api/leave",
            json={"kind": "cm", "date_from": d.isoformat(), "date_to": d.isoformat()},
        )

    assert post(55).status_code == 201
    assert post(70).status_code == 422
