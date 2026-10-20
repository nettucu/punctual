"""Self-registration: an admin issues one shared link, valid for a few hours, and employees create their own account.

New accounts start inactive: an admin activates them and assigns their sites, so a leaked link cannot grant access.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from punctual.auth import Me, require_admin
from punctual.clock import utc_str
from punctual.db import connect
from punctual.employees import PENDING_SQL
from punctual.i18n import t
from punctual.security import (
    check_password_policy,
    hash_password,
    new_token,
    token_hash,
)
from punctual.settings import settings

logger: logging.Logger = logging.getLogger(__name__)

MAX_PENDING_ACCOUNTS: int = 50  # a leaked link cannot flood the employee list

router: APIRouter = APIRouter(tags=["registration"])


class LinkOut(BaseModel):
    """A freshly issued registration link."""

    path: str
    expires_at: str


class RegisterIn(BaseModel):
    """Self-registration payload."""

    token: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(max_length=200)

    @field_validator("password")
    @classmethod
    def _policy(cls, v: str) -> str:
        return check_password_policy(v)


@router.post("/api/registration-link", response_model=LinkOut, status_code=201)
def issue_link(admin: Annotated[Me, Depends(require_admin)]) -> LinkOut:
    """Issue a new registration link; it replaces (invalidates) the previous one.

    Args:
        admin: Authenticated admin.

    Returns:
        The path to give to employees (prefix with the site address) and its expiry.
    """
    token: str = new_token()
    expires: str = utc_str(
        datetime.now(UTC) + timedelta(hours=settings.registration_link_hours)
    )
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO registration_link (id, token_hash, expires_at, created_by) VALUES (1, ?, ?, ?)",
            (token_hash(token), expires, admin.id),
        )
    return LinkOut(path=f"/register?t={token}", expires_at=expires)


@router.post("/auth/register", status_code=201)
def register(body: RegisterIn) -> dict[str, str]:
    """Create an inactive employee account from a valid registration link.

    Args:
        body: Link token and the new account's details.

    Returns:
        A status marker (`pending`: waiting for admin activation).

    Raises:
        HTTPException: 410 if the link is invalid or expired, 409 if the email or name exists, 429 when too many
            accounts already wait for an admin.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM registration_link WHERE token_hash = ? AND expires_at > ?",
            (token_hash(body.token), utc_str(datetime.now(UTC))),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=410, detail=t("err.link_invalid"))
        if (
            conn.execute(
                f"SELECT COUNT(*) FROM employees WHERE {PENDING_SQL}"
            ).fetchone()[0]
            >= MAX_PENDING_ACCOUNTS
        ):
            raise HTTPException(status_code=429, detail=t("err.too_many_accounts"))
        if conn.execute(
            "SELECT 1 FROM employees WHERE name = ? COLLATE NOCASE",
            (body.name.strip(),),
        ).fetchone():
            raise HTTPException(status_code=409, detail=t("err.name_exists"))
        try:
            conn.execute(
                "INSERT INTO employees (name, email, password_hash, role, active) VALUES (?, ?, ?, 'employee', 0)",
                (body.name.strip(), body.email.strip(), hash_password(body.password)),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=t("err.email_exists")) from exc
    logger.info(f"self-registered {body.email.strip()} (pending activation)")
    return {"status": "pending"}
