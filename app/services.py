"""Logic shared by more than one router."""
from sqlalchemy import ColumnElement
from sqlmodel import Session, func, select

from app.models import Channel, Fan, Notification
from app.schemas import ChannelStats, SendStats


def upsert_fan(
    session: Session, artist_id: int, email: str | None, phone: str | None, city: str | None
) -> Fan:
    """Find this person in the artist's list (by email or phone) or add them.

    Signing up again counts as a fresh opt in for whichever channels they gave.
    """
    email = email.lower() if email else None
    by_email = (
        session.exec(select(Fan).where(Fan.artist_id == artist_id, Fan.email == email)).first()
        if email else None
    )
    by_phone = (
        session.exec(select(Fan).where(Fan.artist_id == artist_id, Fan.phone == phone)).first()
        if phone else None
    )
    fan = by_email or by_phone or Fan(artist_id=artist_id)

    if email:
        fan.email, fan.email_opt_in = email, True
    if phone:
        if by_phone is not None and by_phone is not fan:
            # The phone already belongs to a different fan record. Opt that one in
            # instead of moving the number, which would break the unique constraint.
            by_phone.sms_opt_in = True
            session.add(by_phone)
        else:
            fan.phone, fan.sms_opt_in = phone, True
    if city:
        fan.city = city
    session.add(fan)
    session.commit()
    session.refresh(fan)
    return fan


def audience_query(artist_id: int, channel: Channel, city: str | None):
    """Fans of this artist who can be reached on this channel."""
    query = select(Fan).where(Fan.artist_id == artist_id)
    if channel == Channel.email:
        query = query.where(Fan.email.is_not(None), Fan.email_opt_in)
    else:
        query = query.where(Fan.phone.is_not(None), Fan.sms_opt_in)
    if city:
        query = query.where(Fan.city.ilike(city))
    return query


def send_stats(session: Session, where: ColumnElement) -> SendStats:
    """Roll up notifications matching `where` (one drop, or one broadcast)."""
    per_channel = {Channel.email: ChannelStats(), Channel.sms: ChannelStats()}
    rows = session.exec(
        select(Notification.channel, Notification.status, func.count())
        .where(where)
        .group_by(Notification.channel, Notification.status)
    ).all()
    for channel, send_status, n in rows:
        setattr(per_channel[channel], send_status.value, n)
    clicks = session.exec(
        select(Notification.channel, func.count())
        .where(where, Notification.clicked_at.is_not(None))
        .group_by(Notification.channel)
    ).all()
    for channel, n in clicks:
        per_channel[channel].clicked = n

    reached = sum(s.sent + s.delivered for s in per_channel.values())
    clicked = sum(s.clicked for s in per_channel.values())
    return SendStats(
        email=per_channel[Channel.email],
        sms=per_channel[Channel.sms],
        click_rate=round(clicked / reached, 3) if reached else 0.0,
    )
