"""SQLite connection and schema."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from punctual.settings import settings

logger: logging.Logger = logging.getLogger(__name__)

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'employee')) DEFAULT 'employee',
    norma_h INTEGER NOT NULL DEFAULT 8 CHECK (norma_h IN (2, 4, 6, 8)),
    active INTEGER NOT NULL DEFAULT 1,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    lat REAL NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon REAL NOT NULL CHECK (lon BETWEEN -180 AND 180),
    radius_m REAL NOT NULL CHECK (radius_m > 0),
    qr_key TEXT
);

CREATE TABLE IF NOT EXISTS employee_sites (
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    site_id INTEGER NOT NULL REFERENCES sites(id),
    PRIMARY KEY (employee_id, site_id)
);

-- At most one shared self-registration link (id is always 1); issuing a new one replaces it.
CREATE TABLE IF NOT EXISTS registration_link (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES employees(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    expires_at TEXT NOT NULL
);

-- Append-only: ITM audit trail. Corrections are new rows (kind + corrects_id), never edits. An admin "edit" voids
-- the original and inserts its replacement with corrects_id pointing at it, in one transaction.
CREATE TABLE IF NOT EXISTS punches (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    site_id INTEGER NOT NULL REFERENCES sites(id),
    kind TEXT NOT NULL CHECK (kind IN ('in', 'out')),
    ts_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    accuracy_m REAL NOT NULL,
    inside INTEGER NOT NULL,
    corrects_id INTEGER REFERENCES punches(id),
    note TEXT,
    distance_m REAL,
    source TEXT NOT NULL DEFAULT 'device' CHECK (source IN ('device', 'admin')),
    created_by INTEGER REFERENCES employees(id),
    created_at TEXT -- when an admin entered the row (device rows: ts_utc is the entry time)
);
CREATE INDEX IF NOT EXISTS idx_punches_emp_ts ON punches(employee_id, ts_utc);

-- Employee-submitted correction requests. Status is workflow state (pending -> approved/rejected, final);
-- approval creates an append-only admin punch, which is the audit record.
CREATE TABLE IF NOT EXISTS correction_requests (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    site_id INTEGER NOT NULL REFERENCES sites(id),
    kind TEXT NOT NULL CHECK (kind IN ('in', 'out')),
    ts_utc TEXT NOT NULL,
    note TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_by INTEGER REFERENCES employees(id),
    decided_at TEXT,
    decision_note TEXT,
    punch_id INTEGER REFERENCES punches(id)
);
CREATE INDEX IF NOT EXISTS idx_requests_status ON correction_requests(status, created_at);
CREATE TRIGGER IF NOT EXISTS requests_decided_final BEFORE UPDATE ON correction_requests
WHEN OLD.status <> 'pending'
BEGIN SELECT RAISE(ABORT, 'decided requests are final'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_delete BEFORE DELETE ON correction_requests
BEGIN SELECT RAISE(ABORT, 'requests are append-only'); END;

-- Vacation (co), medical leave (cm), free-day (zl) and unpaid-leave (cfp) requests, same workflow as correction requests: decisions are final, rows are
-- never deleted. Dates are local days, inclusive.
CREATE TABLE IF NOT EXISTS leave_requests (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    kind TEXT NOT NULL CHECK (kind IN ('co', 'cm', 'zl', 'cfp')),
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL CHECK (date_to >= date_from),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_by INTEGER REFERENCES employees(id),
    decided_at TEXT,
    decision_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_leave_emp ON leave_requests(employee_id, date_from);
CREATE TRIGGER IF NOT EXISTS leave_decided_final BEFORE UPDATE ON leave_requests
WHEN OLD.status <> 'pending'
BEGIN SELECT RAISE(ABORT, 'decided leave requests are final'); END;
CREATE TRIGGER IF NOT EXISTS leave_no_delete BEFORE DELETE ON leave_requests
BEGIN SELECT RAISE(ABORT, 'leave requests are append-only'); END;

-- Rejected punch attempts (too far, no open shift, ...). Append-only; rows younger than 30 days cannot be
-- deleted; the app purges rows past the retention period (default 90 days, see settings).
CREATE TABLE IF NOT EXISTS punch_attempts (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    site_id INTEGER REFERENCES sites(id),
    kind TEXT NOT NULL,
    ts_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    accuracy_m REAL NOT NULL,
    distance_m REAL,
    reason TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_ts ON punch_attempts(ts_utc);
CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON punch_attempts
BEGIN SELECT RAISE(ABORT, 'punch_attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS attempts_min_age_delete BEFORE DELETE ON punch_attempts
WHEN OLD.ts_utc > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days')
BEGIN SELECT RAISE(ABORT, 'punch_attempts younger than 30 days cannot be deleted'); END;

-- An admin can approve a flagged location (kept, append-only, excluded from flags).
CREATE TABLE IF NOT EXISTS punch_approvals (
    punch_id INTEGER PRIMARY KEY REFERENCES punches(id),
    approved_by INTEGER NOT NULL REFERENCES employees(id),
    reason TEXT NOT NULL,
    approved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TRIGGER IF NOT EXISTS punch_approvals_no_update BEFORE UPDATE ON punch_approvals
BEGIN SELECT RAISE(ABORT, 'punch_approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS punch_approvals_no_delete BEFORE DELETE ON punch_approvals
BEGIN SELECT RAISE(ABORT, 'punch_approvals are append-only'); END;

-- A wrong punch is never edited or deleted; an admin voids it here (also append-only).
CREATE TABLE IF NOT EXISTS punch_voids (
    punch_id INTEGER PRIMARY KEY REFERENCES punches(id),
    voided_by INTEGER NOT NULL REFERENCES employees(id),
    reason TEXT NOT NULL,
    voided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TRIGGER IF NOT EXISTS punch_voids_no_update BEFORE UPDATE ON punch_voids
BEGIN SELECT RAISE(ABORT, 'punch_voids are append-only'); END;
CREATE TRIGGER IF NOT EXISTS punch_voids_no_delete BEFORE DELETE ON punch_voids
BEGIN SELECT RAISE(ABORT, 'punch_voids are append-only'); END;

-- An admin marks a flagged day as checked. `notes` is the comma-joined set of anomalies/flags that were reviewed;
-- the day shows up again if a different one appears later. Append-only.
CREATE TABLE IF NOT EXISTS day_reviews (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    day TEXT NOT NULL,
    notes TEXT NOT NULL,
    reason TEXT NOT NULL,
    reviewed_by INTEGER NOT NULL REFERENCES employees(id),
    reviewed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_day_reviews_emp_day ON day_reviews(employee_id, day);
CREATE TRIGGER IF NOT EXISTS day_reviews_no_update BEFORE UPDATE ON day_reviews
BEGIN SELECT RAISE(ABORT, 'day_reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS day_reviews_no_delete BEFORE DELETE ON day_reviews
BEGIN SELECT RAISE(ABORT, 'day_reviews are append-only'); END;

CREATE TRIGGER IF NOT EXISTS punches_no_update BEFORE UPDATE ON punches
BEGIN SELECT RAISE(ABORT, 'punches are append-only'); END;
CREATE TRIGGER IF NOT EXISTS punches_no_delete BEFORE DELETE ON punches
BEGIN SELECT RAISE(ABORT, 'punches are append-only'); END;
"""


