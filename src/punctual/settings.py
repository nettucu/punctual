"""Application settings (env vars prefixed PUNCTUAL_)."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Attributes:
        db_path: SQLite file path.
        locale: UI and message language, `en` or `ro` (catalogs in `punctual/locales`).
        timezone: IANA zone for every displayed and typed time (storage is always UTC).
        session_ttl_minutes: Session lifetime, fixed from login (not extended by activity).
        max_failed_logins: Failures before temporary lockout.
        lockout_minutes: Lockout duration.
        geofence_mode: `flag` records punches outside the zone or with poor GPS and flags them for review; `block` rejects them.
        geofence_max_accuracy_m: A fix less accurate than this is rejected (0, the default, disables); fixes over 100 m are always flagged.
        geofence_block_over_m: In `flag` mode, punches farther than this from the site are rejected (0, the default, disables: they are recorded and flagged).
        attempts_retention_days: Rejected punch attempts are deleted after this many days (minimum 30).
        forgotten_cutoff_hour: Local hour after which a still-open shift counts as a forgotten
            clock-out; the employee is blocked until they declare the departure time.
        late_in_hour: From this local hour on, the employee UI asks whether an arrival is really happening now.
        early_out_minutes: A departure sooner than this after the arrival is refused (0 disables); the employee
            must ask an admin to void the arrival from Corrections.
        min_break_minutes: An arrival sooner than this after a departure is refused (0 disables); the employee
            declares the real arrival time as a correction request instead.
        max_punches_per_day: Most punches (voided ones excluded) one employee may record per local day (0 disables).
        long_shift_h: Closed shifts longer than this get a `long shift` anomaly.
        public_url: Base URL encoded in QR codes (e.g. https://punctual.example.com); falls back to the request URL.
        bootstrap_admin_email: With the password, creates the first admin on startup when no employees exist.
        bootstrap_admin_password: See bootstrap_admin_email (supply from Secret Manager, then remove).
        dev_port: Port of the local dev server (`uv run punctual`); production uses `$PORT` in the entrypoint.
        registration_link_hours: Lifetime of the shared self-registration link.
        cookie_secure: Set the Secure flag on the session cookie (disable only for local http dev).
    """

    model_config = SettingsConfigDict(
        env_prefix="PUNCTUAL_", env_file=".env", extra="ignore"
    )

    db_path: str = "punctual.db"
    locale: Literal["en", "ro"] = "en"
    timezone: str = "UTC"
    session_ttl_minutes: int = 60
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    cookie_secure: bool = True
    registration_link_hours: int = 6
    dev_port: int = 8000
    public_url: str = ""
    geofence_mode: Literal["flag", "block"] = "flag"
    geofence_block_over_m: float = 0.0
    geofence_max_accuracy_m: float = 0.0
    attempts_retention_days: int = 90
    forgotten_cutoff_hour: int = 22
    late_in_hour: int = 14
    early_out_minutes: int = 5
    min_break_minutes: int = 15
    max_punches_per_day: int = 6
    long_shift_h: float = 12.0
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""


settings: Settings = Settings()
