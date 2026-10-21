import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from punctual import db
from punctual.attempts import purge_old
from punctual.settings import settings

PW = "Correct-Horse-9-battery"
SITE = {"name": "Main Clinic", "lat": 44.4268, "lon": 26.1025, "radius_m": 100}
NEAR = {"lat": 44.4269, "lon": 26.1025, "accuracy_m": 10}
TOO_FAR = {"lat": 44.4600, "lon": 26.1025, "accuracy_m": 10}


def login(client, email):
    assert (
        client.post("/auth/login", json={"email": email, "password": PW}).status_code
        == 200
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


def rows():
    with db.connect() as conn:
        return conn.execute(
            "SELECT reason, kind, distance_m, site_id FROM punch_attempts ORDER BY id"
        ).fetchall()


def test_rejections_are_logged_with_reason(client, hard_geofence_limits):
    sid = setup(client)
    client.post("/api/punch", json={"site_id": sid, "kind": "in", **TOO_FAR})  # too far
    client.post(
        "/api/punch", json={"site_id": sid, "kind": "out", **NEAR}
    )  # no open shift
    client.post(
        "/api/punch",
        json={"site_id": sid, "kind": "in", "lat": 0, "lon": 0, "accuracy_m": 5},
    )  # bad
    client.post(
        "/api/punch", json={"site_id": 999, "kind": "in", **NEAR}
    )  # not assigned, unknown site
    assert [r["reason"] for r in rows()] == [
        "too_far",
        "no_open_shift",
        "bad_location",
        "not_assigned",
    ]
    assert rows()[0]["distance_m"] > 3000 and rows()[3]["site_id"] is None


def test_already_in_is_logged_and_success_is_not(client):
    sid = setup(client)
    assert (
        client.post(
            "/api/punch", json={"site_id": sid, "kind": "in", **NEAR}
        ).status_code
        == 201
    )
    assert rows() == []  # accepted punches are not attempts
    assert (
        client.post(
            "/api/punch", json={"site_id": sid, "kind": "in", **NEAR}
        ).status_code
        == 409
    )
    assert [r["reason"] for r in rows()] == ["already_in"]


def test_admin_lists_attempts_employee_cannot(client, hard_geofence_limits):
    sid = setup(client)
    client.post("/api/punch", json={"site_id": sid, "kind": "in", **TOO_FAR})
    today = datetime.now(UTC).date()
    params = {
        "from": str(today - timedelta(days=1)),
        "to": str(today + timedelta(days=1)),
    }
    assert client.get("/api/attempts", params=params).status_code == 403
    client.post("/auth/logout")
    login(client, "boss@example.com")
    (a,) = client.get("/api/attempts", params=params).json()
    assert (
        a["employee"] == "Ana"
        and a["reason"] == "too_far"
        and a["site"] == "Main Clinic"
    )
    assert a["reason_label"] == "too far from the site" and a["site_lat"] == SITE["lat"]
    assert client.get("/api/admin/overview").json()["rejected_7d"] == 1


def test_retention_purges_old_but_db_protects_recent(client):
    sid = setup(client)
    with db.connect() as conn:
        for days in (100, 40, 5):
            conn.execute(
                "INSERT INTO punch_attempts (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, reason) "
                "VALUES (1, ?, 'in', strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?), 44.4, 26.1, 5, 'too_far')",
                (sid, f"-{days} days"),
            )
    assert purge_old() == 1  # only the 100-day-old one (retention 90)
    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute("DELETE FROM punch_attempts")  # would remove the 5-day-old row
    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute("UPDATE punch_attempts SET reason = 'x'")


def test_retention_floor_is_30_days(client, monkeypatch):
    sid = setup(client)
    monkeypatch.setattr(settings, "attempts_retention_days", 1)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO punch_attempts (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, reason) "
            "VALUES (1, ?, 'in', strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 days'), 44.4, 26.1, 5, 'too_far')",
            (sid,),
        )
    assert purge_old() == 0
    assert client.get("/admin/attempts").status_code == 200


def test_phone_geo_failure_is_logged(client):
    sid = setup(client)
    body = {"site_id": sid, "kind": "in", "reason": "geo_denied", "detail": "1 denied | iPhone"}
    assert client.post("/api/punch/failure", json=body).status_code == 204
    assert client.post("/api/punch/failure", json={**body, "reason": "x"}).status_code == 422
    with db.connect() as conn:
        r = conn.execute("SELECT reason, detail FROM punch_attempts").fetchall()
    assert [tuple(x) for x in r] == [("geo_denied", "1 denied | iPhone")]
