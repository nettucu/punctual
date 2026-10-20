import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from punctual import db
from punctual.clock import TZ
from punctual.reports import build_report

PW = "Correct-Horse-9-battery"
SITE = {"name": "Main Clinic", "lat": 44.4268, "lon": 26.1025, "radius_m": 100}


def login(client, email):
    assert (
        client.post("/auth/login", json={"email": email, "password": PW}).status_code
        == 200
    )


def local(hours_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(hours=hours_ago))
        .astimezone(TZ)
        .strftime("%Y-%m-%dT%H:%M")
    )


def setup(client) -> int:
    login(client, "boss@example.com")
    sid = client.post("/api/sites", json=SITE).json()["id"]
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
    client.post("/auth/logout")
    login(client, "ana@example.com")
    return sid


def ask(client, sid, kind="out", hours_ago=30, note="forgot to clock out"):
    return client.post(
        "/api/requests",
        json={
            "site_id": sid,
            "kind": kind,
            "local_time": local(hours_ago),
            "note": note,
        },
    )


def test_employee_creates_and_sees_own_request(client):
    sid = setup(client)
    assert ask(client, sid).status_code == 201
    (mine,) = client.get("/api/requests/mine").json()
    assert mine["status"] == "pending" and mine["kind"] == "out"


def test_validation(client):
    sid = setup(client)
    assert ask(client, sid, hours_ago=-5).status_code == 422  # future
    assert ask(client, sid, hours_ago=24 * 30).status_code == 422  # too old
    assert ask(client, 999).status_code == 422  # not assigned
    assert ask(client, sid, note="x").status_code == 422
    for _ in range(5):
        assert ask(client, sid).status_code == 201
    assert ask(client, sid).status_code == 429


def test_employee_cannot_use_admin_endpoints(client):
    sid = setup(client)
    rid = ask(client, sid).json()["id"]
    assert client.get("/api/requests").status_code == 403
    assert client.post(f"/api/requests/{rid}/approve", json={}).status_code == 403
    assert client.get("/api/requests/pending-count").status_code == 403


def test_approve_creates_audited_punch_and_fixes_report(client):
    sid = setup(client)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
            "VALUES (1, ?, 'in', strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-38 hours'), 44.4, 26.1, 5, 1)",
            (sid,),
        )
    rid = ask(client, sid, hours_ago=30).json()["id"]
    client.post("/auth/logout")
    login(client, "boss@example.com")
    assert client.get("/api/requests/pending-count").json() == {"count": 1}
    (r,) = client.get("/api/requests").json()
    assert r["employee"] == "Ana" and len(r["day_punches"]) >= 0
    ok = client.post(
        f"/api/requests/{rid}/approve", json={"reason": "confirmed with the employee"}
    )
    assert ok.status_code == 200
    with db.connect() as conn:
        p = conn.execute(
            "SELECT source, note, created_by FROM punches WHERE id = ?",
            (ok.json()["punch_id"],),
        ).fetchone()
    assert (
        p["source"] == "admin"
        and "forgot to clock" in p["note"]
        and p["created_by"] == 2
    )
    assert client.get("/api/requests/pending-count").json() == {"count": 0}
    today = datetime.now(UTC).date()
    days = build_report(today - timedelta(days=5), today + timedelta(days=1)).days
    assert any(d.manual and abs(d.hours - 8.0) < 0.05 for d in days)


def test_decisions_are_final(client):
    sid = setup(client)
    rid = ask(client, sid).json()["id"]
    client.post("/auth/logout")
    login(client, "boss@example.com")
    assert (
        client.post(f"/api/requests/{rid}/reject", json={"reason": ""}).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/requests/{rid}/reject", json={"reason": "nu era in tura"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/requests/{rid}/reject", json={"reason": "din nou"}
        ).status_code
        == 409
    )
    assert client.post(f"/api/requests/{rid}/approve", json={}).status_code == 409
    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute(
            "UPDATE correction_requests SET status = 'approved' WHERE id = ?", (rid,)
        )
    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute("DELETE FROM correction_requests")
    client.post("/auth/logout")
    login(client, "ana@example.com")
    (mine,) = client.get("/api/requests/mine").json()
    assert mine["status"] == "rejected" and mine["decision_note"] == "nu era in tura"


def test_admin_can_adjust_time_on_approve(client):
    sid = setup(client)
    rid = ask(client, sid, hours_ago=30).json()["id"]
    client.post("/auth/logout")
    login(client, "boss@example.com")
    r = client.post(
        f"/api/requests/{rid}/approve",
        json={"local_time": local(29), "reason": "left at 16:30 not 17"},
    )
    assert r.status_code == 200
    with db.connect() as conn:
        assert (
            "adjusted time"
            in conn.execute("SELECT note FROM punches ORDER BY id DESC").fetchone()[
                "note"
            ]
        )


def _old_in(sid):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
            "VALUES (1, ?, 'in', '2020-01-01T06:00:00Z', 44.4, 26.1, 5, 1)",
            (sid,),
        )


def _req(client, sid, kind, local_time):
    return client.post(
        "/api/requests",
        json={"site_id": sid, "kind": kind, "local_time": local_time, "note": "forgot"},
    )


def test_forgotten_departure_exempt_from_age_limit(client):
    sid = setup(client)
    _old_in(sid)
    assert _req(client, sid, "out", "2020-01-01T08:00").status_code == 422  # not after
    assert _req(client, sid, "in", local(1)).status_code == 409  # declare out first
    assert _req(client, sid, "out", "2020-01-01T16:30").status_code == 201
    assert client.get("/api/punch/status").json()["state"] == "closed"
    # with no forgotten shift, old requests are still refused
    assert _req(client, sid, "out", "2020-01-02T16:30").status_code == 422


def test_second_arrival_while_at_work_refused(client):
    sid = setup(client)
    assert _req(client, sid, "in", local(2)).status_code == 201
    st = client.get("/api/punch/status").json()["state"]
    if st == "forgotten":  # run right after 22:00 local
        return
    assert _req(client, sid, "in", local(1)).status_code == 409
    assert (
        _req(client, sid, "in", local(3)).status_code == 201
    )  # earlier: not the current state


def test_decided_requests_in_history(client):
    sid = setup(client)
    ok = ask(client, sid, hours_ago=30).json()["id"]
    no = ask(client, sid, hours_ago=31).json()["id"]
    client.post("/auth/logout")
    login(client, "boss@example.com")
    client.post(f"/api/requests/{ok}/approve", json={})
    client.post(f"/api/requests/{no}/reject", json={"reason": "nu era in tura"})
    today = datetime.now(UTC).date()
    ev = client.get(f"/api/audit?from={today - timedelta(days=3)}&to={today}").json()
    acts = sorted(e["action"] for e in ev)
    assert acts == ["request_approved", "request_rejected"]  # not also "added"
    rej = next(e for e in ev if e["action"] == "request_rejected")
    assert rej["reason"].endswith("/ nu era in tura")
