"""Request and response shapes (what the API accepts and returns)."""
import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models import Channel, DropStatus, DropType, SendStatus, to_utc


# ---------- auth ----------
class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=80)
    handle: str = Field(pattern=r"^[a-z0-9_]{2,30}$", description="lowercase, used in URLs")


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ArtistOut(BaseModel):
    id: int
    email: str
    name: str
    handle: str


# ---------- drops ----------
class DropIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    image_url: str | None = None
    type: DropType = DropType.music
    release_at: datetime | None = None
    destination_url: str
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9-]{3,60}$")

    _utc = field_validator("release_at")(lambda cls, v: to_utc(v))


class DropPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    type: DropType | None = None
    release_at: datetime | None = None
    destination_url: str | None = None

    _utc = field_validator("release_at")(lambda cls, v: to_utc(v))


class DropOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    image_url: str | None
    type: DropType
    release_at: datetime | None
    destination_url: str
    status: DropStatus
    created_at: datetime


class ChannelStats(BaseModel):
    queued: int = 0
    sent: int = 0
    delivered: int = 0
    failed: int = 0
    clicked: int = 0


class SendStats(BaseModel):
    email: ChannelStats
    sms: ChannelStats
    click_rate: float


class DropStats(SendStats):
    rsvps: int


# ---------- public ----------
class PublicDropOut(BaseModel):
    slug: str
    title: str
    description: str
    image_url: str | None
    type: DropType
    artist_name: str
    artist_handle: str
    release_at: datetime | None
    is_live: bool
    rsvp_count: int
    destination_url: str | None  # only revealed once the drop is live


class RSVPIn(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    city: str | None = None
    sms_consent: bool = False  # must be an explicit checkbox in your UI
    source: str | None = None

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        digits = re.sub(r"\D", "", v)
        if len(digits) == 10:  # assume US
            digits = "1" + digits
        if not 11 <= len(digits) <= 15:
            raise ValueError("phone number looks invalid")
        return "+" + digits

    @model_validator(mode="after")
    def need_contact(self):
        if not self.email and not self.phone:
            raise ValueError("provide an email or a phone number")
        if self.phone and not self.sms_consent:
            raise ValueError("sms_consent must be true to sign up by text")
        return self


class RSVPOut(BaseModel):
    ok: bool = True
    already_rsvped: bool
    message: str


# ---------- fans ----------
class FanOut(BaseModel):
    id: int
    email: str | None
    phone: str | None
    city: str | None
    email_opt_in: bool
    sms_opt_in: bool
    created_at: datetime


class NotificationOut(BaseModel):
    channel: Channel
    status: SendStatus
    sent_at: datetime | None
    clicked_at: datetime | None


# ---------- follow (public artist page) ----------
class PublicDropSummary(BaseModel):
    slug: str
    title: str
    type: DropType
    release_at: datetime | None
    is_live: bool


class PublicArtistOut(BaseModel):
    name: str
    handle: str
    drops: list[PublicDropSummary]


class FollowIn(RSVPIn):
    """Same fields and rules as an RSVP: email and/or phone, SMS needs consent."""


class FollowOut(BaseModel):
    ok: bool = True
    message: str


# ---------- broadcasts ----------
class BroadcastIn(BaseModel):
    subject: str = Field(min_length=1, max_length=150, description="email subject line")
    body: str = Field(min_length=1, max_length=10000, description="plain text; blank lines become paragraphs")
    link_url: str | None = None
    link_label: str = "Check it out"
    send_email: bool = True
    send_sms: bool = False
    city: str | None = Field(None, description="only fans in this city")
    send_at: datetime | None = None

    _utc = field_validator("send_at")(lambda cls, v: to_utc(v))

    @model_validator(mode="after")
    def need_channel(self):
        if not self.send_email and not self.send_sms:
            raise ValueError("pick at least one channel (send_email or send_sms)")
        return self


class BroadcastPatch(BaseModel):
    subject: str | None = Field(None, min_length=1, max_length=150)
    body: str | None = Field(None, min_length=1, max_length=10000)
    link_url: str | None = None
    link_label: str | None = None
    send_email: bool | None = None
    send_sms: bool | None = None
    city: str | None = None
    send_at: datetime | None = None

    _utc = field_validator("send_at")(lambda cls, v: to_utc(v))


class BroadcastOut(BaseModel):
    id: int
    subject: str
    body: str
    link_url: str | None
    link_label: str
    send_email: bool
    send_sms: bool
    city: str | None
    send_at: datetime | None
    status: DropStatus
    created_at: datetime


class AudienceOut(BaseModel):
    email: int
    sms: int
    total_messages: int
