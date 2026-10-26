"""Email and SMS providers.

With no API keys set, messages print to the console and land in `outbox`,
so you can build and test the whole flow without paying for anything.
"""
import logging
import secrets

import httpx

from app import config

log = logging.getLogger("dropkit.senders")

# Every console send is recorded here (handy in tests and while developing).
outbox: list[dict] = []


class SendError(Exception):
    pass


def send_email(to: str, subject: str, html: str) -> str:
    """Send an email and return the provider's message id."""
    if not config.RESEND_API_KEY:
        return _console("email", to, f"{subject}\n{html}")
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
        json={"from": config.EMAIL_FROM, "to": [to], "subject": subject, "html": html},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SendError(f"Resend {resp.status_code}: {resp.text[:200]}")
    return resp.json()["id"]


def send_sms(to: str, body: str) -> str:
    """Send a text and return the provider's message id."""
    if not config.TWILIO_ACCOUNT_SID:
        return _console("sms", to, body)
    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_ACCOUNT_SID}/Messages.json",
        auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
        data={
            "From": config.TWILIO_FROM_NUMBER,
            "To": to,
            "Body": body,
            "StatusCallback": f"{config.BASE_URL}/webhooks/twilio/status",
        },
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SendError(f"Twilio {resp.status_code}: {resp.text[:200]}")
    return resp.json()["sid"]


def _console(channel: str, to: str, body: str) -> str:
    provider_id = f"console_{secrets.token_hex(6)}"
    outbox.append({"channel": channel, "to": to, "body": body, "provider_id": provider_id})
    log.warning("[%s → %s]\n%s\n", channel.upper(), to, body)
    return provider_id
