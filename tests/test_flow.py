import re
from datetime import datetime, timedelta, timezone

from app import senders
from app.release import run_due_drops


def make_drop(client, headers, **extra):
    body = {"title": "New Single", "destination_url": "https://open.spotify.com/track/abc", **extra}
    r = client.post("/drops", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_login_and_me(client, artist):
    r = client.post("/auth/login", data={"username": "miles@example.com", "password": "supersecret"})
    assert r.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["handle"] == "testartist"
    assert client.post("/auth/login", data={"username": "miles@example.com", "password": "nope"}).status_code == 401


def test_full_drop_flow(client, artist):
    drop = make_drop(client, artist)
    slug = drop["slug"]

    page = client.get(f"/d/{slug}").json()
    assert page["is_live"] is False and page["destination_url"] is None

    assert client.post(f"/d/{slug}/rsvp", json={"email": "fan1@example.com", "city": "Atlanta"}).json()["already_rsvped"] is False
    assert client.post(f"/d/{slug}/rsvp", json={"phone": "(404) 555-1234", "sms_consent": True}).status_code == 200
    assert client.post(f"/d/{slug}/rsvp", json={"email": "FAN1@example.com"}).json()["already_rsvped"] is True
    assert client.get(f"/d/{slug}").json()["rsvp_count"] == 2

    assert client.post(f"/drops/{drop['id']}/send-now", headers=artist).status_code == 202
    assert {m["channel"] for m in senders.outbox} == {"email", "sms"}
    sms = next(m for m in senders.outbox if m["channel"] == "sms")
    assert sms["to"] == "+14045551234" and "Reply STOP" in sms["body"]

    stats = client.get(f"/drops/{drop['id']}/stats", headers=artist).json()
    assert stats["rsvps"] == 2 and stats["email"]["sent"] == 1 and stats["sms"]["sent"] == 1

    # Click tracking redirects to the destination and counts once.
    link = re.search(r"http://localhost:8000(/r/\S+)", sms["body"]).group(1)
    r = client.get(link, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == drop["destination_url"]
    assert client.get(f"/drops/{drop['id']}/stats", headers=artist).json()["click_rate"] == 0.5

    # No double sends, and the drop is now locked.
    assert client.post(f"/drops/{drop['id']}/send-now", headers=artist).status_code == 409
    assert len(senders.outbox) == 2
    assert client.get(f"/d/{slug}").json()["destination_url"] == drop["destination_url"]


def test_rsvp_validation(client, artist):
    slug = make_drop(client, artist)["slug"]
    assert client.post(f"/d/{slug}/rsvp", json={}).status_code == 422
    assert client.post(f"/d/{slug}/rsvp", json={"phone": "4045551234"}).status_code == 422  # no consent
    assert client.post(f"/d/{slug}/rsvp", json={"phone": "123", "sms_consent": True}).status_code == 422
    assert client.get("/d/does-not-exist").status_code == 404


def test_scheduler_fires_only_due_drops(client, artist):
    now = datetime.now(timezone.utc)
    past = make_drop(client, artist, title="Past", release_at=(now - timedelta(minutes=1)).isoformat())
    future = make_drop(client, artist, title="Future", release_at=(now + timedelta(days=1)).isoformat())
    for d in (past, future):
        client.post(f"/d/{d['slug']}/rsvp", json={"email": "fan@example.com"})
        assert client.post(f"/drops/{d['id']}/schedule", headers=artist).json()["status"] == "scheduled"

    assert run_due_drops() == 1
    assert run_due_drops() == 0  # already sent, never fires twice
    assert client.get(f"/drops/{past['id']}", headers=artist).json()["status"] == "sent"
    assert client.get(f"/drops/{future['id']}", headers=artist).json()["status"] == "scheduled"
    assert len(senders.outbox) == 1


def test_opt_outs(client, artist):
    drop = make_drop(client, artist)
    client.post(f"/d/{drop['slug']}/rsvp", json={"email": "a@example.com", "phone": "4045550000", "sms_consent": True})

    # Texting STOP turns off SMS but not email.
    r = client.post("/webhooks/twilio/inbound", data={"From": "+14045550000", "Body": "STOP"})
    assert r.status_code == 200
    client.post(f"/drops/{drop['id']}/send-now", headers=artist)
    assert [m["channel"] for m in senders.outbox] == ["email"]

    token = senders.outbox[0]["body"].split("/unsubscribe/")[1].split('"')[0]
    assert "unsubscribed" in client.get(f"/unsubscribe/{token}").text
    fans = client.get("/fans", headers=artist).json()
    assert fans[0]["email_opt_in"] is False and fans[0]["sms_opt_in"] is False


def test_artists_cannot_touch_each_others_drops(client, artist):
    drop = make_drop(client, artist)
    other = client.post("/auth/signup", json={
        "email": "other@example.com", "password": "supersecret", "name": "Other", "handle": "other",
    }).json()["access_token"]
    h = {"Authorization": f"Bearer {other}"}
    assert client.get(f"/drops/{drop['id']}", headers=h).status_code == 404
    assert client.post(f"/drops/{drop['id']}/send-now", headers=h).status_code == 404
    assert client.get("/drops", headers={"Authorization": "Bearer junk"}).status_code == 401
