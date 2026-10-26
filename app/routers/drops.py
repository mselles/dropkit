import re
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlmodel import Session, func, select

from app.auth import current_artist
from app.db import get_session
from app.models import RSVP, Artist, Channel, Drop, DropStatus, Notification, SendStatus
from app.release import release_drop
from app.schemas import ChannelStats, DropIn, DropOut, DropPatch, DropStats

router = APIRouter(prefix="/drops", tags=["drops"])
LOCKED = {DropStatus.sending, DropStatus.sent}


def owned_drop(drop_id: int, artist: Artist, session: Session) -> Drop:
    drop = session.get(Drop, drop_id)
    if drop is None or drop.artist_id != artist.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Drop not found")
    return drop


def make_slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "drop"
    return f"{base}-{secrets.token_hex(3)}"


@router.get("", response_model=list[DropOut])
def list_drops(artist: Artist = Depends(current_artist), session: Session = Depends(get_session)):
    return session.exec(
        select(Drop).where(Drop.artist_id == artist.id).order_by(Drop.created_at.desc())
    ).all()


@router.post("", response_model=DropOut, status_code=status.HTTP_201_CREATED)
def create_drop(
    body: DropIn, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    slug = body.slug or make_slug(body.title)
    if session.exec(select(Drop).where(Drop.slug == slug)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "That slug is taken")
    drop = Drop(**body.model_dump(exclude={"slug"}), slug=slug, artist_id=artist.id)
    session.add(drop)
    session.commit()
    session.refresh(drop)
    return drop


@router.get("/{drop_id}", response_model=DropOut)
def get_drop(
    drop_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    return owned_drop(drop_id, artist, session)


@router.patch("/{drop_id}", response_model=DropOut)
def update_drop(
    drop_id: int,
    body: DropPatch,
    artist: Artist = Depends(current_artist),
    session: Session = Depends(get_session),
):
    drop = owned_drop(drop_id, artist, session)
    if drop.status in LOCKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This drop has already gone out")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(drop, key, value)
    session.add(drop)
    session.commit()
    session.refresh(drop)
    return drop


@router.delete("/{drop_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_drop(
    drop_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    drop = owned_drop(drop_id, artist, session)
    if drop.status in LOCKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Can't delete a drop that has gone out")
    for rsvp in session.exec(select(RSVP).where(RSVP.drop_id == drop.id)):
        session.delete(rsvp)
    session.delete(drop)
    session.commit()


@router.post("/{drop_id}/schedule", response_model=DropOut)
def schedule_drop(
    drop_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    drop = owned_drop(drop_id, artist, session)
    if drop.status != DropStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Drop is already {drop.status.value}")
    if drop.release_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Set release_at first")
    drop.status = DropStatus.scheduled
    session.add(drop)
    session.commit()
    session.refresh(drop)
    return drop


@router.post("/{drop_id}/unschedule", response_model=DropOut)
def unschedule_drop(
    drop_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    drop = owned_drop(drop_id, artist, session)
    if drop.status != DropStatus.scheduled:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Drop is {drop.status.value}")
    drop.status = DropStatus.draft
    session.add(drop)
    session.commit()
    session.refresh(drop)
    return drop


@router.post("/{drop_id}/send-now", status_code=status.HTTP_202_ACCEPTED)
def send_now(
    drop_id: int,
    background: BackgroundTasks,
    artist: Artist = Depends(current_artist),
    session: Session = Depends(get_session),
):
    drop = owned_drop(drop_id, artist, session)
    if drop.status in LOCKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This drop has already gone out")
    background.add_task(release_drop, drop.id, [DropStatus.draft, DropStatus.scheduled])
    return {"ok": True, "message": "Sending. Check /stats for progress."}


@router.get("/{drop_id}/stats", response_model=DropStats)
def drop_stats(
    drop_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    drop = owned_drop(drop_id, artist, session)
    rsvps = session.exec(select(func.count()).select_from(RSVP).where(RSVP.drop_id == drop.id)).one()

    per_channel = {Channel.email: ChannelStats(), Channel.sms: ChannelStats()}
    rows = session.exec(
        select(Notification.channel, Notification.status, func.count())
        .where(Notification.drop_id == drop.id)
        .group_by(Notification.channel, Notification.status)
    ).all()
    for channel, send_status, n in rows:
        setattr(per_channel[channel], send_status.value, n)
    clicks = session.exec(
        select(Notification.channel, func.count())
        .where(Notification.drop_id == drop.id, Notification.clicked_at.is_not(None))
        .group_by(Notification.channel)
    ).all()
    for channel, n in clicks:
        per_channel[channel].clicked = n

    reached = sum(s.sent + s.delivered for s in per_channel.values())
    clicked = sum(s.clicked for s in per_channel.values())
    return DropStats(
        rsvps=rsvps,
        email=per_channel[Channel.email],
        sms=per_channel[Channel.sms],
        click_rate=round(clicked / reached, 3) if reached else 0.0,
    )
