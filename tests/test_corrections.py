from datetime import date

from punctual import db
from punctual.reports import build_report, to_csv

PW = "Correct-Horse-9-battery"


def login(client, email="boss@example.com"):
    assert (
        client.post("/auth/login", json={"email": email, "password": PW}).status_code
        == 200
    )


def site(client) -> int:
    return client.post(
        "/api/sites", json={"name": "T", "lat": 44.4, "lon": 26.1, "radius_m": 100}
    ).json()["id"]


def add(client, sid, kind, t, note="forgot to clock in"):
    return client.post(
        "/api/corrections/punches",
        json={
            "employee_id": 1,
            "site_id": sid,
            "kind": kind,
            "local_time": t,
            "note": note,
        },
    )


def test_missing_out_fixed_by_manual_punch(client):
    login(client)
    sid = site(client)
    assert add(client, sid, "in", "2026-03-10T08:00").status_code == 201
    day = build_report(date(2026, 3, 10), date(2026, 3, 10)).days[0]
    assert day.anomalies == ["missing out"] and day.hours == 0  # past the cutoff
    assert add(client, sid, "out", "2026-03-10T16:00").status_code == 201
    day = build_report(date(2026, 3, 10), date(2026, 3, 10)).days[0]
    assert day.hours == 8.0 and day.anomalies == [] and day.manual is True


