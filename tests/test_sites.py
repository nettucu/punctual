from punctual import db
from punctual.cli import create_admin

PW = "Correct-Horse-9-battery"
BODY = {"name": "Main Clinic", "lat": 44.4, "lon": 26.1, "radius_m": 100}


def as_admin(client):
    client.post("/auth/login", json={"email": "boss@example.com", "password": PW})


def test_employee_forbidden(client):
    client.post("/auth/login", json={"email": "ana@example.com", "password": PW})
    assert client.get("/api/sites").status_code == 403
    assert client.post("/api/sites", json=BODY).status_code == 403


def test_anonymous_unauthorized(client):
    assert client.get("/api/sites").status_code == 401


def test_crud(client):
    as_admin(client)
    sid = client.post("/api/sites", json=BODY).json()["id"]
    assert client.post("/api/sites", json=BODY).status_code == 409
    assert (
        client.put(f"/api/sites/{sid}", json={**BODY, "radius_m": 50}).json()[
            "radius_m"
        ]
        == 50
    )
    assert len(client.get("/api/sites").json()) == 1
    assert client.delete(f"/api/sites/{sid}").status_code == 204
    assert client.delete(f"/api/sites/{sid}").status_code == 404


def test_validation(client):
    as_admin(client)
    assert client.post("/api/sites", json={**BODY, "lat": 200}).status_code == 422


def test_cannot_delete_site_with_punches(client):
    as_admin(client)
    sid = client.post("/api/sites", json=BODY).json()["id"]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, lat, lon, accuracy_m, inside) VALUES (1,?,'in',44.4,26.1,5,1)",
            (sid,),
        )
    assert client.delete(f"/api/sites/{sid}").status_code == 409


def test_cross_origin_post_rejected(client):
    as_admin(client)
    r = client.post("/api/sites", json=BODY, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_create_admin_cli(client):
    create_admin("New", "new@example.com", PW)
    assert (
        client.post(
            "/auth/login", json={"email": "new@example.com", "password": PW}
        ).json()["role"]
        == "admin"
    )


def test_pages(client):
    assert client.get("/login").status_code == 200
    assert client.get("/admin/sites").status_code == 200
