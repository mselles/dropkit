"""Callbacks from Twilio. Point your Twilio number's webhooks at these.

TODO before going live: verify the X-Twilio-Signature header
(twilio.request_validator.RequestValidator) so nobody can fake these.
"""
from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response
from sqlmodel import Session, select

from app.db import get_session
from app.models import Fan, Notification, SendStatus

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_WORDS = {"start", "unstop", "yes"}
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

TWILIO_STATUS = {
    "sent": SendStatus.sent,
    "delivered": SendStatus.delivered,
    "failed": SendStatus.failed,
    "undelivered": SendStatus.failed,
}


@router.post("/twilio/inbound")
def twilio_inbound(
    From: str = Form(...), Body: str = Form(""), session: Session = Depends(get_session)
):
    word = Body.strip().lower()
    if word in STOP_WORDS or word in START_WORDS:
        # Opt outs apply to every artist this number signed up with.
        for fan in session.exec(select(Fan).where(Fan.phone == From)):
            fan.sms_opt_in = word in START_WORDS
            session.add(fan)
        session.commit()
    return Response(EMPTY_TWIML, media_type="application/xml")


@router.post("/twilio/status")
def twilio_status(
    MessageSid: str = Form(...),
    MessageStatus: str = Form(...),
    ErrorCode: str | None = Form(None),
    session: Session = Depends(get_session),
):
    new_status = TWILIO_STATUS.get(MessageStatus)
    note = session.exec(select(Notification).where(Notification.provider_id == MessageSid)).first()
    if note and new_status:
        note.status = new_status
        if ErrorCode:
            note.error = f"twilio error {ErrorCode}"
        session.add(note)
        session.commit()
    return Response(status_code=204)