def init_db(path: str | None = None) -> None:
    """Create tables if missing.

    Args:
        path: Optional DB path override.
    """
    with connect(path) as conn:
        old = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'leave_requests'"
        ).fetchone()
        rebuild_leave: bool = old is not None and "'cfp'" not in old["sql"]
        if rebuild_leave:  # CHECK constraints cannot be altered: swap in a table that allows 'cfp', all in one transaction
            conn.executescript(
                "BEGIN; DROP TRIGGER IF EXISTS leave_decided_final; DROP TRIGGER IF EXISTS leave_no_delete; "
                "DROP INDEX IF EXISTS idx_leave_emp; ALTER TABLE leave_requests RENAME TO leave_requests_old;"
                + SCHEMA
                + "INSERT INTO leave_requests SELECT * FROM leave_requests_old; DROP TABLE leave_requests_old; COMMIT;"
            )
        else:
            conn.executescript(SCHEMA)
        site_cols: set[str] = {
            r["name"] for r in conn.execute("PRAGMA table_info(sites)")
        }
        if "qr_key" not in site_cols:
            conn.execute("ALTER TABLE sites ADD COLUMN qr_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sites_qr_key ON sites(qr_key)"
            )
        for r in conn.execute("SELECT id FROM sites WHERE qr_key IS NULL").fetchall():
            conn.execute(
                "UPDATE sites SET qr_key = ? WHERE id = ?",
                (secrets.token_urlsafe(9), r["id"]),
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sites_qr_key ON sites(qr_key)"
        )
        emp_cols: set[str] = {
            r["name"] for r in conn.execute("PRAGMA table_info(employees)")
        }
        if "norma_h" not in emp_cols:
            conn.execute(
                "ALTER TABLE employees ADD COLUMN norma_h INTEGER NOT NULL DEFAULT 8"
            )
        cols: set[str] = {r["name"] for r in conn.execute("PRAGMA table_info(punches)")}
        if "distance_m" not in cols:
            conn.execute("ALTER TABLE punches ADD COLUMN distance_m REAL")
        if "source" not in cols:  # DBs created before corrections existed
            conn.execute(
                "ALTER TABLE punches ADD COLUMN source TEXT NOT NULL DEFAULT 'device'"
            )
            conn.execute(
                "ALTER TABLE punches ADD COLUMN created_by INTEGER REFERENCES employees(id)"
            )
        if "created_at" not in cols:
            conn.execute("ALTER TABLE punches ADD COLUMN created_at TEXT")
        if "detail" not in {
            r["name"] for r in conn.execute("PRAGMA table_info(punch_attempts)")
        }:
            conn.execute(
                "ALTER TABLE punch_attempts ADD COLUMN detail TEXT"
            )  # UPDATE trigger only blocks UPDATEs
        # The one definition of a punch that counts: not voided. Read this instead of repeating the rule.
        conn.execute(
            "CREATE VIEW IF NOT EXISTS live_punches AS "
            "SELECT * FROM punches WHERE id NOT IN (SELECT punch_id FROM punch_voids)"
        )
    logger.debug(f"schema ready at {path or settings.db_path}")


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, always close.

    Args:
        path: Optional DB path override.

    Yields:
        Connection with row access by column name and foreign keys on.
    """
    conn: sqlite3.Connection = sqlite3.connect(path or settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
