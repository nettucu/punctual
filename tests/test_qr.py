from punctual import db

PW = "Correct-Horse-9-battery"
HERE = {"lat": 44.4269, "lon": 26.1025, "accuracy_m": 10}
SITE = {"name": "Main Clinic", "lat": 44.4268, "lon": 26.1025, "radius_m": 100}


def login(client, email):
    assert (
        client.post("/auth/login", json={"email": email, "password": PW}).status_code
        == 200
    )


def setup(client):
    login(client, "boss@example.com")
    site = client.post("/api/sites", json=SITE).json()
    client.put(
        "/api/employees/1",
        json={
            "name": "Ana",
            "role": "employee",
            "norma_h": 8,
            "active": True,
            "site_ids": [site["id"]],
        },
    )
    svg = client.get(f"/api/sites/{site['id']}/qr.svg")
    client.post("/auth/logout")
    login(client, "ana@example.com")
    return site, svg


def test_qr_svg_encodes_scan_url_and_is_admin_only(client):
    site, svg = setup(client)
    assert svg.status_code == 200 and "<svg" in svg.text
    assert client.get("/p/abc/in").status_code == 404  # per-kind codes are gone
    assert client.get(f"/api/sites/{site['id']}/qr.svg").status_code == 403


def test_resolve_key_only_for_assigned_site(client):
    site, _ = setup(client)
    assert client.get(f"/api/punch/site/{site['qr_key']}").json()["id"] == site["id"]
    assert client.get("/api/punch/site/nope").status_code == 404


def test_explicit_kinds(client):
    site, _ = setup(client)
    body = {"site_id": site["id"], **HERE}
    assert (
        client.post("/api/punch", json={**body, "kind": "out"}).status_code == 409
    )  # nothing open
    assert client.post("/api/punch", json={**body, "kind": "in"}).status_code == 201
    assert (
        client.post("/api/punch", json={**body, "kind": "in"}).status_code == 409
    )  # duplicate scan
    assert client.post("/api/punch", json={**body, "kind": "out"}).status_code == 201


def test_in_after_forgotten_out_blocked_until_departure_declared(client):
    site, _ = setup(client)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
            "VALUES (1, ?, 'in', '2020-01-01T06:00:00Z', 44.4, 26.1, 5, 1)",
            (site["id"],),
        )
    r = client.post("/api/punch", json={"site_id": site["id"], "kind": "in", **HERE})
    assert r.status_code == 409 and "was not closed" in r.json()["detail"]
    req = {"site_id": site["id"], "kind": "out", "local_time": "2020-01-01T16:00"}
    assert (
        client.post("/api/requests", json={**req, "note": "forgot"}).status_code == 201
    )
    r = client.post("/api/punch", json={"site_id": site["id"], "kind": "in", **HERE})
    assert r.status_code == 201


def test_regenerate_key_invalidates_old(client):
    login(client, "boss@example.com")
    site = client.post("/api/sites", json=SITE).json()
    new = client.post(f"/api/sites/{site['id']}/regenerate-key").json()
    assert new["qr_key"] != site["qr_key"]


def test_pages(client):
    for path in ("/p/abc", "/admin/qr"):
        assert client.get(path).status_code == 200
    # a QR scan opens the full clock page (forgotten-shift and late-arrival flows included)
    assert "I forgot to clock in on arrival" in client.get("/p/abc").text


def test_bootstrap_admin_only_on_empty_db(client, monkeypatch):
    from punctual.cli import bootstrap_admin
    from punctual.settings import settings

    monkeypatch.setattr(settings, "bootstrap_admin_email", "first@example.com")
    monkeypatch.setattr(settings, "bootstrap_admin_password", PW)
    assert bootstrap_admin() is False  # client fixture already created employees
    with db.connect() as conn:
        conn.execute("DELETE FROM employees")
    assert bootstrap_admin() is True
    assert (
        client.post(
            "/auth/login", json={"email": "first@example.com", "password": PW}
        ).json()["role"]
        == "admin"
    )


def test_in_while_clocked_in_is_refused_with_hint(client, cutoff_far):
    site, _ = setup(client)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
            "VALUES (1, ?, 'in', strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-3 hours'), 44.4, 26.1, 5, 1)",
            (site["id"],),
        )
    r = client.post("/api/punch", json={"site_id": site["id"], "kind": "in", **HERE})
    assert r.status_code == 409 and "choose Departure" in r.json()["detail"]
    assert (
        client.post(
            "/api/punch", json={"site_id": site["id"], "kind": "out", **HERE}
        ).status_code
        == 201
    )


def test_out_without_open_shift_hints_arrival(client):
    site, _ = setup(client)
    r = client.post("/api/punch", json={"site_id": site["id"], "kind": "out", **HERE})
    assert r.status_code == 409 and "choose Arrival" in r.json()["detail"]


def test_qr_encodes_single_url_without_kind(client, monkeypatch):
    from punctual.settings import settings

    monkeypatch.setattr(settings, "public_url", "https://p.example.com")
    login(client, "boss@example.com")
    site = client.post("/api/sites", json=SITE).json()
    import io

    import segno

    expected = segno.make(f"https://p.example.com/p/{site['qr_key']}", error="m")
    buf = io.BytesIO()
    expected.save(
        buf,
        kind="svg",
        scale=8,
        border=4,
        dark="#000000",
        light="#ffffff",
        xmldecl=False,
    )
    got = client.get(f"/api/sites/{site['id']}/qr.svg").content
    assert got == buf.getvalue()


def test_manual_pages(client):
    for path in ("/help", "/admin/help", "/register"):
        assert client.get(path).status_code == 200


def test_html_pages_revalidate(client):
    assert client.get("/admin/employees").headers["cache-control"] == "no-cache"
