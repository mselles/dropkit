from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app import config
from app.db import get_session
from app.models import Artist

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(artist_id: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(artist_id), "exp": expires}, config.JWT_SECRET, algorithm="HS256")


def current_artist(
    token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)
) -> Artist:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Invalid or expired token", {"WWW-Authenticate": "Bearer"}
    )
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        artist = session.get(Artist, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if artist is None:
        raise unauthorized
    return artist
