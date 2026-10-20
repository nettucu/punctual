"""Shared constants and the browser login step."""

PW = "Correct-Horse-9-battery"
SITE_LAT, SITE_LON = 44.4268, 26.1025
AT_SITE = {"latitude": SITE_LAT, "longitude": SITE_LON, "accuracy": 10}
NEAR_SITE = {
    "latitude": SITE_LAT + 0.0030,
    "longitude": SITE_LON,
    "accuracy": 10,
}  # ~330 m: flagged
FAR_AWAY = {
    "latitude": SITE_LAT + 0.05,
    "longitude": SITE_LON,
    "accuracy": 10,
}  # ~5.5 km: rejected
POOR_FIX = {"latitude": SITE_LAT, "longitude": SITE_LON, "accuracy": 500}


def login(page, live, email: str) -> None:
    page.goto(f"{live.url}/login")
    page.fill("#e", email)
    page.fill("#p", PW)
    page.click("#b")
    page.wait_for_url(lambda u: "/login" not in u)


def request_leave(page) -> None:
    """Open the leave dialog and submit one working day (the default, today, fails on weekends and holidays)."""
    from datetime import timedelta

    from punctual.clock import today
    from punctual.holidays import holiday_name

    d = today() + timedelta(days=1)
    while d.weekday() >= 5 or holiday_name(d):
        d += timedelta(days=1)
    page.click("#lvbtn")
    page.fill("#lf [name=date_from]", d.isoformat())
    page.fill("#lf [name=date_to]", d.isoformat())
    page.click("#lf button.btn:not(.ghost)")
