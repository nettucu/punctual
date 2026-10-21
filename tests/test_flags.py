from datetime import UTC, datetime, timedelta

from punctual import db
from punctual.reports import build_report, to_csv

PW = "Correct-Horse-9-battery"
SITE = {"name": "Main Clinic", "lat": 44.4268, "lon": 26.1025, "radius_m": 100}
FAR = {
    "lat": 44.4322,
    "lon": 26.1025,
    "accuracy_m": 10,
}  # ~600 m: outside the radius, under the hard limit
TOO_FAR = {"lat": 44.4600, "lon": 26.1025, "accuracy_m": 10}  # ~3.6 km
NEAR = {"lat": 44.4269, "lon": 26.1025, "accuracy_m": 10}


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


def _range() -> dict[str, str]:
    d = datetime.now(UTC).date()
    return {"from": str(d - timedelta(days=2)), "to": str(d + timedelta(days=2))}


RANGE = _range()


def today_report():
    d = datetime.now(UTC).date()
    return build_report(d - timedelta(days=30), d + timedelta(days=30))


def test_outside_punch_is_recorded_and_flagged(client):
    sid = setup(client)
    r = client.post("/api/punch", json={"site_id": sid, "kind": "in", **FAR})
    assert r.status_code == 201
    body = r.json()
    assert body["flagged"] is True and "outside the zone" in body["warning"]
    with db.connect() as conn:
        row = conn.execute("SELECT inside, distance_m FROM punches").fetchone()
    assert row["inside"] == 0 and 400 < row["distance_m"] < 1000


def test_inside_punch_not_flagged(client):
    sid = setup(client)
    body = client.post("/api/punch", json={"site_id": sid, "kind": "in", **NEAR}).json()
    assert body["flagged"] is False and body["warning"] is None


def test_mediocre_gps_flagged_not_rejected(client):
    sid = setup(client)
    r = client.post(
        "/api/punch", json={"site_id": sid, "kind": "in", **NEAR, "accuracy_m": 120}
    )
    assert r.status_code == 201 and r.json()["flagged"] is True


def test_coarse_location_is_rejected_with_advice_and_logged(
    client, hard_geofence_limits
):
    sid = setup(client)
    r = client.post(
        "/api/punch", json={"site_id": sid, "kind": "in", **NEAR, "accuracy_m": 2000}
    )
    assert r.status_code == 422 and "too imprecise" in r.json()["detail"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 0
        assert (
            conn.execute("SELECT reason FROM punch_attempts").fetchone()["reason"]
            == "low_accuracy"
        )


def test_report_and_csv_show_flag_until_approved(client):
    sid = setup(client)
    client.post("/api/punch", json={"site_id": sid, "kind": "in", **FAR})
    (day,) = today_report().days
    assert day.location_flag is True
    assert "yes" in to_csv(today_report()).splitlines()[1].split(";")[9]
    client.post("/auth/logout")
    login(client, "boss@example.com")
    rows = client.get("/api/corrections/punches", params=RANGE).json()
    pid = rows[0]["id"]
    assert rows[0]["flagged"] is True and 400 < rows[0]["distance_m"] < 1000
    assert (
        rows[0]["lat"] == FAR["lat"] and rows[0]["site_lat"] == SITE["lat"]
    )  # for the map link
    assert (
        client.post(
            f"/api/corrections/punches/{pid}/approve-location",
            json={"reason": "gps gresit"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/corrections/punches/{pid}/approve-location",
            json={"reason": "gps gresit"},
        ).status_code
        == 409
    )
    assert today_report().days[0].location_flag is False
    row = client.get("/api/corrections/punches", params=RANGE).json()[0]
    assert row["flagged"] is False and row["approved"] is True


def test_admin_manual_punch_never_flagged(client):
    login(client, "boss@example.com")
    sid = client.post("/api/sites", json=SITE).json()["id"]
    client.post(
        "/api/corrections/punches",
        json={
            "employee_id": 1,
            "site_id": sid,
            "kind": "in",
            "local_time": "2026-03-10T08:00",
            "note": "forgot",
        },
    )
    rows = client.get(
        "/api/corrections/punches", params={"from": "2026-03-10", "to": "2026-03-10"}
    ).json()
    assert rows[0]["flagged"] is False


def test_dashboard_lists_location_flag(client):
    sid = setup(client)
    client.post("/api/punch", json={"site_id": sid, "kind": "in", **FAR})
    client.post("/auth/logout")
    login(client, "boss@example.com")
    flagged = client.get("/api/admin/overview").json()["flagged_days"]
    assert any("location" in f["notes"] for f in flagged)


def test_null_island_rejected(client):
    sid = setup(client)
    r = client.post(
        "/api/punch",
        json={"site_id": sid, "kind": "in", "lat": 0, "lon": 0, "accuracy_m": 5},
    )
    assert r.status_code == 422
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 0


def test_too_far_is_rejected_even_in_flag_mode(client, hard_geofence_limits):
    sid = setup(client)
    r = client.post("/api/punch", json={"site_id": sid, "kind": "in", **TOO_FAR})
    assert r.status_code == 403 and "too far" in r.json()["detail"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 0


def test_far_and_coarse_punches_are_recorded_and_flagged_by_default(client):
    sid = setup(client)
    r = client.post("/api/punch", json={"site_id": sid, "kind": "in", **TOO_FAR})
    assert r.status_code == 201 and r.json()["flagged"] is True
    client.post("/api/punch", json={"site_id": sid, "kind": "out", **NEAR})
    r = client.post(
        "/api/punch", json={"site_id": sid, "kind": "in", **NEAR, "accuracy_m": 2000}
    )
    assert r.status_code == 201 and r.json()["flagged"] is True


def test_running_shift_is_not_flagged_on_dashboard(client):
    sid = setup(client)
    client.post("/api/punch", json={"site_id": sid, "kind": "in", **NEAR})
    client.post("/auth/logout")
    login(client, "boss@example.com")
    o = client.get("/api/admin/overview").json()
    assert o["clocked_in"] and o["flagged_days"] == []
