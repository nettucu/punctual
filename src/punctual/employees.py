"""Admin management of employees and their site assignments."""

from __future__ import annotations

import logging
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from punctual.auth import Me, require_admin
from punctual.db import connect
from punctual.i18n import t
from punctual.security import MIN_PASSWORD_LEN, check_password_policy, hash_password

logger: logging.Logger = logging.getLogger(__name__)

# An inactive account that never clocked: a self-registration waiting for an admin (shortcut: a brand-new employee an
# admin deactivated before their first punch also counts; upgrade to a column if that matters).
PENDING_SQL: str = "active = 0 AND NOT EXISTS (SELECT 1 FROM punches p WHERE p.employee_id = employees.id)"

router: APIRouter = APIRouter(prefix="/api/employees", tags=["employees"])


class EmployeeIn(BaseModel):
    """Create payload."""

    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=200)
    role: Literal["admin", "employee"] = "employee"
    norma_h: Literal[2, 4, 6, 8] = 8
    site_ids: list[int] = []

    @field_validator("password")
    @classmethod
    def _policy(cls, v: str) -> str:
        return check_password_policy(v)


class EmployeeUpdate(BaseModel):
    """Update payload; password is optional (reset when present)."""

    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "employee"]
    norma_h: Literal[2, 4, 6, 8]
    active: bool
    site_ids: list[int] = []
    password: str | None = Field(
        default=None, min_length=MIN_PASSWORD_LEN, max_length=200
    )

    @field_validator("password")
    @classmethod
    def _policy(cls, v: str | None) -> str | None:
        return None if v is None else check_password_policy(v)


class EmployeeOut(BaseModel):
    """Employee as returned to admins."""

    id: int
    name: str
    email: str
    role: str
    norma_h: int
    active: bool
    pending: bool = False
    site_ids: list[int]


def _load(conn: sqlite3.Connection, employee_id: int) -> EmployeeOut:
    row = conn.execute(
        "SELECT * FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("err.employee_not_found"))
    sites = conn.execute(
        "SELECT site_id FROM employee_sites WHERE employee_id = ?", (employee_id,)
    ).fetchall()
    pending: bool = bool(
        conn.execute(
            f"SELECT 1 FROM employees WHERE id = ? AND {PENDING_SQL}", (employee_id,)
        ).fetchone()
    )
    return EmployeeOut(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        role=row["role"],
        norma_h=row["norma_h"],
        active=bool(row["active"]),
        pending=pending,
        site_ids=sorted(r["site_id"] for r in sites),
    )


def _set_sites(conn: sqlite3.Connection, employee_id: int, site_ids: list[int]) -> None:
    conn.execute("DELETE FROM employee_sites WHERE employee_id = ?", (employee_id,))
    for sid in set(site_ids):
        if conn.execute("SELECT 1 FROM sites WHERE id = ?", (sid,)).fetchone() is None:
            raise HTTPException(status_code=422, detail=t("err.unknown_site", id=sid))
        conn.execute(
            "INSERT INTO employee_sites (employee_id, site_id) VALUES (?, ?)",
            (employee_id, sid),
        )


@router.get("", response_model=list[EmployeeOut])
def list_employees(_: Annotated[Me, Depends(require_admin)]) -> list[EmployeeOut]:
    """List all employees."""
    with connect() as conn:
        ids = [
            r["id"]
            for r in conn.execute("SELECT id FROM employees ORDER BY name").fetchall()
        ]
        return [_load(conn, i) for i in ids]


@router.post("", response_model=EmployeeOut, status_code=201)
def create_employee(
    body: EmployeeIn, _: Annotated[Me, Depends(require_admin)]
) -> EmployeeOut:
    """Create an employee.

    Raises:
        HTTPException: 409 if the email exists, 422 for an unknown site.
    """
    try:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO employees (name, email, password_hash, role, norma_h) VALUES (?, ?, ?, ?, ?)",
                (
                    body.name.strip(),
                    body.email.strip(),
                    hash_password(body.password),
                    body.role,
                    body.norma_h,
                ),
            )
            employee_id: int = int(cur.lastrowid or 0)
            _set_sites(conn, employee_id, body.site_ids)
            return _load(conn, employee_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=t("err.email_exists")) from exc


@router.put("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: int, body: EmployeeUpdate, admin: Annotated[Me, Depends(require_admin)]
) -> EmployeeOut:
    """Update an employee. Deactivation and password reset revoke all their sessions.

    Raises:
        HTTPException: 404 if missing, 409 if an admin would lock themselves out.
    """
    if employee_id == admin.id and (not body.active or body.role != "admin"):
        raise HTTPException(status_code=409, detail=t("err.self_demote"))
    with connect() as conn:
        _load(conn, employee_id)
        conn.execute(
            "UPDATE employees SET name = ?, role = ?, norma_h = ?, active = ? WHERE id = ?",
            (body.name.strip(), body.role, body.norma_h, int(body.active), employee_id),
        )
        revoke: bool = not body.active
        if body.password:
            conn.execute(
                "UPDATE employees SET password_hash = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
                (hash_password(body.password), employee_id),
            )
            revoke = True
        if revoke:
            conn.execute("DELETE FROM sessions WHERE employee_id = ?", (employee_id,))
        _set_sites(conn, employee_id, body.site_ids)
        logger.debug(f"employee {employee_id} updated by admin {admin.id}")
        return _load(conn, employee_id)


@router.delete("/{employee_id}", status_code=204)
def delete_employee(
    employee_id: int, admin: Annotated[Me, Depends(require_admin)]
) -> None:
    """Delete an account created by mistake. Only an employee with no recorded history can go.

    Anyone with punches, requests, leave, attempts or admin decisions stays (deactivate instead), because the
    audit trail is append-only.

    Raises:
        HTTPException: 404 if missing, 409 for oneself or an employee that has history.
    """
    if employee_id == admin.id:
        raise HTTPException(status_code=409, detail=t("err.self_demote"))
    with connect() as conn:
        _load(conn, employee_id)
        try:
            conn.execute("DELETE FROM sessions WHERE employee_id = ?", (employee_id,))
            conn.execute(
                "DELETE FROM employee_sites WHERE employee_id = ?", (employee_id,)
            )
            conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        except (
            sqlite3.IntegrityError
        ) as exc:  # a foreign key points at them: history exists
            raise HTTPException(
                status_code=409, detail=t("err.employee_has_history")
            ) from exc
    logger.warning(f"employee {employee_id} deleted by admin {admin.id}")
