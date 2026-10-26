from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, or_, select

from app.auth import create_token, current_artist, hash_password, verify_password
from app.db import get_session
from app.models import Artist
from app.schemas import ArtistOut, SignupIn, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, session: Session = Depends(get_session)):
    taken = session.exec(
        select(Artist).where(or_(Artist.email == body.email.lower(), Artist.handle == body.handle))
    ).first()
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email or handle already in use")
    artist = Artist(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        name=body.name,
        handle=body.handle,
    )
    session.add(artist)
    session.commit()
    session.refresh(artist)
    return TokenOut(access_token=create_token(artist.id))


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    """Standard OAuth2 form (username = your email), so the Authorize button in /docs works."""
    artist = session.exec(select(Artist).where(Artist.email == form.username.lower())).first()
    if not artist or not verify_password(form.password, artist.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return TokenOut(access_token=create_token(artist.id))


@router.get("/me", response_model=ArtistOut)
def me(artist: Artist = Depends(current_artist)):
    return artist
