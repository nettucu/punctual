import pytest
from helpers import FAR_AWAY, NEAR_SITE, POOR_FIX, login, request_leave
from playwright.sync_api import expect


def press(page):
    page.click("#go")


def test_login_wrong_password_stays_on_login(page, live):
    page.goto(f"{live.url}/login")
    page.fill("#e", "ana@example.com")
    page.fill("#p", "wrong-password-xx")
    page.click("#b")
    expect(page.locator("#toast")).to_contain_text("Wrong credentials")
    assert "/login" in page.url


def test_arrival_then_departure_at_site(page, live):
    login(page, live, "ana@example.com")
    expect(page.locator("#chipt")).to_have_text("Not clocked in")
    press(page)
    expect(page.locator("#msg")).to_have_text("Recorded ✓")
    expect(page.locator("#chipt")).to_contain_text("At work since")
    expect(page.locator("#gol")).to_contain_text("Departure")
    # departure right after arrival is refused by the server
    press(page)
    expect(page.locator("#msg")).to_contain_text("too soon")
    expect(page.locator("#chipt")).to_contain_text("At work since")


def test_near_site_is_flagged_but_recorded(page, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation(NEAR_SITE)
    press(page)
    expect(page.locator("#msg")).not_to_have_class("hint bad")
    expect(page.locator("#chipt")).to_contain_text("At work since")


@pytest.mark.server_env(geofence_block_over_m=1000)
def test_far_away_rejected(page, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation(FAR_AWAY)
    press(page)
    expect(page.locator("#msg")).to_have_class("hint bad")
    expect(page.locator("#chipt")).to_have_text("Not clocked in")


@pytest.mark.server_env(geofence_max_accuracy_m=150)
def test_poor_gps_accuracy_rejected(page, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation(POOR_FIX)
    press(page)
    expect(page.locator("#msg")).to_have_class("hint bad")
    expect(page.locator("#chipt")).to_have_text("Not clocked in")


@pytest.mark.server_env(geofence_mode="block")
def test_block_mode_rejects_near_site(page, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation(
        {**NEAR_SITE, "latitude": NEAR_SITE["latitude"] + 0.001}
    )
    press(page)
    expect(page.locator("#msg")).to_have_class("hint bad")


def test_location_permission_denied(browser, live):
    ctx = browser.new_context(locale="ro-RO")  # no geolocation permission
    page = ctx.new_page()
    login(page, live, "ana@example.com")
    press(page)
    expect(page.locator("#msg")).to_have_class("hint bad")
    expect(page.locator("#chipt")).to_have_text("Not clocked in")
    ctx.close()


@pytest.mark.server_env(late_in_hour=0)
def test_late_arrival_asks_confirmation(page, live):
    login(page, live, "ana@example.com")
    press(page)
    expect(page.locator("#late")).to_be_visible()
    page.click("#latenow")
    expect(page.locator("#chipt")).to_contain_text("At work since")


@pytest.mark.server_env(late_in_hour=0)
def test_late_arrival_forgot_opens_request_dialog(page, live):
    login(page, live, "ana@example.com")
    press(page)
    page.click("#lateforgot")
    expect(page.locator("#dlg")).to_be_visible()


def test_forgotten_shift_blocks_until_departure_declared(page, live):
    ana = 1
    live.sql(
        "INSERT INTO punches (employee_id, site_id, kind, lat, lon, accuracy_m, inside, ts_utc) "
        "VALUES (?, ?, 'in', 44.4268, 26.1025, 10, 1, strftime('%Y-%m-%dT%H:%M:%SZ','now','-3 days'))",
        ana,
        live.site_id,
    )
    login(page, live, "ana@example.com")
    expect(page.locator("#forgot")).to_be_visible()
    expect(page.locator("#go")).to_be_hidden()
    expect(page.locator("#chipt")).to_have_text("Unclosed shift")


def test_qr_scan_locks_site(page, live):
    login(page, live, "ana@example.com")
    page.goto(f"{live.url}/p/{live.site_key}")
    expect(page.locator("#lockinfo")).to_contain_text("Main Clinic")
    press(page)
    expect(page.locator("#msg")).to_have_text("Recorded ✓")


def test_qr_unknown_key_disables_button(page, live):
    login(page, live, "ana@example.com")
    page.goto(f"{live.url}/p/not-a-real-key")
    expect(page.locator("#lockinfo")).to_contain_text("Unknown QR code")
    expect(page.locator("#go")).to_be_disabled()


def test_leave_request_from_clock_page(page, live):
    login(page, live, "ana@example.com")
    request_leave(page)
    expect(page.locator("#mylv")).to_contain_text("pending")


def test_correction_request_from_clock_page(page, live):
    login(page, live, "ana@example.com")
    page.click("#reqbtn")
    page.fill("#rf textarea[name=note]", "forgot to clock in")
    page.evaluate(
        "document.querySelector('#rf [name=local_time]').value = new Date(Date.now()-3600e3).toISOString().slice(0,16)"
    )
    page.click("#rf button.btn:not(.ghost)")
    expect(page.locator("#mine")).to_contain_text("pending")
