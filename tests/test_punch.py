from punctual import db
from punctual.settings import settings

PW = "Correct-Horse-9-battery"
SITE = {"name": "Main Clinic", "lat": 44.4268, "lon": 26.1025, "radius_m": 100}
HERE = {"lat": 44.4269, "lon": 26.1025, "accuracy_m": 10}


def login(client, email):
    assert (
        client.post("/auth/login", json={"email": email, "password": PW}).status_code
        == 200
    )


def setup_site(client) -> int:
    login(client, "boss@example.com")
    sid = client.post("/api/sites", json=SITE).json()["id"]
    emp = next(
        e
        for e in client.get("/api/employees").json()
        if e["email"] == "ana@example.com"
    )
    r = client.put(
        f"/api/employees/{emp['id']}",
        json={
            "name": "Ana",
            "role": "employee",
            "norma_h": 8,
            "active": True,
            "site_ids": [sid],
        },
    )
    assert r.status_code == 200
    client.post("/auth/logout")
    login(client, "ana@example.com")
    return sid


def test_in_out_alternates(client):
    sid = setup_site(client)
    assert client.get("/api/punch/status").json()["next_kind"] == "in"
    assert (
        client.post("/api/punch", json={"site_id": sid, "kind": "in", **HERE}).json()[
            "kind"
        ]
        == "in"
    )
    assert client.get("/api/punch/status").json()["next_kind"] == "out"
    assert (
        client.post("/api/punch", json={"site_id": sid, "kind": "out", **HERE}).json()[
            "kind"
        ]
        == "out"
    )


def test_outside_rejected_and_not_stored(client, monkeypatch):
    monkeypatch.setattr(settings, "geofence_mode", "block")
    sid = setup_site(client)
    r = client.post(
        "/api/punch",
        json={"site_id": sid, "kind": "in", "lat": 44.5, "lon": 26.1, "accuracy_m": 10},
    )
    assert r.status_code == 403
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 0


def test_poor_accuracy_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "geofence_mode", "block")
    sid = setup_site(client)
    assert (
        client.post(
            "/api/punch", json={"site_id": sid, "kind": "in", **HERE, "accuracy_m": 500}
        ).status_code
        == 422
    )


def test_unassigned_site_rejected(client):
    login(client, "boss@example.com")
    sid = client.post("/api/sites", json=SITE).json()["id"]
    client.post("/auth/logout")
    login(client, "ana@example.com")
    assert (
        client.post(
            "/api/punch", json={"site_id": sid, "kind": "in", **HERE}
        ).status_code
        == 403
    )


def test_punch_requires_auth(client):
    assert (
        client.post("/api/punch", json={"site_id": 1, "kind": "in", **HERE}).status_code
        == 401
    )


def test_employee_admin_flow(client):
    login(client, "boss@example.com")
    r = client.post(
        "/api/employees",
        json={"name": "Nou", "email": "nou@example.com", "password": PW},
    )
    assert r.status_code == 201
    assert (
        client.post(
            "/api/employees",
            json={"name": "Nou", "email": "NOU@example.com", "password": PW},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/employees",
            json={"name": "X", "email": "x@example.com", "password": "short"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/employees",
            json={
                "name": "X",
                "email": "x@example.com",
                "password": PW,
                "site_ids": [99],
            },
        ).status_code
        == 422
    )


def test_deactivate_revokes_session_and_blocks_self_lockout(client):
    login(client, "ana@example.com")
    ana = client.get("/auth/me").json()["id"]
    client.post("/auth/logout")
    login(client, "boss@example.com")
    boss = client.get("/auth/me").json()["id"]
    body = {
        "name": "Boss",
        "role": "admin",
        "norma_h": 8,
        "active": False,
        "site_ids": [],
    }
    assert client.put(f"/api/employees/{boss}", json=body).status_code == 409
    login_ana = client.post(
        "/auth/login", json={"email": "ana@example.com", "password": PW}
    )  # sets cookie on shared client
    assert login_ana.status_code == 200
    client.post("/auth/login", json={"email": "boss@example.com", "password": PW})
    r = client.put(
        f"/api/employees/{ana}",
        json={
            "name": "Ana",
            "role": "employee",
            "norma_h": 8,
            "active": False,
            "site_ids": [],
        },
    )
    assert r.status_code == 200
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE employee_id = ?", (ana,)
            ).fetchone()[0]
            == 0
        )
    client.post("/auth/logout")
    assert (
        client.post(
            "/auth/login", json={"email": "ana@example.com", "password": PW}
        ).status_code
        == 401
    )


def test_pages(client):
    assert client.get("/").status_code == 200
    assert client.get("/admin/employees").status_code == 200


def _old_in(sid, ts="2020-01-01T06:00:00Z"):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
            "VALUES (1, ?, 'in', ?, 44.4, 26.1, 5, 1)",
            (sid, ts),
        )


def _count_punches():
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0]


