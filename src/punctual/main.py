"""FastAPI entry point."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from punctual.attempts import purge_old
from punctual.attempts import router as attempts_router
from punctual.audit import router as audit_router
from punctual.auth import router as auth_router
from punctual.cli import bootstrap_admin
from punctual.clock import TimeInputError
from punctual.corrections import router as corrections_router
from punctual.db import init_db
from punctual.employees import router as employees_router
from punctual.i18n import catalog
from punctual.leave import router as leave_router
from punctual.overview import router as overview_router
from punctual.punch import router as punch_router
from punctual.registration import router as registration_router
from punctual.reports import router as reports_router
from punctual.requests import router as requests_router
from punctual.settings import settings
from punctual.sites import router as sites_router

logger: logging.Logger = logging.getLogger(__name__)

init_db()
bootstrap_admin()
purge_old()
app: FastAPI = FastAPI(title="Punctual")
app.include_router(auth_router)
app.include_router(sites_router)
app.include_router(employees_router)
app.include_router(punch_router)
app.include_router(reports_router)
app.include_router(corrections_router)
app.include_router(requests_router)
app.include_router(leave_router)
app.include_router(attempts_router)
app.include_router(overview_router)
app.include_router(audit_router)
app.include_router(registration_router)


@app.exception_handler(TimeInputError)
async def time_input_error(_: Request, exc: TimeInputError) -> JSONResponse:
    """A bad typed time or date range (see `punctual.clock`) is the client's mistake: 422."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


STATIC: Path = Path(__file__).parent / "static"
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
SCANNER_PATH_MARKERS: tuple[str, ...] = (
    "wp-",
    "xmlrpc.php",
    "wlwmanifest.xml",
    ".env",
    ".git/",
    "phpmyadmin",
    "wordpress",
)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def scanner_probe_block(request: Request, call_next) -> Response:
    """Fast-reject well-known vulnerability-scanner paths before routing or auth run.

    Cloud Run bills CPU while an instance is warm; scanner bots (e.g. WordPress
    probes) repeatedly wake idle instances for paths this app never serves.

    Args:
        request: Incoming request.
        call_next: Next handler.

    Returns:
        404 immediately for known scanner paths, otherwise the normal response.
    """
    path: str = request.url.path.lower()
    if any(marker in path for marker in SCANNER_PATH_MARKERS):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return await call_next(request)


@app.middleware("http")
async def origin_check(request: Request, call_next) -> Response:
    """Reject cross-origin state-changing requests (CSRF defense in depth on top of SameSite=Strict).

    Args:
        request: Incoming request.
        call_next: Next handler.

    Returns:
        403 if the Origin header does not match the Host, otherwise the normal response.
    """
    if request.method not in SAFE_METHODS:
        origin: str | None = request.headers.get("origin")
        if origin is not None and origin.split("://", 1)[-1] != request.headers.get(
            "host"
        ):
            return JSONResponse({"detail": "bad origin"}, status_code=403)
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/static/") or response.headers.get(
        "content-type", ""
    ).startswith("text/html"):
        response.headers["Cache-Control"] = (
            "no-cache"  # revalidate (ETag) so deploys are picked up
        )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/", include_in_schema=False)
def index_page() -> FileResponse:
    """Serve the employee clock page."""
    return FileResponse(STATIC / "index.html")


@app.get("/admin/employees", include_in_schema=False)
def employees_page() -> FileResponse:
    """Serve the employees admin page (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "employees.html")


@app.get("/admin/reports", include_in_schema=False)
def reports_page() -> FileResponse:
    """Serve the reports page (the API behind it enforces auth)."""
    return FileResponse(STATIC / "reports.html")


@app.get("/admin", include_in_schema=False)
def dashboard_page() -> FileResponse:
    """Serve the admin dashboard (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "dashboard.html")


@app.get("/admin/corrections", include_in_schema=False)
def corrections_page() -> FileResponse:
    """Serve the corrections page (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "corrections.html")


@app.get("/admin/leave", include_in_schema=False)
def leave_page() -> FileResponse:
    """Serve the leave requests page (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "leave.html")


@app.get("/p/{key}", include_in_schema=False)
def scan_page(key: str) -> FileResponse:
    """Serve the clock page with the site locked to the scanned QR (the API enforces auth and site assignment)."""
    return FileResponse(STATIC / "index.html")


@app.get("/admin/qr", include_in_schema=False)
def qr_page() -> FileResponse:
    """Serve the printable QR page (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "qr.html")


@app.get("/admin/history", include_in_schema=False)
def history_page() -> FileResponse:
    """Serve the change history page (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "history.html")


@app.get("/admin/attempts", include_in_schema=False)
def attempts_page() -> FileResponse:
    """Serve the rejected-attempts page (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "attempts.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    """Serve the login page."""
    return FileResponse(STATIC / "login.html")


@app.get("/help", include_in_schema=False)
def help_page() -> FileResponse:
    """Serve the employee manual."""
    return FileResponse(STATIC / f"help.{settings.locale}.html")


@app.get("/admin/help", include_in_schema=False)
def admin_help_page() -> FileResponse:
    """Serve the admin manual (admins only; others are redirected to the employee manual)."""
    return FileResponse(STATIC / f"admin-help.{settings.locale}.html")


@app.get("/register", include_in_schema=False)
def register_page() -> FileResponse:
    """Serve the self-registration page (the API checks the link token)."""
    return FileResponse(STATIC / "register.html")


@app.get("/admin/sites", include_in_schema=False)
def sites_page() -> FileResponse:
    """Serve the sites admin page (the API behind it enforces admin auth)."""
    return FileResponse(STATIC / "sites.html")


@app.get("/i18n.js", include_in_schema=False)
def i18n_js() -> Response:
    """The active language's messages for the browser: `window.LOCALE` and `window.I18N`."""
    body: str = f"window.LOCALE={json.dumps(settings.locale)};window.I18N={json.dumps(catalog(settings.locale))};"
    return Response(
        body,
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/brand/logo", include_in_schema=False)
def brand_logo() -> FileResponse:
    """The logo: `static/custom/logo.*` when present (private branding, git-ignored), else the default."""
    custom = next(iter(sorted((STATIC / "custom").glob("logo.*"))), None)
    return FileResponse(custom or STATIC / "logo.svg")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe for Cloud Run / Docker healthchecks.

    Returns:
        Static OK payload.
    """
    return {"status": "ok"}


def main() -> None:
    """Run the dev server."""
    import uvicorn

    uvicorn.run(
        "punctual.main:app",
        host="127.0.0.1",
        port=settings.dev_port,
        reload=True,
        reload_includes=["*.json"],  # catalogs are cached in memory
    )
