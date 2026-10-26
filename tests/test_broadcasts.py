import re
from datetime import datetime, timedelta, timezone

from app import senders
from app.release import run_due_drops


def follow(client, **body):
    r = client.post("/a/testartist/follow", json=body)
    assert r.status_code == 200, r.text
    return r


def test_follow_and_public_page(client, artist):
    assert "Test Artist" in follow(client, email="fan@example.com").json()["message"]
    follow(client, email="FAN@example.com", city="Atlanta")  # same person, no duplicate
    fans = client.get("/fans", headers=artist).json()
    assert len(fans) == 1 and fans[0]["city"] == "Atlanta"

    assert client.post("/a/nobody/follow", json={"email": "x@example.com"}).status_code == 404
    assert client.post("/a/testartist/follow", json={"phone": "4045551234"}).status_code == 422

    # Drafts stay hidden on the public page; scheduled drops show up.
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    d = client.post("/drops", json={"title": "Secret", "destination_url": "https://x.co", "release_at": soon}, headers=artist).json()
    assert client.get("/a/testartist").json()["drops"] == []
    client.post(f"/drops/{d['id']}/schedule", headers=artist)
    page = client.get("/a/testartist").json()
    assert page["name"] == "Test Artist" and page["drops"][0]["is_live"] is False


def test_broadcast_to_whole_list(client, artist):
    follow(client, email="atl@example.com", city="Atlanta")
    follow(client, email="la@example.com", phone="3105550000", sms_consent=True, city="Los Angeles")
    # A fan who only RSVP'd to a drop is still on the list.
    d = client.post("/drops", json={"title": "Tape", "destination_url": "https://x.co"}, headers=artist).json()
    client.post(f"/d/{d['slug']}/rsvp", json={"email": "rsvp@example.com"})

    aud = client.get("/broadcasts/audience?send_sms=true", headers=artist).json()
    assert aud == {"email": 3, "sms": 1, "total_messages": 4}
    assert client.get("/broadcasts/audience?city=atlanta", headers=artist).json()["email"] == 1

    b = client.post("/broadcasts", json={
        "subject": "Tour dates <3",
        "body": "Big news.\n\nWe're going on tour.",
        "link_url": "https://tickets.example.com",
        "send_sms": True,
    }, headers=artist).json()
    assert b["status"] == "draft" and senders.outbox == []  # drafts never send

    assert client.post(f"/broadcasts/{b['id']}/send-now", headers=artist).status_code == 202
    emails = [m for m in senders.outbox if m["channel"] == "email"]
    texts = [m for m in senders.outbox if m["channel"] == "sms"]
    assert len(emails) == 3 and len(texts) == 1
    assert "Tour dates &lt;3" not in emails[0]["body"]  # subject isn't in the body
    assert "<p>Big news.</p><p>We&#x27;re going on tour.</p>" in emails[0]["body"]  # escaped paragraphs
    assert "Unsubscribe" in emails[0]["body"] and "Reply STOP" in texts[0]["body"]

    link = re.search(r"http://localhost:8000(/r/[\w-]+)", texts[0]["body"]).group(1)
    r = client.get(link, follow_redirects=False)
    assert r.headers["location"] == "https://tickets.example.com"

    stats = client.get(f"/broadcasts/{b['id']}/stats", headers=artist).json()
    assert stats["email"]["sent"] == 3 and stats["sms"]["clicked"] == 1 and stats["click_rate"] == 0.25

    # Locked after sending, and never sends twice.
    assert client.post(f"/broadcasts/{b['id']}/send-now", headers=artist).status_code == 409
    assert client.patch(f"/broadcasts/{b['id']}", json={"subject": "oops"}, headers=artist).status_code == 409
    assert len(senders.outbox) == 4


def test_city_filter_and_unsubscribed_fans(client, artist):
    follow(client, email="atl@example.com", city="Atlanta")
    follow(client, email="nyc@example.com", city="New York")
    follow(client, email="gone@example.com", city="Atlanta")
    client.post("/broadcasts", json={"subject": "s", "body": "b"}, headers=artist)  # warm up ids
    fans = {f["email"]: f for f in client.get("/fans", headers=artist).json()}

    # Unsubscribe one Atlanta fan using their real token.
    b0 = client.post("/broadcasts", json={"subject": "hi", "body": "hi"}, headers=artist).json()
    client.post(f"/broadcasts/{b0['id']}/send-now", headers=artist)
    gone_mail = next(m for m in senders.outbox if m["to"] == "gone@example.com")
    token = re.search(r"/unsubscribe/([\w-]+)", gone_mail["body"]).group(1)
    client.get(f"/unsubscribe/{token}")
    senders.outbox.clear()

    b = client.post("/broadcasts", json={"subject": "ATL show", "body": "Friday!", "city": "atlanta"}, headers=artist).json()
    client.post(f"/broadcasts/{b['id']}/send-now", headers=artist)
    assert [m["to"] for m in senders.outbox] == ["atl@example.com"]
    assert fans["nyc@example.com"]["email_opt_in"] is True


def test_scheduled_broadcast(client, artist):
    follow(client, email="fan@example.com")
    now = datetime.now(timezone.utc)
    due = client.post("/broadcasts", json={"subject": "now", "body": "x", "send_at": (now - timedelta(seconds=1)).isoformat()}, headers=artist).json()
    later = client.post("/broadcasts", json={"subject": "later", "body": "x", "send_at": (now + timedelta(days=1)).isoformat()}, headers=artist).json()
    no_time = client.post("/broadcasts", json={"subject": "t", "body": "x"}, headers=artist).json()

    for b in (due, later):
        assert client.post(f"/broadcasts/{b['id']}/schedule", headers=artist).json()["status"] == "scheduled"
    assert client.post(f"/broadcasts/{no_time['id']}/schedule", headers=artist).status_code == 422

    assert run_due_drops() == 1 and run_due_drops() == 0
    assert client.get(f"/broadcasts/{due['id']}", headers=artist).json()["status"] == "sent"
    assert client.get(f"/broadcasts/{later['id']}", headers=artist).json()["status"] == "scheduled"
    assert len(senders.outbox) == 1


def test_validation_and_ownership(client, artist):
    assert client.post("/broadcasts", json={"subject": "s", "body": "b", "send_email": False}, headers=artist).status_code == 422
    b = client.post("/broadcasts", json={"subject": "s", "body": "b"}, headers=artist).json()
    assert client.patch(f"/broadcasts/{b['id']}", json={"send_email": False}, headers=artist).status_code == 422

    other = client.post("/auth/signup", json={
        "email": "other@example.com", "password": "supersecret", "name": "Other", "handle": "other",
    }).json()["access_token"]
    h = {"Authorization": f"Bearer {other}"}
    assert client.get(f"/broadcasts/{b['id']}", headers=h).status_code == 404
    assert client.post(f"/broadcasts/{b['id']}/send-now", headers=h).status_code == 404
    follow(client, email="mine@example.com")
    assert client.get("/broadcasts/audience", headers=h).json()["email"] == 0  # can't see my fans
