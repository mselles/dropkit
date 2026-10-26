# DropKit

A small Laylo-style drop API built with FastAPI. Artists create drop pages, fans RSVP with email or phone, and everyone gets notified the moment the drop goes live. Artists can also grow a mailing list and send newsletter style broadcasts to it.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open **http://localhost:8000/docs**. Every endpoint is testable from that page.

Without any API keys, emails and texts **print to your terminal** instead of sending, so you can build the whole flow for free. Data lives in `dropkit.db` (SQLite). Delete that file to start fresh.

## Try the whole flow in /docs

1. **POST /auth/signup** with your email, a password, a name, and a handle.
2. Click **Authorize** (top right). Enter your email as the username, plus your password.
3. **POST /drops** with `{"title": "New Single", "destination_url": "https://open.spotify.com/..."}` and copy the `slug` from the response.
4. **POST /d/{slug}/rsvp** with `{"email": "you@example.com"}`. This is what your fan facing page will call.
5. **POST /drops/{id}/send-now** and watch the notification print in your terminal.
6. Open the `/r/...` link from the message. It redirects to your destination URL and records the click.
7. **GET /drops/{id}/stats** shows RSVPs, sends, and click rate.

### Try a broadcast (newsletter)

1. **POST /a/{handle}/follow** with `{"email": "fan2@example.com", "city": "Atlanta"}`. This is a "join my list" signup with no drop attached.
2. **GET /broadcasts/audience** shows how many people you'd reach. Add `?city=Atlanta` to narrow it.
3. **POST /broadcasts** with `{"subject": "New merch", "body": "Hoodies are back.\n\nLimited run.", "link_url": "https://your-shop.com", "link_label": "Shop now"}`. This only creates a draft.
4. **POST /broadcasts/{id}/send-now** and check your terminal.

Everyone on the artist's list who's opted in gets it, whether they followed or RSVP'd to a drop. Blank lines in `body` become paragraphs. Set `send_sms: true` to text it too, or `city` to target one place.

To test the scheduler instead, set `release_at` to a minute from now, then call **POST /drops/{id}/schedule**. It fires on its own within 30 seconds. Broadcasts work the same way with `send_at` and **POST /broadcasts/{id}/schedule**.

## Endpoints

| | Route | What it does |
|---|---|---|
| **Auth** | `POST /auth/signup` · `POST /auth/login` · `GET /auth/me` | JWT auth for artists |
| **Drops** | `GET/POST /drops` · `GET/PATCH/DELETE /drops/{id}` | Manage drops |
| | `POST /drops/{id}/schedule` · `/unschedule` | Arm or disarm the scheduler |
| | `POST /drops/{id}/send-now` | Release immediately |
| | `GET /drops/{id}/stats` | RSVPs, sends, deliveries, clicks by channel |
| **Broadcasts** | `GET/POST /broadcasts` · `GET/PATCH/DELETE /broadcasts/{id}` | Newsletter style messages to your list |
| | `GET /broadcasts/audience?send_sms=&city=` | Preview how many people you'd reach |
| | `POST /broadcasts/{id}/schedule` · `/unschedule` · `/send-now` | Send later or now |
| | `GET /broadcasts/{id}/stats` | Sends and clicks by channel |
| **Fans** | `GET /fans?city=&channel=&drop_id=` | Your fan list, filterable |
| | `GET /fans/{id}/history` | Every message a fan has received |
| **Public** | `GET /a/{handle}` | Artist page data: name plus published drops |
| | `POST /a/{handle}/follow` | Join the artist's list (no drop needed) |
| | `GET /d/{slug}` | Data for the drop page (hides the link until it's live) |
| | `POST /d/{slug}/rsvp` | Fan signup |
| | `GET /r/{token}` | Click tracking redirect |
| | `GET/POST /unsubscribe/{token}` | One click unsubscribe |
| **Webhooks** | `POST /webhooks/twilio/inbound` | Handles STOP and START replies |
| | `POST /webhooks/twilio/status` | Delivery receipts |

## How the code is laid out

```
app/
  main.py        app setup, starts the scheduler
  config.py      every setting, read from environment variables
  models.py      the 6 database tables
  schemas.py     request and response shapes, input validation
  auth.py        password hashing and JWT
  release.py     the send engine: claim → queue → send   ← read this one
  services.py    shared logic: fan signup, audiences, stats
  senders.py     email (Resend) and SMS (Twilio), console fallback
  routers/       one file per group of endpoints
tests/           pytest suite covering the full flow
```

`release.py` is the interesting part. `claim_drop` flips a drop to `sending` with a single conditional UPDATE. Drops and broadcasts share the engine. If the scheduler and a "send now" click race, only one wins, so fans never get double texted.

## Changing the database

There are no migrations yet, so after pulling a version that changes `models.py`, delete `dropkit.db` and restart. That wipes your test data. Add Alembic before you have real fans in there.

## Run the tests

```bash
pytest -q
```

## Going live

1. **Email.** Make a free [Resend](https://resend.com) account, verify a domain, and set `RESEND_API_KEY` and `EMAIL_FROM`.
2. **Postgres.** Set `DATABASE_URL=postgresql+psycopg://...` and `pip install "psycopg[binary]"`. Neon and Supabase both have free tiers.
3. **SMS.** Set the `TWILIO_*` variables. You'll need A2P 10DLC registration for US texting, which takes days to weeks. Point your number's inbound webhook at `/webhooks/twilio/inbound`, and add Twilio signature validation first (see the TODO in `routers/webhooks.py`).
4. Set a real `JWT_SECRET`, plus `BASE_URL` set to your public URL.
5. Deploy to Railway, Render, or Fly.io: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

## Where to take it next

- **The fan facing drop page.** A frontend that calls `GET /d/{slug}` and `POST /d/{slug}/rsvp`, with a countdown. This is the design opportunity.
- **A real queue** (arq + Redis) once a drop has thousands of fans. The send loop already works in batches, so it's a contained swap.
- **Migrations** with Alembic, before you change a table that already holds real data.
- **Instagram DM keyword flow** via the Meta Graph API.