def test_forgotten_shift_blocks_out_and_in(client):
    sid = setup_site(client)
    _old_in(sid)
    s = client.get("/api/punch/status").json()
    assert s["state"] == "forgotten" and s["next_kind"] == "in"
    assert s["open_since"] == "2020-01-01T08:00" and s["open_site_id"] == sid
    for kind in ("out", "in"):
        r = client.post("/api/punch", json={"site_id": sid, "kind": kind, **HERE})
        assert r.status_code == 409 and "01.01" in r.json()["detail"]
    assert _count_punches() == 1  # no 400-hour shift was created
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM punch_attempts WHERE reason = 'forgotten_out'"
            ).fetchone()[0]
            == 2
        )


def test_forgotten_arrival_request_then_out(client, cutoff_far):
    from datetime import UTC, datetime, timedelta

    from punctual.clock import TZ

    sid = setup_site(client)
    arrived = (datetime.now(UTC) - timedelta(minutes=30)).astimezone(TZ)
    r = client.post(
        "/api/requests",
        json={
            "site_id": sid,
            "kind": "in",
            "local_time": arrived.strftime("%Y-%m-%dT%H:%M"),
            "note": "forgot to clock in",
        },
    )
    assert r.status_code == 201
    s = client.get("/api/punch/status").json()
    if s["state"] == "forgotten":  # test run right after 22:00 local
        return
    assert s["state"] == "pending_in" and s["next_kind"] == "out"
    r = client.post("/api/punch", json={"site_id": sid, "kind": "out", **HERE})
    assert r.status_code == 201
    assert client.get("/api/punch/status").json()["state"] == "closed"


def test_dashboard_lists_forgotten_shift_with_ids(client):
    sid = setup_site(client)
    _old_in(sid)
    client.post("/auth/logout")
    login(client, "boss@example.com")
    ov = client.get("/api/admin/overview").json()
    (f,) = ov["forgotten_open"]
    assert ov["clocked_in"] == []
    assert f["employee_id"] == 1 and f["site_id"] == sid and f["punch_id"] == 1
    assert f["since"] == "2020-01-01 08:00"


def test_status_reports_minutes_since_arrival(client):
    sid = setup_site(client)
    assert client.get("/api/punch/status").json()["open_minutes"] is None
    client.post("/api/punch", json={"site_id": sid, "kind": "in", **HERE})
    s = client.get("/api/punch/status").json()
    assert s["open_minutes"] == 0


def test_dashboard_follows_the_clock_page_rules(client):
    sid = setup_site(client)
    _old_in(sid)
    r = client.post(
        "/api/requests",
        json={
            "site_id": sid,
            "kind": "out",
            "local_time": "2020-01-01T16:00",
            "note": "forgot the departure",
        },
    )
    assert r.status_code == 201
    client.post("/auth/logout")
    login(client, "boss@example.com")
    ov = client.get("/api/admin/overview").json()
    assert ov["forgotten_open"] == [] and ov["clocked_in"] == []
    assert ov["pending_requests"] == 1


def test_dashboard_lists_forgotten_arrival_request_without_punch(client):
    sid = setup_site(client)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO correction_requests (employee_id, site_id, kind, ts_utc, note) "
            "VALUES (1, ?, 'in', '2020-01-01T06:00:00Z', 'forgot the arrival')",
            (sid,),
        )
    client.post("/auth/logout")
    login(client, "boss@example.com")
    (f,) = client.get("/api/admin/overview").json()["forgotten_open"]
    assert f["employee_id"] == 1 and f["punch_id"] is None


def _punch(client, sid, kind):
    return client.post("/api/punch", json={"site_id": sid, "kind": kind, **HERE})


def test_out_right_after_in_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "early_out_minutes", 5)
    sid = setup_site(client)
    _punch(client, sid, "in")
    r = _punch(client, sid, "out")
    assert r.status_code == 409 and "too soon" in r.json()["detail"]
    assert client.get("/api/punch/status").json()["next_kind"] == "out"


def test_in_right_after_out_is_refused(client, monkeypatch):
    sid = setup_site(client)
    _punch(client, sid, "in")
    _punch(client, sid, "out")
    monkeypatch.setattr(settings, "min_break_minutes", 15)
    r = _punch(client, sid, "in")
    assert r.status_code == 409 and "real arrival time" in r.json()["detail"]


def test_daily_cap(client, monkeypatch):
    sid = setup_site(client)
    monkeypatch.setattr(settings, "max_punches_per_day", 4)
    for kind in ("in", "out", "in", "out"):
        assert _punch(client, sid, kind).status_code == 201
    r = _punch(client, sid, "in")
    assert r.status_code == 409 and "4 punches" in r.json()["detail"]


def test_delete_employee_only_without_history(client):
    login(client, "ana@example.com")
    ana = client.get("/auth/me").json()["id"]
    sid = setup_site(client)
    client.post("/auth/logout")
    login(client, "boss@example.com")
    boss = client.get("/auth/me").json()["id"]
    assert client.delete(f"/api/employees/{boss}").status_code == 409
    fresh = client.post(
        "/api/employees",
        json={"name": "Typo", "email": "typo@example.com", "password": PW, "role": "employee", "norma_h": 8, "site_ids": [sid]},
    ).json()["id"]
    assert client.delete(f"/api/employees/{fresh}").status_code == 204
    assert client.delete(f"/api/employees/{fresh}").status_code == 404
    login(client, "ana@example.com")
    _punch(client, sid, "in")
    login(client, "boss@example.com")
    assert client.delete(f"/api/employees/{ana}").status_code == 409
