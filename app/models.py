"""Database tables. All timestamps are timezone aware UTC."""
import secrets
from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime | None) -> datetime | None:
    """Accept any datetime from a client. A time with no offset is treated as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(16)


class DropType(str, Enum):
    music = "music"
    merch = "merch"
    tickets = "tickets"
    event = "event"


class DropStatus(str, Enum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"
    sent = "sent"


class Channel(str, Enum):
    email = "email"
    sms = "sms"


class SendStatus(str, Enum):
    queued = "queued"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"


class Artist(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    name: str
    handle: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Drop(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    artist_id: int = Field(foreign_key="artist.id", index=True)
    slug: str = Field(unique=True, index=True)
    title: str
    description: str = ""
    image_url: str | None = None
    type: DropType = DropType.music
    release_at: datetime | None = None
    destination_url: str
    status: DropStatus = Field(default=DropStatus.draft, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Fan(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("artist_id", "email"),
        UniqueConstraint("artist_id", "phone"),
    )
    id: int | None = Field(default=None, primary_key=True)
    artist_id: int = Field(foreign_key="artist.id", index=True)
    email: str | None = Field(default=None, index=True)
    phone: str | None = Field(default=None, index=True)  # E.164, e.g. +14045551234
    city: str | None = None
    email_opt_in: bool = False
    sms_opt_in: bool = False
    unsubscribe_token: str = Field(default_factory=new_token, unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class RSVP(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("drop_id", "fan_id"),)
    id: int | None = Field(default=None, primary_key=True)
    drop_id: int = Field(foreign_key="drop.id", index=True)
    fan_id: int = Field(foreign_key="fan.id", index=True)
    source: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Notification(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("drop_id", "fan_id", "channel"),)
    id: int | None = Field(default=None, primary_key=True)
    drop_id: int = Field(foreign_key="drop.id", index=True)
    fan_id: int = Field(foreign_key="fan.id", index=True)
    channel: Channel
    status: SendStatus = SendStatus.queued
    token: str = Field(default_factory=new_token, unique=True, index=True)  # for click links
    provider_id: str | None = Field(default=None, index=True)
    error: str | None = None
    sent_at: datetime | None = None
    clicked_at: datetime | None = None
