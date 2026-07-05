# Charity Concert Ticketing Site — Prototype Plan

## Context

We're building a website for a charity concert in Hanoi (~800 tickets). The first
prototype must deliver a **fully functional seat-selection and ticket-selling flow**:
a Ticketmaster-style interactive hall map (driven by a color-coded Excel layout the
user will provide), temporary seat holds while a buyer pays, and payment via **payOS**
(Vietnamese gateway). The front page (past-concert photos + event info) is intentionally
minimal now — an art-oriented team will restyle it later, so we keep markup/CSS clean
and easy to reskin.

**Stack (decided with user):**
- Backend: **FastAPI + SQLAlchemy + PostgreSQL**
- Frontend: **server-rendered Jinja2 + HTMX**, with a small self-contained vanilla-JS
  module for the **SVG seat map** only. Plain CSS. **Vietnamese UI**, chosen for broad
  device compatibility and to keep the codebase ~95% Python (maintainable by the user,
  who knows Python, not JS).
- Payments: **payOS Python SDK** (`payos`)
- Checkout: **guest** (name/email/phone → pay → e-ticket email; no accounts)
- Hosting: **Azure VM via Docker Compose**, with **Caddy** reverse proxy for automatic
  HTTPS (payOS webhooks require a public HTTPS URL + a domain pointed at the VM)

## payOS integration facts (from docs)
- Create payment: `POST /v2/payment-requests` with `orderCode` (int), `amount` (int VND),
  `description` (≤9 chars for non-linked accounts), `returnUrl`, `cancelUrl`, HMAC-SHA256
  `signature`; headers `x-client-id`, `x-api-key`. Returns `checkoutUrl`, `qrCode`,
  `paymentLinkId`. The Python SDK handles signing.
- **Webhook is the source of truth for "paid"**, not the returnUrl. payOS POSTs a signed
  payload; we verify the signature with the checksum key before confirming. Must be
  idempotent (may fire more than once). Register the webhook URL once via `confirm-webhook`.
- Get / cancel link: `GET` / `POST .../{id}/cancel`.

## Architecture & key design decisions

### Seat-hold concurrency (the crux)
Hold state lives **directly on the `seats` row** and is claimed with a single atomic
conditional UPDATE — no explicit row locks needed, and holds expire lazily:

```sql
UPDATE seats
SET held_by_cart = :cart, hold_expires_at = now() + :ttl
WHERE id = :seat_id
  AND status = 'available'
  AND (held_by_cart IS NULL OR hold_expires_at < now());
-- rowcount == 1  => hold acquired; 0 => already taken
```
- A seat is **held** when `status='available' AND hold_expires_at > now()`.
- A seat is **booked** (permanent) when `status='booked'` (paid sale *or* comp).
- A seat is **blocked** (`status='blocked'`) when reserved for invitations — hidden/greyed
  on the public map and not buyable, until an admin assigns it or unblocks it.
- TTL ~10 min while browsing; extended to ~15 min when a payOS link is created.
- An APScheduler job (every ~60s) cancels stale pending orders / frees holds for display;
  correctness is already guaranteed by the lazy condition above.

### Cart / session
Signed cookie (itsdangerous) holding a `cart_id` UUID. Holds and orders are tied to it.
Configurable **max seats per order** (default **8**).

### Order & payment lifecycle
1. Select seats (atomic holds) → 2. Enter name/email/phone → 3. Create `order` +
payOS link, extend holds, redirect to `checkoutUrl` → 4. payOS webhook verified →
mark order `paid`, flip held seats to `booked` (idempotent), generate tickets, email them
→ 5. returnUrl success page **polls order status** (truth comes from webhook); cancelUrl
releases holds.

### E-ticket
Per-order tickets with a unique code + QR (`qrcode`/Pillow), delivered by email (SMTP,
config-driven) and viewable on a tokenized ticket page.

