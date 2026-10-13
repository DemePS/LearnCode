"""Email + password auth: bcrypt-hashed passwords, a JWT bearer token the frontend sends as
Authorization: Bearer <token> on every /api/learn request."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import db

SECRET_KEY = os.environ.get("LEARNCODE_SECRET_KEY")
if not SECRET_KEY:
    # Fine for local dev; set LEARNCODE_SECRET_KEY in any real deployment -- without it, every
    # process restart invalidates every issued token (a fresh random key each time) and signs out
    # everyone, and two backend instances wouldn't honor each other's tokens at all.
    SECRET_KEY = secrets.token_hex(32)

ALGORITHM = "HS256"
TOKEN_TTL_DAYS = 30

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(db.get_db),
) -> db.User:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    user_id = decode_token(creds.credentials)
    if user_id is None:
        raise HTTPException(401, "Invalid or expired token")
    user = session.get(db.User, user_id)
    if user is None:
        raise HTTPException(401, "User no longer exists")
    return user
