"""Demo data for trying Punctual out: `punctual-admin seed-demo` fills an empty database."""

from __future__ import annotations

import random
import secrets
from datetime import date, datetime, timedelta

from punctual.clock import TZ, today, utc_str
from punctual.db import connect, init_db
from punctual.holidays import holiday_name
from punctual.security import hash_password

DEMO_PASSWORD: str = "Demo-Password-1!"
ADMIN_EMAIL: str = "admin@example.com"
HISTORY_DAYS: int = 21
# (name, email, daily norm, site names). The first site is the usual one.
STAFF: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("Ana Popescu", "ana@example.com", 8, ("Main Clinic",)),
    ("Mihai Ionescu", "mihai@example.com", 8, ("Main Clinic",)),
    ("Elena Radu", "elena@example.com", 6, ("North Branch",)),
    ("Andrei Stan", "andrei@example.com", 8, ("North Branch", "Main Clinic")),
)
SITES: dict[str, tuple[float, float]] = {
    "Main Clinic": (44.4268, 26.1025),
    "North Branch": (44.4568, 26.0825),
}


def _at(day: date, hour: int, minute: int, plus_min: int = 0) -> str:
    """Stored UTC string of a local wall-clock time (plus optional minutes)."""
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return utc_str(start + timedelta(minutes=plus_min))


def _workdays(back: int) -> list[date]:
    """Past working days, oldest first, ending yesterday."""
    days: list[date] = [today() - timedelta(days=n) for n in range(back, 0, -1)]
    return [d for d in days if d.weekday() < 5 and not holiday_name(d)]


