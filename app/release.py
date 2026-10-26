"""The release flow: claim a drop, fan out notifications, send them.

The claim step is the important one. It flips a drop to `sending` with a
single conditional UPDATE, so if two workers (or the scheduler and a
"send now" click) race, only one wins and fans never get texted twice.
"""
import asyncio
import logging

from sqlalchemy import update
from sqlmodel import Session, select

from app import config, senders
from app.db import engine
from app.models import (
    RSVP, Artist, Channel, Drop, DropStatus, Fan, Notification, SendStatus, utcnow,
)

log = logging.getLogger("dropkit.release")
BATCH_SIZE = 100


def claim_drop(session: Session, drop_id: int, from_statuses: list[DropStatus]) -> bool:
    result = session.exec(
        update(Drop)
        .where(Drop.id == drop_id, Drop.status.in_(from_statuses))
        .values(status=DropStatus.sending)
    )
    session.commit()
    return result.rowcount == 1


def queue_notifications(session: Session, drop: Drop) -> int:
    """One notifications row per RSVP'd fan per opted-in channel."""
    fans = session.exec(
        select(Fan).join(RSVP, RSVP.fan_id == Fan.id).where(RSVP.drop_id == drop.id)
    ).all()
    already = {
        (n.fan_id, n.channel)
        for n in session.exec(select(Notification).where(Notification.drop_id == drop.id))
    }
    count = 0
    for fan in fans:
        for channel, wanted in ((Channel.email, fan.email and fan.email_opt_in),
                                (Channel.sms, fan.phone and fan.sms_opt_in)):
            if wanted and (fan.id, channel) not in already:
                session.add(Notification(drop_id=drop.id, fan_id=fan.id, channel=channel))
                count += 1
    session.commit()
    return count


def render(drop: Drop, artist: Artist, fan: Fan, note: Notification) -> tuple[str, str]:
    """Return (subject, body) for this notification."""
    link = f"{config.BASE_URL}/r/{note.token}"
    if note.channel == Channel.sms:
        return "", f"{artist.name}: {drop.title} is out now 🔥 {link}\nReply STOP to opt out"
    unsub = f"{config.BASE_URL}/unsubscribe/{fan.unsubscribe_token}"
    subject = f"{drop.title} is out now"
    html = (
        f"<h1>{drop.title}</h1><p>{drop.description}</p>"
        f'<p><a href="{link}">Get it here</a></p>'
        f'<p style="font-size:12px;color:#888"><a href="{unsub}">Unsubscribe</a> '
        f"from {artist.name}</p>"
    )
    return subject, html


def deliver(session: Session, drop: Drop) -> None:
    artist = session.get(Artist, drop.artist_id)
    while True:
        batch = session.exec(
            select(Notification, Fan)
            .join(Fan, Fan.id == Notification.fan_id)
            .where(Notification.drop_id == drop.id, Notification.status == SendStatus.queued)
            .limit(BATCH_SIZE)
        ).all()
        if not batch:
            break
        for note, fan in batch:
            subject, body = render(drop, artist, fan, note)
            try:
                if note.channel == Channel.email:
                    note.provider_id = senders.send_email(fan.email, subject, body)
                else:
                    note.provider_id = senders.send_sms(fan.phone, body)
                note.status, note.sent_at = SendStatus.sent, utcnow()
            except Exception as exc:  # one bad number shouldn't stop the drop
                note.status, note.error = SendStatus.failed, str(exc)[:500]
                log.exception("send failed for notification %s", note.id)
            session.add(note)
        session.commit()  # commit per batch so progress survives a crash


def release_drop(drop_id: int, from_statuses: list[DropStatus] | None = None) -> bool:
    """Run the whole flow for one drop. Returns False if someone else already claimed it."""
    from_statuses = from_statuses or [DropStatus.scheduled]
    with Session(engine) as session:
        if not claim_drop(session, drop_id, from_statuses):
            return False
        drop = session.get(Drop, drop_id)
        queued = queue_notifications(session, drop)
        log.info("drop %s: queued %s notifications", drop.slug, queued)
        deliver(session, drop)
        drop.status = DropStatus.sent
        session.add(drop)
        session.commit()
        return True


def run_due_drops() -> int:
    """Fire every scheduled drop whose release time has passed."""
    with Session(engine) as session:
        due = session.exec(
            select(Drop.id).where(Drop.status == DropStatus.scheduled, Drop.release_at <= utcnow())
        ).all()
    return sum(release_drop(drop_id) for drop_id in due)


async def scheduler_loop() -> None:
    while True:
        try:
            fired = await asyncio.to_thread(run_due_drops)
            if fired:
                log.info("scheduler released %s drop(s)", fired)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(config.SCHEDULER_INTERVAL_SECONDS)
