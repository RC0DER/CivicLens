"""Hashing, tokens, TOTP, and the two intake keys."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_ph = PasswordHasher()


# --------------------------------------------------------------------------- passwords
def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(raw: str, stored: str) -> bool:
    try:
        _ph.verify(stored, raw)
        return True
    except (VerifyMismatchError, Exception):
        return False


def verify_totp(secret: str, code: str) -> bool:
    # valid_window=1 tolerates one 30s step of clock drift, no more.
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def totp_window(at: datetime | None = None) -> int:
    """The 30-second step a code belongs to.

    Stored with the employee code after a successful sign-in so that the same
    code cannot be replayed inside its validity window - the difference
    between second-factor and second-password.
    """
    return int((at or datetime.now(UTC)).timestamp()) // 30


class WeakPassword(ValueError):
    pass


def check_password_policy(raw: str) -> None:
    """Enforced where passwords are set, not where they are checked."""
    s = get_settings()
    if len(raw) < s.min_password_length:
        raise WeakPassword(f"Departmental passwords must be at least {s.min_password_length} characters.")
    classes = sum([
        any(c.islower() for c in raw), any(c.isupper() for c in raw),
        any(c.isdigit() for c in raw), any(not c.isalnum() for c in raw),
    ])
    if classes < 3:
        raise WeakPassword(
            "Use at least three of: lower case, upper case, digits, punctuation."
        )
    if raw.lower() in {"password", "civiclens", "demo-password"} or raw.isdigit():
        raise WeakPassword("That password is guessable. Choose something unrelated to the service.")


def new_totp_secret() -> str:
    return pyotp.random_base32()


# --------------------------------------------------------------------------- JWT
def issue_token(
    subject: str, role: str, department: str | None, minutes: int | None = None
) -> tuple[str, str, datetime]:
    """Returns (token, jti, expiry). The jti is what a sign-out revokes."""
    s = get_settings()
    now = datetime.now(UTC)
    jti = secrets.token_hex(8)
    expires = now + timedelta(minutes=minutes or s.jwt_ttl_minutes)
    payload = {
        "sub": subject,
        "role": role,
        "dept": department,
        "iat": now,
        "exp": expires,
        "jti": jti,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256"), jti, expires


def issue_upload_token(case_no: str) -> str:
    """Short-lived, scoped to one case, so evidence can follow a report that
    was filed without any account."""
    s = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": case_no, "role": "upload", "iat": now,
         "exp": now + timedelta(minutes=s.upload_token_ttl_minutes)},
        s.jwt_secret,
        algorithm="HS256",
    )


def read_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])


# --------------------------------------------------------------------------- intake keys
class IntakeKeysUnavailable(RuntimeError):
    """Raised in any process that was not given the intake keys."""


def intake_token(case_no: str) -> str:
    """The only bridge between a case number and its contact row - and it is
    one-way. Possession of the token does not reveal the case number."""
    s = get_settings()
    if not s.intake_hmac_key:
        raise IntakeKeysUnavailable("no intake HMAC key in this process")
    return hmac.new(s.intake_hmac_key.encode(), case_no.encode(), hashlib.sha256).hexdigest()


def seal_contact(plaintext: str) -> bytes:
    s = get_settings()
    if not s.intake_enc_key:
        raise IntakeKeysUnavailable("no intake encryption key in this process")
    return Fernet(s.intake_enc_key.encode()).encrypt(plaintext.encode())


def open_contact(ciphertext: bytes) -> str | None:
    s = get_settings()
    if not s.intake_enc_key:
        raise IntakeKeysUnavailable("no intake encryption key in this process")
    try:
        return Fernet(s.intake_enc_key.encode()).decrypt(ciphertext).decode()
    except InvalidToken:
        return None


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