def test_void_removes_from_report_but_keeps_row(client):
    login(client)
    sid = site(client)
    pid = add(client, sid, "in", "2026-03-10T08:00").json()["id"]
    assert (
        client.post(
            f"/api/corrections/punches/{pid}/void", json={"reason": "greseala"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/corrections/punches/{pid}/void", json={"reason": "greseala"}
        ).status_code
        == 409
    )
    assert build_report(date(2026, 3, 10), date(2026, 3, 10)).days == []
    rows = client.get("/api/corrections/punches?from=2026-03-10&to=2026-03-10").json()
    assert len(rows) == 1 and rows[0]["voided"] and rows[0]["created_by"] == "Boss"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 1


def test_validation(client):
    login(client)
    sid = site(client)
    assert add(client, sid, "in", "2999-01-01T08:00").status_code == 422
    assert add(client, sid, "in", "garbage").status_code == 422
    assert add(client, sid, "in", "2026-03-10T08:00", note="x").status_code == 422
    assert add(client, 999, "in", "2026-03-10T08:00").status_code == 422


def test_employee_forbidden(client):
    login(client, "ana@example.com")
    assert client.post("/api/corrections/punches", json={}).status_code == 403
    assert (
        client.get("/api/corrections/punches?from=2026-03-01&to=2026-03-02").status_code
        == 403
    )
    assert client.get("/api/admin/overview").status_code == 403


def test_void_keeps_clock_state_consistent(client):
    login(client)
    sid = site(client)
    client.put(
        "/api/employees/1",
        json={
            "name": "Ana",
            "role": "employee",
            "norma_h": 8,
            "active": True,
            "site_ids": [sid],
        },
    )
    pid = add(client, sid, "in", "2026-03-10T08:00").json()["id"]
    client.post(f"/api/corrections/punches/{pid}/void", json={"reason": "greseala"})
    client.post("/auth/logout")
    login(client, "ana@example.com")
    assert client.get("/api/punch/status").json()["next_kind"] == "in"


def test_overview_and_pages(client):
    login(client)
    d = client.get("/api/admin/overview").json()
    assert d["employees"] == 2 and d["clocked_in"] == []
    for path in ("/admin", "/admin/corrections", "/static/app.js"):
        assert client.get(path).status_code == 200


def _edit(client, pid, sid, kind, t, reason="time confirmed with the employee"):
    return client.post(
        f"/api/corrections/punches/{pid}/edit",
        json={"site_id": sid, "kind": kind, "local_time": t, "reason": reason},
    )


def test_edit_voids_original_and_links_replacement(client):
    login(client)
    sid = site(client)
    pid = add(client, sid, "in", "2026-03-10T08:00").json()["id"]
    add(client, sid, "out", "2026-03-10T16:00")
    r = _edit(client, pid, sid, "in", "2026-03-10T09:00")
    assert r.status_code == 201
    new = r.json()["id"]
    day = build_report(date(2026, 3, 10), date(2026, 3, 10)).days[0]
    assert day.hours == 7.0 and day.first_in == "09:00"
    rows = {
        x["id"]: x
        for x in client.get(
            "/api/corrections/punches?from=2026-03-10&to=2026-03-10"
        ).json()
    }
    assert rows[pid]["voided"] and rows[pid]["void_reason"].startswith("Edited:")
    assert rows[new]["corrects_id"] == pid and "08:00 arrival" in rows[new]["was"]
    # the original is final; edit the current version instead
    assert _edit(client, pid, sid, "in", "2026-03-10T09:30").status_code == 409
    assert (
        _edit(client, new, sid, "in", "2026-03-10T09:00").status_code == 422
    )  # no change
    assert _edit(client, 999, sid, "in", "2026-03-10T09:00").status_code == 404
    assert (
        _edit(client, new, sid, "in", "2026-03-10T09:30", reason="x").status_code == 422
    )


def test_edit_requires_admin(client):
    login(client)
    sid = site(client)
    pid = add(client, sid, "in", "2026-03-10T08:00").json()["id"]
    client.post("/auth/logout")
    login(client, "ana@example.com")
    assert _edit(client, pid, sid, "in", "2026-03-10T09:00").status_code == 403


def test_history_lists_every_change(client):
    login(client)
    sid = site(client)
    a = add(client, sid, "in", "2026-03-10T08:00").json()["id"]
    b = add(client, sid, "out", "2026-03-10T16:00").json()["id"]
    _edit(client, a, sid, "in", "2026-03-10T09:00")
    client.post(f"/api/corrections/punches/{b}/void", json={"reason": "dublura"})
    ev = client.get("/api/audit?from=2026-03-10&to=2026-03-10").json()
    by = {e["action"]: e for e in ev}
    assert [e["action"] for e in ev].count("added") == 2
    assert by["edited"]["before"].startswith("2026-03-10 08:00 arrival")
    assert by["edited"]["after"].startswith("2026-03-10 09:00 arrival")
    assert by["edited"]["admin"] == "Boss" and by["edited"]["at"]
    assert by["voided"]["reason"] == "dublura" and by["voided"]["after"] is None
    assert ev[0]["at"] >= ev[-1]["at"]
    assert client.get("/api/audit?from=2026-03-11&to=2026-03-11").json() == []
    csv = client.get("/api/audit/export.csv?from=2026-03-10&to=2026-03-10")
    assert csv.status_code == 200 and "Edited" in csv.text
    assert client.get("/admin/history").status_code == 200
    client.post("/auth/logout")
    login(client, "ana@example.com")
    assert client.get("/api/audit?from=2026-03-10&to=2026-03-10").status_code == 403


def _review(client, day="2026-03-10", reason="confirmed with the employee", emp=1):
    return client.post(
        "/api/corrections/reviews",
        json={"employee_id": emp, "day": day, "reason": reason},
    )


def test_review_clears_flag_until_a_new_one_appears(client):
    login(client)
    sid = site(client)
    a = add(client, sid, "in", "2026-03-10T05:00").json()["id"]
    add(client, sid, "out", "2026-03-10T19:00")  # 14 h -> long shift
    day = build_report(date(2026, 3, 10), date(2026, 3, 10)).days[0]
    assert day.anomalies == ["long shift"] and not day.reviewed
    assert _review(client, reason="x").status_code == 422
    assert _review(client, day="2026-03-11").status_code == 404  # no punches
    assert _review(client).status_code == 201
    assert _review(client).status_code == 409  # already reviewed
    day = build_report(date(2026, 3, 10), date(2026, 3, 10)).days[0]
    assert day.reviewed and day.anomalies == ["long shift"]  # still reported, marked
    assert "yes" in to_csv(build_report(date(2026, 3, 10), date(2026, 3, 10)))
    # editing the day so a different anomaly appears flags it again
    client.post(f"/api/corrections/punches/{a}/void", json={"reason": "greseala"})
    day = build_report(date(2026, 3, 10), date(2026, 3, 10)).days[0]
    assert day.anomalies == ["orphan out"] and not day.reviewed
    ev = client.get("/api/audit?from=2026-03-10&to=2026-03-10").json()
    rv = next(e for e in ev if e["action"] == "reviewed")
    assert rv["admin"] == "Boss" and "[long shift]" in rv["reason"]
    assert rv["day"] == "2026-03-10"


def test_review_nothing_flagged_and_admin_only(client):
    login(client)
    sid = site(client)
    add(client, sid, "in", "2026-03-10T08:00")
    add(client, sid, "out", "2026-03-10T16:00")
    assert _review(client).status_code == 409  # clean day
    client.post("/auth/logout")
    login(client, "ana@example.com")
    assert _review(client).status_code == 403
    import sqlite3

    import pytest

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO day_reviews (employee_id, day, notes, reason, reviewed_by) VALUES (1, '2026-03-10', 'x', 'y', 2)"
        )
    for sql in ("DELETE FROM day_reviews", "UPDATE day_reviews SET reason = 'z'"):
        with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
            conn.execute(sql)


def test_dashboard_hides_reviewed_days(client):
    login(client)
    sid = site(client)
    add(client, sid, "in", "2026-03-10T05:00")
    add(client, sid, "out", "2026-03-10T19:00")
    from datetime import UTC, datetime, timedelta

    from punctual.clock import TZ

    day = (datetime.now(UTC) - timedelta(days=2)).astimezone(TZ).date()
    for kind, hh in (("in", "05:00"), ("out", "19:00")):
        add(client, sid, kind, f"{day}T{hh}")
    flagged = client.get("/api/admin/overview").json()["flagged_days"]
    assert any(f["day"] == day.isoformat() for f in flagged)
    assert _review(client, day=day.isoformat()).status_code == 201
    flagged = client.get("/api/admin/overview").json()["flagged_days"]
    assert not any(f["day"] == day.isoformat() for f in flagged)
