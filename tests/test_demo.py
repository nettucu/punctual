import pytest
from fastapi.testclient import TestClient

from punctual import db
from punctual.demo import ADMIN_EMAIL, DEMO_PASSWORD, seed_demo
from punctual.settings import settings


@pytest.fixture()
def demo_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "demo.db"))
    monkeypatch.setattr(settings, "cookie_secure", False)
    seed_demo()
    from punctual.main import app

    c = TestClient(app)
    r = c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": DEMO_PASSWORD})
    assert r.status_code == 200
    return c


def test_demo_data_feeds_every_admin_view(demo_client):
    o = demo_client.get("/api/admin/overview").json()
    assert o["employees"] >= 4 and o["sites"] == 2 and o["flagged_days"]
    assert len(demo_client.get("/api/employees").json()) == 5
    assert demo_client.get("/api/requests").json()  # a pending correction request
    assert demo_client.get("/api/leave?status=pending").json()
    assert demo_client.get("/api/attempts?from=2000-01-01&to=2100-01-01").json()


def test_demo_refuses_a_database_that_has_data(demo_client):
    with pytest.raises(ValueError, match="not empty"):
        seed_demo()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] > 50
