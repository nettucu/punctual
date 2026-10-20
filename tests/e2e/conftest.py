"""Real-browser E2E: a live uvicorn on a throwaway DB, driven by Playwright (Chromium, geolocation emulated)."""

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from helpers import AT_SITE, PW, SITE_LAT, SITE_LON

from punctual import db
from punctual.security import hash_password
from punctual.settings import settings


@dataclass
class Live:
    url: str
    db_path: str
    site_key: str
    site_id: int

    def sql(self, query: str, *args: object) -> None:
        """Write straight into the live DB (seed history the UI cannot create)."""
        con = db.sqlite3.connect(self.db_path)
        with con:
            con.execute(query, args)
        con.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live(tmp_path, monkeypatch, request) -> Iterator[Live]:
    """Fresh server + DB per test. Override env with `@pytest.mark.server_env(KEY=value)`."""
    path = str(tmp_path / "e2e.db")
    monkeypatch.setattr(settings, "db_path", path)
    db.init_db()
    with db.connect() as conn:
        for name, email, role in (
            ("Ana", "ana@example.com", "employee"),
            ("Boss", "boss@example.com", "admin"),
        ):
            conn.execute(
                "INSERT INTO employees (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                (name, email, hash_password(PW), role),
            )
        site_id = conn.execute(
            "INSERT INTO sites (name, lat, lon, radius_m, qr_key) VALUES ('Main Clinic', ?, ?, 100, 'qr-main')",
            (SITE_LAT, SITE_LON),
        ).lastrowid
        ana = conn.execute(
            "SELECT id FROM employees WHERE email = 'ana@example.com'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO employee_sites (employee_id, site_id) VALUES (?, ?)",
            (ana, site_id),
        )
    port = _free_port()
    env_marker = request.node.get_closest_marker("server_env")
    # Defaults keep the time-of-day rules out of the way; tests opt in with the marker.
    from datetime import datetime

    from punctual.clock import TZ

    wanted = {
        "late_in_hour": 24,
        "forgotten_cutoff_hour": (datetime.now(TZ).hour + 12) % 24,
    }
    wanted |= env_marker.kwargs if env_marker else {}
    extra = {f"PUNCTUAL_{k.upper()}": str(v) for k, v in wanted.items()}
    import os

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "punctual.main:app",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env={
            **os.environ,
            "PUNCTUAL_DB_PATH": path,
            "PUNCTUAL_COOKIE_SECURE": "false",
            **extra,
        },
        cwd=Path(__file__).parents[2],
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/healthz").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("server did not start")
    yield Live(url, path, "qr-main", site_id)
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture()
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "geolocation": AT_SITE,
        "permissions": ["geolocation"],
        "locale": "ro-RO",
    }


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Reuse a Chromium already on disk when the pinned Playwright's own build is not installed."""
    import glob
    import os

    found = os.environ.get("E2E_CHROME") or next(
        iter(
            sorted(
                glob.glob(
                    os.path.expanduser(
                        "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"
                    )
                )
            )
        ),
        None,
    )
    return {**browser_type_launch_args, **({"executable_path": found} if found else {})}