### Invitation / comp tickets
Two separate concepts, both admin-driven (no guest-facing self-selection):
- **Blocking seats**: admin marks individual seats or whole rows as `status='blocked'` to
  pull them from public sale (hidden/greyed on the public map). Reversible (unblock).
- **Issuing a comp ticket**: admin picks seat(s) (typically blocked ones) + enters a guest
  name/email and sends. This creates an `order` with `kind='comp'`, `amount_vnd=0`, **no
  payOS step**, status straight to `paid`/confirmed, seats flipped to `booked`. It reuses
  the **same ticket/QR/email machinery** as paid orders, so comp and paid tickets **scan
  identically at the door** and reports can split sold vs. comp counts.

## Data model (SQLAlchemy + Alembic)
- `price_tiers`: id, name, color_hex, price_vnd
- `seats`: id, section, row_label, seat_number, label, tier_id, svg_x, svg_y,
  status ('available'|'blocked'|'booked'), held_by_cart (uuid, nullable),
  hold_expires_at (nullable)
- `orders`: id, order_code (int, unique, for payOS), kind ('sale'|'comp'), cart_id
  (nullable for comp), buyer_name, email, phone, amount_vnd, status
  ('pending'|'paid'|'cancelled'|'expired'), payos_payment_link_id, created_at
- `order_items`: order_id, seat_id, price_vnd
- `tickets`: id, order_id, seat_id, ticket_code (unique), qr_token

## Project structure
```
website/
  app/
    main.py                 # FastAPI app, mounts routers, static, templates, scheduler
    config.py               # pydantic-settings (DB, payOS keys, SMTP, TTLs, base URL)
    db.py                   # engine + session
    models.py               # SQLAlchemy models
    routers/
      pages.py              # GET / (front page), GET /tickets (seat selection page)
      seats.py              # POST /hold, POST /release, GET /seats/status (HTMX/JSON)
      checkout.py           # POST /checkout (create order + payOS link), success/cancel
      webhook.py            # POST /payos/webhook (verify + confirm, idempotent)
      admin.py              # basic-auth dashboard: orders, occupancy, release,
                            #   block/unblock seats, issue comp tickets
    services/
      holds.py              # acquire/release/extend holds (the atomic UPDATEs)
      payos_client.py       # wraps payos SDK: create link, verify webhook, cancel
      tickets.py            # QR generation + SMTP email
    templates/              # Jinja2: base, index, tickets, checkout, success, ticket, admin
    static/
      css/styles.css
      js/seatmap.js         # SVG render/select + status polling (the only JS we write)
      img/                  # past-concert photos (placeholder)
  scripts/
    import_seatmap.py       # openpyxl: color-coded .xlsx -> price_tiers + seats
    seed_placeholder.py     # generates a placeholder hall until real .xlsx arrives
    register_webhook.py     # one-time payOS confirm-webhook call
  alembic/                  # migrations
  tests/                    # pytest: hold concurrency, webhook verify, order lifecycle
    conftest.py             # disposable Postgres test DB + transactional rollback fixtures
  .github/workflows/ci.yml  # Postgres service + pytest + ruff on push/PR
  Dockerfile
  docker-compose.yml        # app (gunicorn+uvicorn), db (postgres), caddy, mailpit (dev)
  Caddyfile                 # auto-HTTPS reverse proxy -> app
  .env.example
  requirements.txt          # + requirements-dev.txt (pytest, ruff)
  README.md                 # setup, dev (cloudflared tunnel for webhooks), Azure deploy
```

## Key dependencies
fastapi, uvicorn[standard], gunicorn, sqlalchemy, psycopg[binary], alembic, jinja2,
python-multipart, itsdangerous, pydantic-settings, **payos**, qrcode[pil], openpyxl,
apscheduler, httpx; dev/test: pytest, ruff, Mailpit (docker, fake SMTP inbox).

