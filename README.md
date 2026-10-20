# Punctual

Geofenced employee time clock. Employees scan a QR code at their workplace and clock in or out from their phone; the punch is accepted only near the site (or recorded and flagged for review). Admins correct records, approve leave and export hour reports as CSV. FastAPI + SQLite, with continuous backup through Litestream.

Features: sites with a QR code and a radius, punch flagging and review, correction and leave requests with an admin approval workflow, append-only audit trail, per-employee reports with public holidays, self-registration link.

## Run locally

    uv sync
    PUNCTUAL_COOKIE_SECURE=false uv run punctual      # http://localhost:8000

Create the first admin with `PUNCTUAL_BOOTSTRAP_ADMIN_EMAIL` / `PUNCTUAL_BOOTSTRAP_ADMIN_PASSWORD` (used only while no employees exist), or with `uv run punctual-admin create-admin --name Ana --email ana@example.com` (prompts for the password). All settings are `PUNCTUAL_*` variables, see [`.env.example`](.env.example) and `src/punctual/settings.py`.

## Deploy

Docker Compose on any VPS (HTTPS included) or Google Cloud Run: see [`deploy/README.md`](deploy/README.md).

## Tests

    uv run pytest                 # unit + API scenarios + browser E2E
    uv run pytest tests/e2e       # only the Playwright suite (live server per test, emulated GPS)

E2E uses an installed Chromium (`~/.cache/ms-playwright/chromium-*`, or `E2E_CHROME=/path/to/chrome`); else `uv run playwright install chromium`.
Per-test server settings: `@pytest.mark.server_env(late_in_hour=0, geofence_mode="block")`.

## License

MIT, see [LICENSE](LICENSE).
