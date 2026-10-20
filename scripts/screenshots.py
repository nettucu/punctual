"""Regenerate docs/screenshots from a throwaway demo instance: `uv run python scripts/screenshots.py`.

Needs Chromium: the one Playwright installs (`uv run playwright install chromium`), or set E2E_CHROME=/path/to/chrome.
"""

import glob
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs" / "screenshots"
LOGO = ROOT / "src" / "punctual" / "static" / "logo.svg"
PASSWORD = "Demo-Password-1!"
MAIN_CLINIC = {"latitude": 44.4268, "longitude": 26.1025, "accuracy": 10}
ADMIN_PAGES = [
    ("dashboard", "/admin"),
    ("reports", "/admin/reports"),
    ("corrections", "/admin/corrections"),
]


def chromium() -> str | None:
    found = os.environ.get("E2E_CHROME") or next(
        iter(
            sorted(
                glob.glob(
                    os.path.expanduser(
                        "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"
                    )
                )
            )
        ),
        None,
    )
    return found


def main() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {
        **os.environ,
        "PUNCTUAL_DB_PATH": str(Path(tempfile.mkdtemp()) / "demo.db"),
        "PUNCTUAL_COOKIE_SECURE": "false",
        "PUNCTUAL_LOCALE": "en",
        "PUNCTUAL_TIMEZONE": "UTC",
    }
    subprocess.run(["punctual-admin", "seed-demo"], env=env, check=True)
    server = subprocess.Popen(
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
        env=env,
        cwd=ROOT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        OUT.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chromium())

            def shot(
                email: str, size: dict, shots: list[tuple[str, str]], **ctx
            ) -> None:
                page = browser.new_context(
                    viewport=size, timezone_id="UTC", **ctx
                ).new_page()
                # Always show the bundled logo, even where a private static/custom/logo.* exists.
                page.route(
                    "**/brand/logo",
                    lambda r: r.fulfill(path=str(LOGO), content_type="image/svg+xml"),
                )
                page.goto(f"{url}/login")
                page.fill("#e", email)
                page.fill("#p", PASSWORD)
                page.click("#b")
                page.wait_for_url(lambda u: "/login" not in u)
                for name, path in shots:
                    page.goto(url + path)
                    page.wait_for_load_state("networkidle")
                    page.screenshot(path=str(OUT / f"{name}.png"))

            shot("ana@example.com", {"width": 390, "height": 760}, [("clock", "/")],
                 geolocation=MAIN_CLINIC, permissions=["geolocation"], device_scale_factor=2)  # fmt: skip
            shot("admin@example.com", {"width": 1280, "height": 800}, ADMIN_PAGES)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
