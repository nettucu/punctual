"""Password hashing and session tokens."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher: PasswordHasher = PasswordHasher()
MIN_PASSWORD_LEN: int = 12


def check_password_policy(password: str) -> str:
    """Validate the password rules.

    Args:
        password: Plaintext password.

    Returns:
        The same password.

    Raises:
        ValueError: If shorter than MIN_PASSWORD_LEN or missing an uppercase letter, a digit or a special character.
    """
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if not any(c.isupper() for c in password):
        raise ValueError("password needs an uppercase letter")
    if not any(c.isdigit() for c in password):
        raise ValueError("password needs a digit")
    if all(c.isalnum() for c in password):
        raise ValueError("password needs a special character")
    return password


def hash_password(password: str) -> str:
    """Hash a password with argon2id.

    Args:
        password: Plaintext password.

    Returns:
        Encoded hash.

    Raises:
        ValueError: If the password breaks the policy (see check_password_policy).
    """
    return _hasher.hash(check_password_policy(password))


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password against its hash.

    Args:
        password_hash: Stored hash.
        password: Candidate plaintext.

    Returns:
        True on match.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    """Return a fresh 256-bit URL-safe session token."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Hash a session token for storage (DB leak must not yield live sessions).

    Args:
        token: Raw token from the cookie.

    Returns:
        SHA-256 hex digest.
    """
    return hashlib.sha256(token.encode()).hexdigest()
