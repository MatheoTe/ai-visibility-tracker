"""Password hashing (stdlib scrypt) and JWT issuing/verification."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import uuid

import jwt

_N, _R, _P = 2**14, 8, 1
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "$".join(
        [
            "scrypt",
            str(_N),
            str(_R),
            str(_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def create_access_token(user_id: uuid.UUID, secret: str, ttl_minutes: int) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + dt.timedelta(minutes=ttl_minutes)}
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, secret: str) -> uuid.UUID:
    """Raises ``jwt.PyJWTError`` (or ``ValueError``) when the token is invalid or expired."""
    payload = jwt.decode(token, secret, algorithms=[_ALGORITHM], options={"require": ["sub", "exp"]})
    return uuid.UUID(str(payload["sub"]))
