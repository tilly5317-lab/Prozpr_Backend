"""Shared utility — `security.py`.

Small, reusable helpers (security, formatting) with no business workflow; safe to import from routers, services, or scripts.
"""


from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status

from app.config import get_settings


def _password_bytes(password: str) -> bytes:
    raw = password.encode("utf-8")
    return raw[:72] if len(raw) > 72 else raw


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_password_bytes(plain), hashed.encode("utf-8"))


def create_access_token(user_id: uuid.UUID, phone: str) -> str:
    secret = get_settings().get_jwt_secret()
    payload = {
        "sub": str(user_id),
        "phone": phone,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    secret = get_settings().get_jwt_secret()
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_fernet():
    from cryptography.fernet import Fernet

    key = get_settings().get_encryption_key()
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError as e:
        raise RuntimeError(
            "ENCRYPTION_KEY must be a valid Fernet key (32 url-safe base64-encoded bytes)."
        ) from e
