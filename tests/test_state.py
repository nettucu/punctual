from datetime import UTC, datetime

from punctual import db
from punctual.clock import cutoff_after
from punctual.state import all_states, effective_state


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_cutoff_is_next_22_local():
    # winter: EET = UTC+2, 22:00 local = 20:00Z
    assert cutoff_after(utc(2026, 3, 10, 6)) == utc(2026, 3, 10, 20)
    # arrival after 22:00 local -> 22:00 the next day
    assert cutoff_after(utc(2026, 3, 10, 20, 30)) == utc(2026, 3, 11, 20)
    # DST starts 2026-03-29 03:00 local: the cutoff that evening is EEST (UTC+3)
    assert cutoff_after(utc(2026, 3, 28, 21)) == utc(2026, 3, 29, 19)


def _punch(conn, kind, ts, site=1):
    return conn.execute(
        "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
        "VALUES (1, ?, ?, ?, 44.4, 26.1, 5, 1)",
        (site, kind, ts),
    ).lastrowid


def _request(conn, kind, ts, status="pending", note=None):
    conn.execute(
        "INSERT INTO correction_requests (employee_id, site_id, kind, ts_utc, note) VALUES (1, 1, ?, ?, 'x')",
        (kind, ts),
    )
    if status != "pending":
        conn.execute(
            "UPDATE correction_requests SET status = ?, decided_by = 2, decided_at = ?, decision_note = ? "
            "WHERE id = last_insert_rowid()",
            (status, ts, note),
        )


def _site(conn):
    conn.execute(
        "INSERT INTO sites (id, name, lat, lon, radius_m) VALUES (1, 'S', 44.4, 26.1, 100)"
    )


def test_effective_state_transitions(client):
    noon = utc(2026, 3, 10, 10)
    night = utc(2026, 3, 10, 21)
    with db.connect() as conn:
        _site(conn)
        assert effective_state(conn, 1, noon).state == "closed"
        _punch(conn, "in", "2026-03-10T06:00:00Z")
        st = effective_state(conn, 1, noon)
        assert st.state == "open" and st.open_since == utc(2026, 3, 10, 6)
        assert st.open_site_id == 1
        assert effective_state(conn, 1, night).state == "forgotten"
        _request(conn, "out", "2026-03-10T15:00:00Z", "rejected", "nu e adevarat")
        st = effective_state(conn, 1, night)
        assert st.state == "forgotten" and st.rejected_note == "nu e adevarat"
        _request(conn, "out", "2026-03-10T15:30:00Z")
        assert effective_state(conn, 1, night).state == "closed"


def test_voided_arrival_no_longer_open(client):
    with db.connect() as conn:
        _site(conn)
        pid = _punch(conn, "in", "2026-03-10T06:00:00Z")
        conn.execute(
            "INSERT INTO punch_voids (punch_id, voided_by, reason) VALUES (?, 2, 'gresit')",
            (pid,),
        )
        assert effective_state(conn, 1, utc(2026, 3, 11)).state == "closed"


def test_pending_arrival_opens_shift(client):
    with db.connect() as conn:
        _site(conn)
        _request(conn, "in", "2026-03-10T06:00:00Z")
        assert effective_state(conn, 1, utc(2026, 3, 10, 15)).state == "pending_in"
        assert effective_state(conn, 1, utc(2026, 3, 10, 20)).state == "forgotten"
        _punch(conn, "out", "2026-03-10T15:00:00Z")
        assert effective_state(conn, 1, utc(2026, 3, 10, 20)).state == "closed"


def test_all_states_matches_effective_state(client):
    """The dashboard (all_states) and the clock page (effective_state) never disagree."""
    night = utc(2026, 3, 10, 21)
    with db.connect() as conn:
        _site(conn)
        conn.execute(
            "INSERT INTO employees (name, email, password_hash, role) VALUES ('C', 'c@example.com', 'x', 'employee')"
        )
        ins = (
            "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside) "
            "VALUES (?, 1, ?, ?, 44.4, 26.1, 5, 1)"
        )
        # 1: forgotten punch shift with a declared departure -> closed
        pid = conn.execute(ins, (1, "in", "2026-03-10T06:00:00Z")).lastrowid
        _request(conn, "out", "2026-03-10T15:00:00Z")
        # 2: arrived 22:30 local, so its cutoff is 22:00 the next day -> open
        conn.execute(ins, (2, "in", "2026-03-10T20:30:00Z"))
        # 3: forgotten arrival request left open -> forgotten without a punch
        conn.execute(
            "INSERT INTO correction_requests (employee_id, site_id, kind, ts_utc, note) "
            "VALUES (3, 1, 'in', '2026-03-10T06:00:00Z', 'x')"
        )
        states = all_states(conn, night)
        for emp in (1, 2, 3):
            assert states.get(emp, effective_state(conn, 99, night)) == effective_state(
                conn, emp, night
            )
    assert 1 not in states and pid
    assert states[2].state == "open" and states[2].open_punch_id is not None
    assert states[3].state == "forgotten" and states[3].open_punch_id is None
