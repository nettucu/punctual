import os

# Hermetic: a developer .env must not change what the tests see.
os.environ["PUNCTUAL_TIMEZONE"] = (
    "Europe/Bucharest"  # tests assert Bucharest wall times
)
os.environ["PUNCTUAL_LOCALE"] = "en"

import pytest
from fastapi.testclient import TestClient

from punctual import db
from punctual.security import hash_password
from punctual.settings import settings


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(settings, "cookie_secure", False)
    for guard in ("early_out_minutes", "min_break_minutes", "max_punches_per_day"):
        monkeypatch.setattr(
            settings, guard, 0
        )  # tests tap fast; guardrail tests opt in
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO employees (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (
                "Ana",
                "ana@example.com",
                hash_password("Correct-Horse-9-battery"),
                "employee",
            ),
        )
        conn.execute(
            "INSERT INTO employees (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (
                "Boss",
                "boss@example.com",
                hash_password("Correct-Horse-9-battery"),
                "admin",
            ),
        )
    from punctual.main import app

    return TestClient(app)


@pytest.fixture()
def cutoff_far(monkeypatch) -> None:
    """Move the forgotten-shift cutoff 12 hours away from now, so tests that clock in "a few hours ago" don't
    turn into forgotten shifts when run late in the evening."""
    from datetime import datetime

    from punctual.clock import TZ

    monkeypatch.setattr(
        settings, "forgotten_cutoff_hour", (datetime.now(TZ).hour + 12) % 24
    )


@pytest.fixture()
def hard_geofence_limits(monkeypatch) -> None:
    """Opt back in to the optional hard limits (off by default: far or coarse punches are recorded and flagged)."""
    monkeypatch.setattr(settings, "geofence_block_over_m", 1000.0)
    monkeypatch.setattr(settings, "geofence_max_accuracy_m", 150.0)