def seed_demo() -> None:
    """Create an admin, four employees, two sites and three weeks of realistic activity.

    Raises:
        ValueError: If the database already has employees.
    """
    init_db()
    rng: random.Random = random.Random(7)
    pw_hash: str = hash_password(DEMO_PASSWORD)
    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]:
            raise ValueError("the database is not empty, refusing to add demo data")
        admin: int = conn.execute(
            "INSERT INTO employees (name, email, password_hash, role) VALUES ('Demo Admin', ?, ?, 'admin')",
            (ADMIN_EMAIL, pw_hash),
        ).lastrowid
        site_id: dict[str, int] = {
            name: conn.execute(
                "INSERT INTO sites (name, lat, lon, radius_m, qr_key) VALUES (?, ?, ?, 100, ?)",
                (name, lat, lon, secrets.token_urlsafe(12)),
            ).lastrowid
            for name, (lat, lon) in SITES.items()
        }
        emp: dict[str, int] = {}
        for name, email, norma, sites in STAFF:
            emp[email] = conn.execute(
                "INSERT INTO employees (name, email, password_hash, norma_h) VALUES (?, ?, ?, ?)",
                (name, email, pw_hash, norma),
            ).lastrowid
            conn.executemany(
                "INSERT INTO employee_sites (employee_id, site_id) VALUES (?, ?)",
                [(emp[email], site_id[s]) for s in sites],
            )
        days: list[date] = _workdays(HISTORY_DAYS)

        def punch(
            email: str,
            site: str,
            kind: str,
            ts: str,
            *,
            off_m: float = 0.0,
            source: str = "device",
            created_by: int | None = None,
            created_at: str | None = None,
            note: str | None = None,
            corrects_id: int | None = None,
        ) -> int:
            lat, lon = SITES[site]
            return conn.execute(
                "INSERT INTO punches (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, inside, distance_m, "
                "source, created_by, created_at, note, corrects_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    emp[email],
                    site_id[site],
                    kind,
                    ts,
                    lat + off_m / 111_000 + rng.uniform(-0.0001, 0.0001),
                    lon + rng.uniform(-0.0001, 0.0001),
                    rng.uniform(6, 25),
                    int(off_m < 100),
                    round(off_m or rng.uniform(3, 40), 1),
                    source,
                    created_by,
                    created_at,
                    note,
                    corrects_id,
                ),
            ).lastrowid

        def leave(
            email: str, kind: str, d_from: date, d_to: date, note: str, status: str
        ) -> None:
            conn.execute(
                "INSERT INTO leave_requests (employee_id, kind, date_from, date_to, note, status, decided_by, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    emp[email],
                    kind,
                    d_from.isoformat(),
                    d_to.isoformat(),
                    note,
                    status,
                    admin if status != "pending" else None,
                    utc_str(datetime.now(TZ)) if status != "pending" else None,
                ),
            )

        off: set[tuple[str, date]] = set()
        if len(days) > 9:
            vac = days[2:4]
            leave(
                "elena@example.com", "co", vac[0], vac[-1], "Short vacation", "approved"
            )
            off |= {("elena@example.com", d) for d in vac}
            sick = days[7]
            leave("mihai@example.com", "cm", sick, sick, "Flu", "approved")
            off.add(("mihai@example.com", sick))
        special = {
            "flag": days[-5]
            if len(days) > 5
            else None,  # Mihai, arrival outside the zone
            "edit": days[-8]
            if len(days) > 8
            else None,  # Ana, departure corrected by the admin
            "missing": days[-3] if len(days) > 3 else None,  # Elena forgot to clock out
            "forgot": days[-1] if days else None,  # Andrei never clocked out yesterday
        }
        for day in days:
            for name, email, norma, sites in STAFF:
                if (email, day) in off:
                    continue
                site = sites[day.toordinal() % len(sites)]
                m_in: int = rng.randint(-10, 15)
                worked: int = norma * 60 + rng.randint(-25, 20)
                far = email == "mihai@example.com" and day == special["flag"]
                punch(email, site, "in", _at(day, 8, 0, m_in), off_m=330 if far else 0)
                if email == "elena@example.com" and day == special["missing"]:
                    continue
                if email == "andrei@example.com" and day == special["forgot"]:
                    continue
                if email == "ana@example.com" and day == special["edit"]:
                    wrong = punch(email, site, "out", _at(day, 16, 5))
                    conn.execute(
                        "INSERT INTO punch_voids (punch_id, voided_by, reason) VALUES (?, ?, ?)",
                        (wrong, admin, "Edited: left at 17:00, confirmed by phone"),
                    )
                    punch(
                        email, site, "out", _at(day, 17, 0), source="admin", created_by=admin,
                        created_at=utc_str(datetime.now(TZ)), corrects_id=wrong,
                        note="Edited: left at 17:00, confirmed by phone",
                    )  # fmt: skip
                    continue
                punch(email, site, "out", _at(day, 8, 0, m_in + worked))
        if special["missing"]:
            conn.execute(
                "INSERT INTO correction_requests (employee_id, site_id, kind, ts_utc, note) VALUES (?, ?, 'out', ?, ?)",
                (
                    emp["elena@example.com"],
                    site_id["North Branch"],
                    _at(special["missing"], 14, 0),
                    "I forgot to clock out, I left at 14:00",
                ),
            )
        now: datetime = datetime.now(TZ)
        if today().weekday() < 5 and not holiday_name(today()) and now.hour >= 9:
            for email, site in (
                ("ana@example.com", "Main Clinic"),
                ("elena@example.com", "North Branch"),
            ):
                punch(email, site, "in", _at(today(), 8, rng.randint(0, 10)))
        if days:
            conn.execute(
                "INSERT INTO punch_attempts (employee_id, site_id, kind, ts_utc, lat, lon, accuracy_m, distance_m, reason) "
                "VALUES (?, ?, 'in', ?, ?, ?, 15, 3700, 'too_far')",
                (
                    emp["andrei@example.com"],
                    site_id["Main Clinic"],
                    _at(days[-1], 8, 40),
                    SITES["Main Clinic"][0] + 0.033,
                    SITES["Main Clinic"][1],
                ),
            )
        ahead: date = today() + timedelta(days=7)
        while ahead.weekday() >= 5:
            ahead += timedelta(days=1)
        leave(
            "andrei@example.com",
            "co",
            ahead,
            ahead + timedelta(days=1),
            "Family trip",
            "pending",
        )
