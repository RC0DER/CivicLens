"""Hashing, tokens, TOTP, and the two intake keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

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


# --------------------------------------------------------------------------- sealing
# Contacts are sealed with a public key and opened with a private one, so the
# service that collects them cannot read them back.
#
# A symmetric scheme cannot express that: one key both seals and opens, so the
# public service - the internet-facing one, and the likeliest to be
# compromised - would be able to decrypt every contact it has ever taken. Here
# it holds only the sealing half. Reading a contact is mathematically out of
# reach for it, not merely forbidden by a check it could be tricked past.
#
# This is a sealed box (libsodium's crypto_box_seal): an ephemeral X25519
# keypair per message, ECDH against the recipient's public key, HKDF to an
# AES-256-GCM key. The ephemeral private key is discarded immediately, so even
# the sealing process cannot reverse its own work.
_SEAL_VERSION = b"CL1"
_INFO = b"civiclens-intake-contact"


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode())


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def _derive(shared: bytes, ephemeral_public: bytes, recipient_public: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=_INFO + ephemeral_public + recipient_public,
    ).derive(shared)


def seal_contact(plaintext: str) -> bytes:
    """Seal a contact to the investigator's public key."""
    s = get_settings()
    if not s.intake_seal_key:
        raise IntakeKeysUnavailable("no intake sealing key in this process")

    recipient_raw = _b64d(s.intake_seal_key)
    recipient = X25519PublicKey.from_public_bytes(recipient_raw)

    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes_raw()
    key = _derive(ephemeral.exchange(recipient), ephemeral_public, recipient_raw)

    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _SEAL_VERSION)
    return _SEAL_VERSION + ephemeral_public + nonce + ciphertext


def open_contact(sealed: bytes) -> str | None:
    """Open a sealed contact. Requires the private half, which only the
    investigator service is given."""
    s = get_settings()
    if not s.intake_open_key:
        raise IntakeKeysUnavailable("no intake opening key in this process")

    if not sealed.startswith(_SEAL_VERSION):
        return None
    body = sealed[len(_SEAL_VERSION):]
    ephemeral_public, nonce, ciphertext = body[:32], body[32:44], body[44:]

    private = X25519PrivateKey.from_private_bytes(_b64d(s.intake_open_key))
    recipient_public = private.public_key().public_bytes_raw()
    key = _derive(
        private.exchange(X25519PublicKey.from_public_bytes(ephemeral_public)),
        ephemeral_public, recipient_public,
    )
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _SEAL_VERSION).decode()
    except InvalidTag:
        return None


def generate_intake_keypair() -> tuple[str, str]:
    """Returns (seal_key, open_key) - the public and private halves.

    The seal key goes to the public service; the open key goes only to the
    investigator service.
    """
    private = X25519PrivateKey.generate()
    return _b64e(private.public_key().public_bytes_raw()), _b64e(private.private_bytes_raw())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
