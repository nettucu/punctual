import sqlite3

from punctual import db
from punctual.attempts import MAX_ATTEMPTS_PER_DAY, purge_old, record_attempt


def test_old_leave_table_is_rebuilt_with_its_rows(client, tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        db.SCHEMA.replace("'zl', 'cfp'", "'zl'")
    )  # the schema before `cfp` existed
    old.execute(
        "INSERT INTO employees (name, email, password_hash) VALUES ('A', 'a@x.ro', 'h')"
    )
    old.execute(
        "INSERT INTO leave_requests (employee_id, kind, date_from, date_to) VALUES (1, 'cm', '2026-01-05', '2026-01-06')"
    )
    old.commit()
    old.close()
    db.init_db(path)
    with db.connect(path) as conn:
        assert [
            tuple(r) for r in conn.execute("SELECT kind, date_from FROM leave_requests")
        ] == [("cm", "2026-01-05")]
        conn.execute(
            "INSERT INTO leave_requests (employee_id, kind, date_from, date_to) VALUES (1, 'cfp', '2026-02-02', '2026-02-02')"
        )


def test_attempts_are_capped_per_employee_and_expired_sessions_purged(client):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sites (name, lat, lon, radius_m) VALUES ('S', 45, 25, 100)"
        )
        conn.execute(
            "INSERT INTO sessions (token_hash, employee_id, expires_at) VALUES ('old', 1, '2020-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO sessions (token_hash, employee_id, expires_at) VALUES ('live', 1, '2999-01-01T00:00:00Z')"
        )
    for _ in range(MAX_ATTEMPTS_PER_DAY + 5):
        record_attempt(1, 1, "in", 0, 0, 0, "geo_denied")
    purge_old()
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM punch_attempts WHERE employee_id = 1"
            ).fetchone()[0]
            == MAX_ATTEMPTS_PER_DAY
        )
        assert [r[0] for r in conn.execute("SELECT token_hash FROM sessions")] == [
            "live"
        ]
