import sqlite3

import pytest

from punctual import db
from punctual.settings import settings

PW = "Correct-Horse-9-battery"


def login(client, email="ana@example.com", pw=PW):
    return client.post("/auth/login", json={"email": email, "password": pw})


def test_login_and_me(client):
    assert login(client).status_code == 200
    assert client.get("/auth/me").json()["email"] == "ana@example.com"


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401


def test_bad_password_and_unknown_email_same_response(client):
    a, b = (
        login(client, pw="wrong-wrong-wrong"),
        login(client, email="nobody@example.com"),
    )
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_failures(client):
    for _ in range(settings.max_failed_logins):
        login(client, pw="wrong-wrong-wrong")
    assert login(client).status_code == 401  # correct password, but locked


def test_logout_invalidates_session(client):
    login(client)
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


def test_inactive_user_denied(client):
    with db.connect() as conn:
        conn.execute("UPDATE employees SET active = 0 WHERE email = 'ana@example.com'")
    assert login(client).status_code == 401


def test_punches_append_only(client):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sites (name, lat, lon, radius_m) VALUES ('s', 44.4, 26.1, 100)"
        )
        conn.execute(
            "INSERT INTO punches (employee_id, site_id, kind, lat, lon, accuracy_m, inside) VALUES (1,1,'in',44.4,26.1,5,1)"
        )
    for sql in ("UPDATE punches SET kind='out'", "DELETE FROM punches"):
        with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
            conn.execute(sql)


def test_session_lasts_one_hour_and_expires(client):
    r = login(client)
    assert "Max-Age=3600" in r.headers["set-cookie"]
    assert client.get("/auth/me").status_code == 200
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00Z'")
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "pw",
    [
        "Short-1!",
        "correct-horse-9-battery",
        "Correct-Horse-battery",
        "CorrectHorse9Battery",
    ],
)
def test_weak_passwords_rejected(client, pw):
    from punctual.security import check_password_policy

    with pytest.raises(ValueError):
        check_password_policy(pw)


def _link_token(client) -> str:
    login(client, "boss@example.com")
    path = client.post("/api/registration-link").json()["path"]
    client.post("/auth/logout")
    return path.split("t=")[1]


def test_register_via_link_creates_inactive_account(client):
    t = _link_token(client)
    body = {"token": t, "name": "Nou", "email": "nou@example.com", "password": PW}
    assert client.post("/auth/register", json=body).status_code == 201
    assert (
        login(client, "nou@example.com").status_code == 401
    )  # inactive until admin activates
    assert client.post("/auth/register", json=body).status_code == 409


def test_register_rejects_bad_expired_or_replaced_link_and_weak_password(client):
    t = _link_token(client)
    ok = {"name": "Nou", "email": "nou@example.com", "password": PW}
    assert (
        client.post("/auth/register", json={**ok, "token": "nope"}).status_code == 410
    )
    assert (
        client.post(
            "/auth/register", json={**ok, "token": t, "password": "weakweakweak"}
        ).status_code
        == 422
    )
    with db.connect() as conn:
        conn.execute("UPDATE registration_link SET expires_at = '2000-01-01T00:00:00Z'")
    assert client.post("/auth/register", json={**ok, "token": t}).status_code == 410
    t2 = _link_token(client)
    t3 = _link_token(client)
    assert client.post("/auth/register", json={**ok, "token": t2}).status_code == 410
    assert client.post("/auth/register", json={**ok, "token": t3}).status_code == 201


def test_registration_link_admin_only(client):
    login(client)  # Ana (employee) per helper default
    assert client.post("/api/registration-link").status_code == 403


def test_register_rejects_duplicate_name_and_caps_pending(client, monkeypatch):
    from punctual import registration

    t = _link_token(client)
    ok = {"token": t, "name": "Nou", "email": "nou@example.com", "password": PW}
    assert client.post("/auth/register", json=ok).status_code == 201
    assert (
        client.post(
            "/auth/register", json={**ok, "name": "nou", "email": "alt@example.com"}
        ).status_code
        == 409
    )
    assert (
        client.post("/auth/register", json={**ok, "name": "ana"}).status_code == 409
    )  # existing employee
    monkeypatch.setattr(registration, "MAX_PENDING_ACCOUNTS", 1)
    assert (
        client.post(
            "/auth/register", json={**ok, "name": "Altul", "email": "z@example.com"}
        ).status_code
        == 429
    )


def test_pending_accounts_flagged_and_counted_for_admin(client):
    t = _link_token(client)
    client.post(
        "/auth/register",
        json={"token": t, "name": "Nou", "email": "nou@example.com", "password": PW},
    )
    login(client, "boss@example.com")
    assert client.get("/api/admin/pending-counts").json()["accounts"] == 1
    flags = {e["email"]: e["pending"] for e in client.get("/api/employees").json()}
    assert flags == {
        "ana@example.com": False,
        "boss@example.com": False,
        "nou@example.com": True,
    }


def test_admin_create_rejects_weak_password(client):
    login(client, "boss@example.com")
    r = client.post(
        "/api/employees",
        json={"name": "X", "email": "x@example.com", "password": "alllowercase12"},
    )
    assert r.status_code == 422
