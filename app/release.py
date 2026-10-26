"""The release flow: claim, fan out notifications, send them.

Drops and broadcasts share one engine. The claim step is the important part:
it flips the job to `sending` with a single conditional UPDATE, so if two
workers (or the scheduler and a "send now" click) race, only one wins and
fans never get messaged twice.
"""
import asyncio
import html
import logging

from sqlalchemy import update
from sqlmodel import Session, select

from app import config, senders
from app.db import engine
from app.models import (
    RSVP, Artist, Broadcast, Channel, Drop, DropStatus, Fan, Notification, SendStatus, utcnow,
)
from app.services import audience_query

log = logging.getLogger("dropkit.release")
BATCH_SIZE = 100


# ---------- shared pieces ----------
def claim(session: Session, model, job_id: int, from_statuses: list[DropStatus]) -> bool:
    result = session.exec(
        update(model)
        .where(model.id == job_id, model.status.in_(from_statuses))
        .values(status=DropStatus.sending)
    )
    session.commit()
    return result.rowcount == 1


def finish(session: Session, job) -> None:
    job.status = DropStatus.sent
    session.add(job)
    session.commit()


def footer_html(artist: Artist, fan: Fan) -> str:
    unsub = f"{config.BASE_URL}/unsubscribe/{fan.unsubscribe_token}"
    return (
        f'<p style="font-size:12px;color:#888">You signed up for updates from '
        f'{html.escape(artist.name)}. <a href="{unsub}">Unsubscribe</a></p>'
    )


def paragraphs(text: str) -> str:
    return "".join(
        f"<p>{html.escape(chunk).replace(chr(10), '<br>')}</p>"
        for chunk in text.strip().split("\n\n") if chunk.strip()
    )


def deliver(session: Session, where, render) -> None:
    """Send every queued notification matching `where`, in batches."""
    while True:
        batch = session.exec(
            select(Notification, Fan)
            .join(Fan, Fan.id == Notification.fan_id)
            .where(where, Notification.status == SendStatus.queued)
            .limit(BATCH_SIZE)
        ).all()
        if not batch:
            break
        for note, fan in batch:
            subject, body = render(fan, note)
            try:
                if note.channel == Channel.email:
                    note.provider_id = senders.send_email(fan.email, subject, body)
                else:
                    note.provider_id = senders.send_sms(fan.phone, body)
                note.status, note.sent_at = SendStatus.sent, utcnow()
            except Exception as exc:  # one bad address shouldn't stop the whole send
                note.status, note.error = SendStatus.failed, str(exc)[:500]
                log.exception("send failed for notification %s", note.id)
            session.add(note)
        session.commit()  # commit per batch so progress survives a crash


# ---------- drops ----------
def queue_drop(session: Session, drop: Drop) -> int:
    """One notification per RSVP'd fan per opted-in channel."""
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


def render_drop(drop: Drop, artist: Artist):
    def render(fan: Fan, note: Notification) -> tuple[str, str]:
        link = f"{config.BASE_URL}/r/{note.token}"
        if note.channel == Channel.sms:
            return "", f"{artist.name}: {drop.title} is out now 🔥 {link}\nReply STOP to opt out"
        body = (
            f"<h1>{html.escape(drop.title)}</h1>{paragraphs(drop.description)}"
            f'<p><a href="{link}">Get it here</a></p>{footer_html(artist, fan)}'
        )
        return f"{drop.title} is out now", body
    return render


def release_drop(drop_id: int, from_statuses: list[DropStatus] | None = None) -> bool:
    """Run the whole flow for one drop. Returns False if someone else already claimed it."""
    with Session(engine) as session:
        if not claim(session, Drop, drop_id, from_statuses or [DropStatus.scheduled]):
            return False
        drop = session.get(Drop, drop_id)
        artist = session.get(Artist, drop.artist_id)
        log.info("drop %s: queued %s notifications", drop.slug, queue_drop(session, drop))
        deliver(session, Notification.drop_id == drop.id, render_drop(drop, artist))
        finish(session, drop)
        return True


# ---------- broadcasts ----------
def broadcast_channels(b: Broadcast) -> list[Channel]:
    return [c for c, on in ((Channel.email, b.send_email), (Channel.sms, b.send_sms)) if on]


def queue_broadcast(session: Session, b: Broadcast) -> int:
    """One notification per opted-in fan per chosen channel, at send time."""
    already = {
        (n.fan_id, n.channel)
        for n in session.exec(select(Notification).where(Notification.broadcast_id == b.id))
    }
    count = 0
    for channel in broadcast_channels(b):
        for fan in session.exec(audience_query(b.artist_id, channel, b.city)):
            if (fan.id, channel) not in already:
                session.add(Notification(broadcast_id=b.id, fan_id=fan.id, channel=channel))
                count += 1
    session.commit()
    return count


def render_broadcast(b: Broadcast, artist: Artist):
    def render(fan: Fan, note: Notification) -> tuple[str, str]:
        link = f"{config.BASE_URL}/r/{note.token}" if b.link_url else None
        if note.channel == Channel.sms:
            text = f"{artist.name}: {b.body.strip()}"
            if link:
                text += f" {link}"
            return "", f"{text}\nReply STOP to opt out"
        button = (
            f'<p><a href="{link}" style="display:inline-block;padding:10px 18px;'
            f'background:#111;color:#fff;border-radius:6px;text-decoration:none">'
            f"{html.escape(b.link_label)}</a></p>"
            if link else ""
        )
        return b.subject, f"{paragraphs(b.body)}{button}{footer_html(artist, fan)}"
    return render


def release_broadcast(broadcast_id: int, from_statuses: list[DropStatus] | None = None) -> bool:
    with Session(engine) as session:
        if not claim(session, Broadcast, broadcast_id, from_statuses or [DropStatus.scheduled]):
            return False
        b = session.get(Broadcast, broadcast_id)
        artist = session.get(Artist, b.artist_id)
        log.info("broadcast %s: queued %s notifications", b.id, queue_broadcast(session, b))
        deliver(session, Notification.broadcast_id == b.id, render_broadcast(b, artist))
        finish(session, b)
        return True


# ---------- scheduler ----------
def run_due_drops() -> int:
    """Fire every scheduled drop and broadcast whose time has come."""
    now = utcnow()
    with Session(engine) as session:
        drops = session.exec(
            select(Drop.id).where(Drop.status == DropStatus.scheduled, Drop.release_at <= now)
        ).all()
        broadcasts = session.exec(
            select(Broadcast.id).where(
                Broadcast.status == DropStatus.scheduled, Broadcast.send_at <= now
            )
        ).all()
    return sum(release_drop(i) for i in drops) + sum(release_broadcast(i) for i in broadcasts)


async def scheduler_loop() -> None:
    while True:
        try:
            fired = await asyncio.to_thread(run_due_drops)
            if fired:
                log.info("scheduler released %s job(s)", fired)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(config.SCHEDULER_INTERVAL_SECONDS)
