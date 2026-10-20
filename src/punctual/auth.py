"""Login/logout endpoints and auth dependencies."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from punctual.clock import utc_str
from punctual.db import connect
from punctual.i18n import t
from punctual.security import hash_password, new_token, token_hash, verify_password
from punctual.settings import settings

logger: logging.Logger = logging.getLogger(__name__)

COOKIE_NAME: str = "punctual_session"
# Verified against when the email is unknown, so timing does not reveal valid emails.
_DUMMY_HASH: str = hash_password("Aa1!" + new_token())

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    """Login payload."""

    email: str
    password: str


class Me(BaseModel):
    """Authenticated user."""

    id: int
    name: str
    email: str
    role: str
    norma_h: int = 8


def _now() -> datetime:
    return datetime.now(UTC)


def _fmt(dt: datetime) -> str:
    return utc_str(dt)


def _register_failure(conn: sqlite3.Connection, employee_id: int) -> None:
    """Increment the failure counter and lock the account when the limit is hit."""
    conn.execute(
        "UPDATE employees SET failed_logins = failed_logins + 1 WHERE id = ?",
        (employee_id,),
    )
    row = conn.execute(
        "SELECT failed_logins FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()
    if row["failed_logins"] >= settings.max_failed_logins:
        until: str = _fmt(_now() + timedelta(minutes=settings.lockout_minutes))
        conn.execute(
            "UPDATE employees SET locked_until = ?, failed_logins = 0 WHERE id = ?",
            (until, employee_id),
        )
        logger.warning(f"employee {employee_id} locked until {until}")


@router.post("/login", response_model=Me)
def login(body: LoginBody, response: Response) -> Me:
    """Authenticate and set the session cookie.

    Args:
        body: Email and password.
        response: Response used to set the cookie.

    Returns:
        The authenticated user.

    Raises:
        HTTPException: 401 on any failure (same message for all causes).
    """
    denied: HTTPException = HTTPException(
        status_code=401, detail=t("err.invalid_credentials")
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE email = ?", (body.email.strip(),)
        ).fetchone()
        if row is None:
            verify_password(_DUMMY_HASH, body.password)
            raise denied
        if not row["active"]:
            raise denied
        if row["locked_until"] and row["locked_until"] > _fmt(_now()):
            raise denied
        if not verify_password(row["password_hash"], body.password):
            _register_failure(conn, row["id"])
            conn.commit()
            raise denied
        conn.execute(
            "UPDATE employees SET failed_logins = 0, locked_until = NULL WHERE id = ?",
            (row["id"],),
        )
        token: str = new_token()
        expires: datetime = _now() + timedelta(minutes=settings.session_ttl_minutes)
        conn.execute(
            "INSERT INTO sessions (token_hash, employee_id, expires_at) VALUES (?, ?, ?)",
            (token_hash(token), row["id"], _fmt(expires)),
        )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    logger.debug(f"login ok employee={row['id']}")
    return Me(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        role=row["role"],
        norma_h=row["norma_h"],
    )


@router.post("/logout", status_code=204)
def logout(
    response: Response, punctual_session: Annotated[str | None, Cookie()] = None
) -> None:
    """Delete the server-side session and clear the cookie.

    Args:
        response: Response used to clear the cookie.
        punctual_session: Session cookie value.
    """
    if punctual_session:
        with connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (token_hash(punctual_session),),
            )
    response.delete_cookie(COOKIE_NAME)


def current_user(punctual_session: Annotated[str | None, Cookie()] = None) -> Me:
    """Resolve the session cookie to a user.

    Args:
        punctual_session: Session cookie value.

    Returns:
        The authenticated active user.

    Raises:
        HTTPException: 401 if missing, expired, or the user is inactive.
    """
    if not punctual_session:
        raise HTTPException(status_code=401, detail=t("err.not_authenticated"))
    with connect() as conn:
        row = conn.execute(
            "SELECT e.id, e.name, e.email, e.role, e.norma_h FROM sessions s JOIN employees e ON e.id = s.employee_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND e.active = 1",
            (token_hash(punctual_session), _fmt(_now())),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail=t("err.not_authenticated"))
    return Me(**dict(row))


def require_admin(user: Annotated[Me, Depends(current_user)]) -> Me:
    """Allow only admins.

    Args:
        user: Authenticated user.

    Returns:
        The same user.

    Raises:
        HTTPException: 403 if not an admin.
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail=t("err.admin_only"))
    return user


@router.get("/me", response_model=Me)
def me(user: Annotated[Me, Depends(current_user)]) -> Me:
    """Return the current user."""
    return user
