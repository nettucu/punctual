"""Admin CRUD for sites (geofenced workplaces)."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from punctual.auth import Me, require_admin
from punctual.db import connect
from punctual.i18n import t
from punctual.settings import settings

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/sites", tags=["sites"])


class SiteIn(BaseModel):
    """Site payload."""

    name: str = Field(min_length=1, max_length=100)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(gt=0, le=5000)


class SiteOut(SiteIn):
    """Stored site."""

    id: int
    qr_key: str


@router.get("", response_model=list[SiteOut])
def list_sites(_: Annotated[Me, Depends(require_admin)]) -> list[SiteOut]:
    """List all sites."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY name").fetchall()
    return [SiteOut(**dict(r)) for r in rows]


@router.post("", response_model=SiteOut, status_code=201)
def create_site(body: SiteIn, _: Annotated[Me, Depends(require_admin)]) -> SiteOut:
    """Create a site.

    Raises:
        HTTPException: 409 if the name already exists.
    """
    key: str = secrets.token_urlsafe(9)
    try:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO sites (name, lat, lon, radius_m, qr_key) VALUES (?, ?, ?, ?, ?)",
                (body.name.strip(), body.lat, body.lon, body.radius_m, key),
            )
            site_id: int = int(cur.lastrowid or 0)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=t("err.site_name_exists")) from exc
    logger.debug(f"site created id={site_id}")
    return SiteOut(id=site_id, qr_key=key, **body.model_dump())


@router.put("/{site_id}", response_model=SiteOut)
def update_site(
    site_id: int, body: SiteIn, _: Annotated[Me, Depends(require_admin)]
) -> SiteOut:
    """Update a site.

    Raises:
        HTTPException: 404 if missing, 409 on duplicate name.
    """
    try:
        with connect() as conn:
            cur = conn.execute(
                "UPDATE sites SET name = ?, lat = ?, lon = ?, radius_m = ? WHERE id = ?",
                (body.name.strip(), body.lat, body.lon, body.radius_m, site_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=t("err.site_not_found"))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=t("err.site_name_exists")) from exc
    with connect() as conn:
        key_row = conn.execute(
            "SELECT qr_key FROM sites WHERE id = ?", (site_id,)
        ).fetchone()
    return SiteOut(id=site_id, qr_key=key_row["qr_key"], **body.model_dump())


@router.post("/{site_id}/regenerate-key", response_model=SiteOut)
def regenerate_key(site_id: int, _: Annotated[Me, Depends(require_admin)]) -> SiteOut:
    """Issue a new QR key (old printed codes stop resolving).

    Raises:
        HTTPException: 404 if missing.
    """
    key: str = secrets.token_urlsafe(9)
    with connect() as conn:
        cur = conn.execute("UPDATE sites SET qr_key = ? WHERE id = ?", (key, site_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=t("err.site_not_found"))
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return SiteOut(**dict(row))


@router.get("/{site_id}/qr.svg")
def qr_svg(
    site_id: int, request: Request, _: Annotated[Me, Depends(require_admin)]
) -> Response:
    """Render the printable QR code for a site: one code for both arrival and departure.

    The clock page picks the right action from the employee's state.

    Raises:
        HTTPException: 404 for an unknown site.
    """
    import io

    import segno

    with connect() as conn:
        row = conn.execute(
            "SELECT qr_key FROM sites WHERE id = ?", (site_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("err.site_not_found"))
    base: str = (settings.public_url or str(request.base_url)).rstrip("/")
    buf = io.BytesIO()
    segno.make(f"{base}/p/{row['qr_key']}", error="m").save(
        buf,
        kind="svg",
        scale=8,
        border=4,
        dark="#000000",
        light="#ffffff",
        xmldecl=False,
    )
    return Response(buf.getvalue(), media_type="image/svg+xml")


@router.delete("/{site_id}", status_code=204)
def delete_site(site_id: int, _: Annotated[Me, Depends(require_admin)]) -> None:
    """Delete a site that has no punches.

    Raises:
        HTTPException: 404 if missing, 409 if punches reference it (audit trail must stay intact).
    """
    try:
        with connect() as conn:
            conn.execute("DELETE FROM employee_sites WHERE site_id = ?", (site_id,))
            cur = conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=t("err.site_not_found"))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=t("err.site_has_punches")) from exc