## Implementation phases
1. **Scaffold**: repo, `git init`, requirements, `config.py`, `db.py`, Docker Compose
   (app + Postgres), Caddy, `.env.example`. Install Python deps.
2. **Data layer**: models + Alembic migration + `seed_placeholder.py` (a believable hall
   with a few tiers) so the UI works before the real Excel arrives. `import_seatmap.py`
   keyed to the expected color→tier mapping.
3. **Seat selection page**: server-rendered SVG map, `seatmap.js` (select + poll), atomic
   hold/release endpoints, max-per-order limit.
4. **Checkout + payOS**: order creation, payOS link, success/cancel pages, **webhook**
   verify+confirm (idempotent), hold extension.
5. **E-tickets**: QR + SMTP email + tokenized ticket page.
6. **Hold expiry job** (APScheduler) + **admin** dashboard: orders/occupancy, manual
   release, **block/unblock seats**, and **issue comp tickets** (assign seat + guest
   name/email → comp order → e-ticket email; reuses the ticket/QR/email machinery).
7. **Front page** (minimal) + **deploy docs** (Azure VM, DNS/domain, Caddy HTTPS, webhook
   registration).

## What I'll need from you (non-blocking — I'll scaffold with placeholders)
- The **color-coded Excel** seat map (+ a note on how each color maps to a price tier).
- **payOS** merchant credentials (clientId / apiKey / checksumKey) for `.env`.
- **SMTP** credentials for sending e-tickets.
- A **domain** to point at the Azure VM (needed for HTTPS + payOS webhook).

## Defaults assumed (tell me to change any)
- Max **8** seats per order; browse-hold TTL **10 min**, payment window **15 min**.
- QR-code e-tickets emailed + tokenized ticket page.
- Minimal basic-auth admin dashboard (view orders, seat occupancy, manual release).
- Single concert/event (no multi-event support yet).

## Testing strategy (rigor: focused on the risky core)
Built to be testable: DB session is injected, the payOS SDK sits behind `payos_client.py`,
and email send is a swappable function. Solid tests on the three things that can lose money
or double-sell a seat; light/smoke coverage elsewhere.

**Tests run against a real disposable Postgres** (docker / CI service), **not SQLite** —
the hold mechanism relies on Postgres's atomic conditional `UPDATE`. `conftest.py` provides
schema setup + per-test transactional rollback; concurrency tests commit for real.

- **Seat holds / concurrency:** a `ThreadPoolExecutor` fires N simultaneous `/hold` requests
  at the same seat → exactly one wins; hold expiry frees a seat; blocked seats aren't sellable.
- **payOS webhook (SDK mocked — never hits payOS):** payloads signed with a test checksum key
  → valid signature books seats; **tampered signature rejected**; **double-fire = one booking**
  (idempotent). Outbound create-link is mocked to return a fake `checkoutUrl`.
- **Order lifecycle:** pending→paid→seats booked; cancel/expire releases holds; comp order
  books a seat with amount 0 and issues a scannable ticket.
- **Email:** send is monkeypatched in tests (assert called with right ticket/QR data); in dev
  it goes to **Mailpit** to eyeball the real e-ticket.
- **Excel import:** tiny fixture `.xlsx` → assert correct tiers/seats parsed.

## CI (GitHub Actions)
`.github/workflows/ci.yml` runs on push/PR: spins up a **Postgres service container**, runs
**`pytest`** and **`ruff`** lint. Keeps broken hold/webhook logic from landing.

## Manual / pre-deploy verification (what tests can't prove)
- `docker compose up` (app + Postgres + Mailpit), run `seed_placeholder.py`, open `/tickets`,
  hold seats in **two browsers** to see live status; checkout against the **payOS sandbox**
  with a **cloudflared tunnel** exposing the webhook; confirm paid + e-ticket in Mailpit.
- **Pre-deploy:** smoke-test on the Azure VM with the real domain + Caddy HTTPS, then register
  the production webhook URL.
