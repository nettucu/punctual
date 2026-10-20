"""Employee and admin in two real browser contexts: request, decide, see the result."""

import pytest
from helpers import AT_SITE, NEAR_SITE, login, request_leave
from playwright.sync_api import expect

ADMIN_PAGES = [
    "/admin", "/admin/employees", "/admin/sites", "/admin/reports", "/admin/corrections",
    "/admin/leave", "/admin/attempts", "/admin/history", "/admin/qr",
]  # fmt: skip


@pytest.fixture()
def boss(browser, live):
    ctx = browser.new_context(locale="ro-RO")
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept(d.default_value or "good enough reason"))
    login(page, live, "boss@example.com")
    yield page
    ctx.close()


def test_admin_pages_load_without_js_errors(boss, live):
    errors: list[str] = []
    boss.on("pageerror", lambda e: errors.append(str(e)))
    boss.on(
        "response",
        lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 500 else None,
    )
    for path in ADMIN_PAGES:
        boss.goto(live.url + path)
        boss.wait_for_load_state("networkidle")
        expect(boss.locator("main")).to_be_visible()
    assert errors == []


def test_employee_cannot_open_admin_api(page, live):
    login(page, live, "ana@example.com")
    assert page.request.get(f"{live.url}/api/employees").status == 403
    page.goto(f"{live.url}/admin/leave")
    page.wait_for_url(f"{live.url}/")


def test_anonymous_redirected_or_denied(page, live):
    assert page.request.get(f"{live.url}/api/punch/status").status == 401


def test_leave_request_approved_by_admin_shows_on_clock_page(page, boss, live):
    login(page, live, "ana@example.com")
    request_leave(page)
    expect(page.locator("#mylv")).to_contain_text("pending")
    boss.goto(f"{live.url}/admin/leave")
    boss.get_by_role("button", name="Approve").click()
    page.reload()
    expect(page.locator("#mylv")).to_contain_text("approved")


def test_leave_request_rejected_shows_reason(page, boss, live):
    login(page, live, "ana@example.com")
    request_leave(page)
    expect(page.locator("#mylv")).to_contain_text("pending")
    boss.goto(f"{live.url}/admin/leave")
    boss.get_by_role("button", name="Reject").click()
    page.reload()
    expect(page.locator("#mylv")).to_contain_text("rejected")
    expect(page.locator("#mylv")).to_contain_text("good enough reason")


def test_correction_request_approved_creates_punch(page, boss, live):
    login(page, live, "ana@example.com")
    page.click("#reqbtn")
    page.fill("#rf textarea[name=note]", "forgot to clock in")
    page.evaluate(
        "document.querySelector('#rf [name=local_time]').value = "
        "new Date(Date.now()-3*3600e3).toISOString().slice(0,16)"
    )
    page.click("#rf button.btn:not(.ghost)")
    expect(page.locator("#mine")).to_contain_text("pending")
    boss.goto(f"{live.url}/admin/corrections")
    boss.get_by_role("button", name="Approve", exact=True).first.click()
    page.reload()
    expect(page.locator("#mine")).to_contain_text("approved")


def test_flagged_punch_appears_in_corrections_and_can_be_approved(page, boss, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation(NEAR_SITE)
    page.click("#go")
    expect(page.locator("#chipt")).to_contain_text("At work since")
    boss.goto(f"{live.url}/admin/corrections")
    boss.get_by_role("button", name="Approve location").click()
    expect(boss.locator("#toast")).to_contain_text("Location approved")


def test_admin_voids_punch_employee_is_back_to_not_clocked(page, boss, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation(AT_SITE)
    page.click("#go")
    expect(page.locator("#chipt")).to_contain_text("At work since")
    boss.goto(f"{live.url}/admin/corrections")
    boss.get_by_role("button", name="Void", exact=True).first.click()
    expect(boss.locator("#toast")).to_contain_text("Punch voided")
    page.reload()
    expect(page.locator("#chipt")).to_have_text("Not clocked in")


@pytest.mark.server_env(geofence_block_over_m=1000)
def test_rejected_attempt_listed_for_admin(page, boss, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation({"latitude": 45.0, "longitude": 26.1, "accuracy": 10})
    page.click("#go")
    expect(page.locator("#msg")).to_have_class("hint bad")
    boss.goto(f"{live.url}/admin/attempts")
    expect(boss.locator("tbody tr")).not_to_have_count(0)
    expect(boss.locator("tbody")).to_contain_text("Ana")


def test_dashboard_shows_clocked_in_employee(page, boss, live):
    login(page, live, "ana@example.com")
    page.click("#go")
    expect(page.locator("#chipt")).to_contain_text("At work since")
    boss.goto(f"{live.url}/admin")
    expect(boss.locator("main")).to_contain_text("Ana")


def test_report_csv_downloads(boss, live):
    r = boss.request.get(
        f"{live.url}/api/reports/hours.csv?from=2026-01-01&to=2026-01-31"
    )
    assert r.status == 200


KEY_LIKE = r"^[a-z]+(\.[a-z_0-9]+)+$"


def _assert_translated(boss, live):
    """Every page renders real text (a raw catalog key on screen means a missing translation)."""
    for path in ["/", "/help", "/admin/help", *ADMIN_PAGES]:
        boss.goto(f"{live.url}{path}")
        boss.wait_for_load_state("networkidle")
        raw = boss.evaluate(
            """src => { const re = new RegExp(src); return [...document.querySelectorAll('body *')].filter(e => e.children.length === 0
                && re.test(e.textContent.trim())).map(e => e.textContent.trim()) }""",
            KEY_LIKE,
        )
        assert not raw, (path, raw)


def test_pages_have_no_untranslated_keys_in_english(boss, live):
    _assert_translated(boss, live)
    assert boss.evaluate("document.documentElement.lang") == "en"


@pytest.mark.server_env(locale="ro")
def test_pages_have_no_untranslated_keys_in_romanian(boss, live):
    _assert_translated(boss, live)
    assert boss.evaluate("document.documentElement.lang") == "ro"
    boss.goto(f"{live.url}/admin")
    expect(boss.locator("main h1")).to_contain_text("Dashboard")
    expect(boss.locator("aside.side")).to_contain_text("Rapoarte")


def test_time_picker_is_24h_and_value_roundtrips(page, live):
    login(page, live, "ana@example.com")
    page.click("#reqbtn")
    page.evaluate("document.querySelector('#rf [name=local_time]').value = '2026-01-05T17:45'")
    sel = page.locator("#rf .dt24 select")
    expect(sel.nth(0)).to_have_value("17")
    expect(sel.nth(1)).to_have_value("45")
    sel.nth(0).select_option("08")
    assert page.evaluate("document.querySelector('#rf [name=local_time]').value") == "2026-01-05T08:45"


def test_punch_row_shows_map_icon_for_ok_punch(page, boss, live):
    login(page, live, "ana@example.com")
    page.context.set_geolocation(AT_SITE)
    page.click("#go")
    expect(page.locator("#chipt")).to_contain_text("At work since")
    boss.goto(f"{live.url}/admin/corrections")
    expect(boss.locator("#rows a[href*='google.com/maps']").first).to_be_visible()
