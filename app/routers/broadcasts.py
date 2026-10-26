"""Newsletter style messages to an artist's whole list (or a slice of it)."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlmodel import Session, func, select

from app.auth import current_artist
from app.db import get_session
from app.models import Artist, Broadcast, Channel, DropStatus, Notification
from app.release import release_broadcast
from app.schemas import AudienceOut, BroadcastIn, BroadcastOut, BroadcastPatch, SendStats
from app.services import audience_query, send_stats

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])
LOCKED = {DropStatus.sending, DropStatus.sent}


def owned_broadcast(broadcast_id: int, artist: Artist, session: Session) -> Broadcast:
    b = session.get(Broadcast, broadcast_id)
    if b is None or b.artist_id != artist.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broadcast not found")
    return b


def editable(b: Broadcast) -> None:
    if b.status in LOCKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This broadcast has already gone out")


def count(session: Session, artist_id: int, channel: Channel, city: str | None) -> int:
    sub = audience_query(artist_id, channel, city).subquery()
    return session.exec(select(func.count()).select_from(sub)).one()


@router.get("", response_model=list[BroadcastOut])
def list_broadcasts(
    artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    return session.exec(
        select(Broadcast).where(Broadcast.artist_id == artist.id).order_by(Broadcast.created_at.desc())
    ).all()


@router.post("", response_model=BroadcastOut, status_code=status.HTTP_201_CREATED)
def create_broadcast(
    body: BroadcastIn, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    """Creates a draft. Nothing sends until you call /schedule or /send-now."""
    b = Broadcast(**body.model_dump(), artist_id=artist.id)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


@router.get("/audience", response_model=AudienceOut)
def preview_audience(
    send_email: bool = True,
    send_sms: bool = False,
    city: str | None = None,
    artist: Artist = Depends(current_artist),
    session: Session = Depends(get_session),
):
    """How many people a broadcast with these settings would reach. Handy for a composer UI."""
    email = count(session, artist.id, Channel.email, city) if send_email else 0
    sms = count(session, artist.id, Channel.sms, city) if send_sms else 0
    return AudienceOut(email=email, sms=sms, total_messages=email + sms)


@router.get("/{broadcast_id}", response_model=BroadcastOut)
def get_broadcast(
    broadcast_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    return owned_broadcast(broadcast_id, artist, session)


@router.patch("/{broadcast_id}", response_model=BroadcastOut)
def update_broadcast(
    broadcast_id: int,
    body: BroadcastPatch,
    artist: Artist = Depends(current_artist),
    session: Session = Depends(get_session),
):
    b = owned_broadcast(broadcast_id, artist, session)
    editable(b)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(b, key, value)
    if not b.send_email and not b.send_sms:
        raise HTTPException(422, "Pick at least one channel")
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


@router.delete("/{broadcast_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_broadcast(
    broadcast_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    b = owned_broadcast(broadcast_id, artist, session)
    editable(b)
    session.delete(b)
    session.commit()


@router.post("/{broadcast_id}/schedule", response_model=BroadcastOut)
def schedule_broadcast(
    broadcast_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    b = owned_broadcast(broadcast_id, artist, session)
    if b.status != DropStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Broadcast is already {b.status.value}")
    if b.send_at is None:
        raise HTTPException(422, "Set send_at first")
    b.status = DropStatus.scheduled
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


@router.post("/{broadcast_id}/unschedule", response_model=BroadcastOut)
def unschedule_broadcast(
    broadcast_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    b = owned_broadcast(broadcast_id, artist, session)
    if b.status != DropStatus.scheduled:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Broadcast is {b.status.value}")
    b.status = DropStatus.draft
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


@router.post("/{broadcast_id}/send-now", status_code=status.HTTP_202_ACCEPTED)
def send_now(
    broadcast_id: int,
    background: BackgroundTasks,
    artist: Artist = Depends(current_artist),
    session: Session = Depends(get_session),
):
    b = owned_broadcast(broadcast_id, artist, session)
    editable(b)
    background.add_task(release_broadcast, b.id, [DropStatus.draft, DropStatus.scheduled])
    return {"ok": True, "message": "Sending. Check /stats for progress."}


@router.get("/{broadcast_id}/stats", response_model=SendStats)
def broadcast_stats(
    broadcast_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    b = owned_broadcast(broadcast_id, artist, session)
    return send_stats(session, Notification.broadcast_id == b.id)
