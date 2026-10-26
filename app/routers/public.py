"""Fan facing routes. No auth: anyone with the link can see a drop and RSVP."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, func, select

from app.db import get_session
from app.models import RSVP, Artist, Broadcast, Drop, DropStatus, Fan, Notification, utcnow
from app.schemas import (
    FollowIn, FollowOut, PublicArtistOut, PublicDropOut, PublicDropSummary, RSVPIn, RSVPOut,
)
from app.services import upsert_fan

router = APIRouter(tags=["public"])


def drop_by_slug(slug: str, session: Session) -> Drop:
    drop = session.exec(select(Drop).where(Drop.slug == slug)).first()
    if drop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Drop not found")
    return drop


def is_live(drop: Drop) -> bool:
    return drop.status == DropStatus.sent or (
        drop.release_at is not None and drop.release_at <= utcnow()
    )


@router.get("/d/{slug}", response_model=PublicDropOut)
def public_drop(slug: str, session: Session = Depends(get_session)):
    drop = drop_by_slug(slug, session)
    artist = session.get(Artist, drop.artist_id)
    live = is_live(drop)
    rsvps = session.exec(select(func.count()).select_from(RSVP).where(RSVP.drop_id == drop.id)).one()
    return PublicDropOut(
        slug=drop.slug,
        title=drop.title,
        description=drop.description,
        image_url=drop.image_url,
        type=drop.type,
        artist_name=artist.name,
        artist_handle=artist.handle,
        release_at=drop.release_at,
        is_live=live,
        rsvp_count=rsvps,
        destination_url=drop.destination_url if live else None,
    )


@router.post("/d/{slug}/rsvp", response_model=RSVPOut)
def rsvp(slug: str, body: RSVPIn, session: Session = Depends(get_session)):
    drop = drop_by_slug(slug, session)
    if drop.status in {DropStatus.sending, DropStatus.sent}:
        raise HTTPException(status.HTTP_409_CONFLICT, "This drop is already out")

    fan = upsert_fan(session, drop.artist_id, body.email, body.phone, body.city)

    existing = session.exec(
        select(RSVP).where(RSVP.drop_id == drop.id, RSVP.fan_id == fan.id)
    ).first()
    if existing:
        return RSVPOut(already_rsvped=True, message="You're already on the list")
    session.add(RSVP(drop_id=drop.id, fan_id=fan.id, source=body.source))
    session.commit()
    return RSVPOut(already_rsvped=False, message="You're in. We'll hit you the second it drops.")


def artist_by_handle(handle: str, session: Session) -> Artist:
    artist = session.exec(select(Artist).where(Artist.handle == handle.lower())).first()
    if artist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artist not found")
    return artist


@router.get("/a/{handle}", response_model=PublicArtistOut)
def public_artist(handle: str, session: Session = Depends(get_session)):
    """Data for an artist's public page: who they are and their published drops."""
    artist = artist_by_handle(handle, session)
    drops = session.exec(
        select(Drop)
        .where(Drop.artist_id == artist.id, Drop.status != DropStatus.draft)
        .order_by(Drop.release_at.desc())
    ).all()
    return PublicArtistOut(
        name=artist.name,
        handle=artist.handle,
        drops=[
            PublicDropSummary(
                slug=d.slug, title=d.title, type=d.type, release_at=d.release_at, is_live=is_live(d)
            )
            for d in drops
        ],
    )


@router.post("/a/{handle}/follow", response_model=FollowOut)
def follow(handle: str, body: FollowIn, session: Session = Depends(get_session)):
    """Join an artist's list without RSVPing to a specific drop."""
    artist = artist_by_handle(handle, session)
    upsert_fan(session, artist.id, body.email, body.phone, body.city)
    return FollowOut(message=f"You're on {artist.name}'s list.")


@router.get("/r/{token}", include_in_schema=False)
def track_click(token: str, session: Session = Depends(get_session)):
    note = session.exec(select(Notification).where(Notification.token == token)).first()
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    if note.clicked_at is None:
        note.clicked_at = utcnow()
        session.add(note)
        session.commit()
    if note.drop_id is not None:
        url = session.get(Drop, note.drop_id).destination_url
    else:
        url = session.get(Broadcast, note.broadcast_id).link_url
    if not url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.api_route("/unsubscribe/{token}", methods=["GET", "POST"], response_class=HTMLResponse)
def unsubscribe(token: str, session: Session = Depends(get_session)):
    """GET so the link in an email works with one click; POST for one-click unsubscribe headers."""
    fan = session.exec(select(Fan).where(Fan.unsubscribe_token == token)).first()
    if fan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    fan.email_opt_in = fan.sms_opt_in = False
    session.add(fan)
    session.commit()
    artist = session.get(Artist, fan.artist_id)
    return f"<p>You're unsubscribed from {artist.name}. You won't hear from them again.</p>"
