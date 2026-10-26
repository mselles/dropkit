from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.auth import current_artist
from app.db import get_session
from app.models import RSVP, Artist, Channel, Fan, Notification
from app.schemas import FanOut, NotificationOut

router = APIRouter(prefix="/fans", tags=["fans"])


@router.get("", response_model=list[FanOut])
def list_fans(
    city: str | None = None,
    channel: Channel | None = Query(None, description="only fans reachable on this channel"),
    drop_id: int | None = Query(None, description="only fans who RSVP'd to this drop"),
    limit: int = Query(100, le=1000),
    offset: int = 0,
    artist: Artist = Depends(current_artist),
    session: Session = Depends(get_session),
):
    query = select(Fan).where(Fan.artist_id == artist.id)
    if city:
        query = query.where(Fan.city.ilike(city))
    if channel == Channel.email:
        query = query.where(Fan.email_opt_in)
    elif channel == Channel.sms:
        query = query.where(Fan.sms_opt_in)
    if drop_id is not None:
        query = query.join(RSVP, RSVP.fan_id == Fan.id).where(RSVP.drop_id == drop_id)
    return session.exec(query.order_by(Fan.created_at.desc()).offset(offset).limit(limit)).all()


@router.get("/{fan_id}/history", response_model=list[NotificationOut])
def fan_history(
    fan_id: int, artist: Artist = Depends(current_artist), session: Session = Depends(get_session)
):
    fan = session.get(Fan, fan_id)
    if fan is None or fan.artist_id != artist.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fan not found")
    return session.exec(select(Notification).where(Notification.fan_id == fan.id)).all()
