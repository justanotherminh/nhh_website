# Deploying NHH 2026 to Azure (nanghoanghon.org)

This is the go-live runbook. Architecture on the server:

```
Internet ──443──> Caddy (auto-HTTPS) ──> app (gunicorn/uvicorn) ──> Postgres
                    (Let's Encrypt)         internal only            internal only
```

Everything runs via `docker-compose.prod.yml`. Only Caddy is exposed (ports 80/443).
Decisions baked in: **Cloudflare DNS-only (Caddy gets the cert)**, **payOS sandbox**,
**real SMTP email**.

Legend: 💻 = run on **your Mac**, 🖥️ = run on the **server** (over SSH).

---

## 0. One-time local prerequisites 💻

```bash
# Azure CLI
brew install azure-cli
az version

# A dedicated SSH key for this VM (press enter for no passphrase, or set one)
ssh-keygen -t ed25519 -f ~/.ssh/nhh_azure -C "nhh-azure"
```

---

## 1. Provision the Azure VM 💻

```bash
az login    # opens a browser

# Resource group in Southeast Asia (Singapore — closest region to Hanoi)
az group create --name nhh-rg --location southeastasia

# Ubuntu 22.04 LTS, 2 vCPU / 4 GB (plenty for ~800 tickets)
az vm create \
  --resource-group nhh-rg \
  --name nhh-vm \
  --image Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest \
  --size Standard_B2s \
  --admin-username azureuser \
  --ssh-key-values ~/.ssh/nhh_azure.pub \
  --public-ip-sku Standard

# Open HTTP + HTTPS (SSH/22 is already opened by `az vm create`)
az vm open-port --resource-group nhh-rg --name nhh-vm --port 80  --priority 900
az vm open-port --resource-group nhh-rg --name nhh-vm --port 443 --priority 901

# Grab the public IP — you'll need it for DNS
az vm show -d -g nhh-rg -n nhh-vm --query publicIps -o tsv
```

Note the IP (e.g. `20.x.x.x`).

> 💵 A `B2s` runs ~US$30–40/month. To stop billing for compute when idle:
> `az vm deallocate -g nhh-rg -n nhh-vm` (the public IP is Standard/static, so it
> survives). To destroy **everything**: `az group delete --name nhh-rg` (see §9).

---

## 2. Point DNS at the VM (Cloudflare) 💻/browser

In the Cloudflare dashboard for **nanghoanghon.org** → **DNS** → add two records:

| Type | Name  | Content (IPv4)   | Proxy status          |
|------|-------|------------------|-----------------------|
| A    | `@`   | `<VM public IP>` | **DNS only** (grey ☁️) |
| A    | `www` | `<VM public IP>` | **DNS only** (grey ☁️) |

**Grey cloud (DNS only) is required** so Caddy can complete the Let's Encrypt
challenge directly. You can switch to orange (proxied) later — ping me and we'll
adjust the TLS setup.

Verify it resolves (may take a few minutes):

```bash
dig +short www.nanghoanghon.org    # should print the VM IP
```

---

## 3. Prepare the server 🖥️

```bash
ssh -i ~/.ssh/nhh_azure azureuser@<VM public IP>

# Install Docker + compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker    # or log out/in so the group takes effect
docker compose version
```

---

## 4. Get the code onto the server 🖥️

The GitHub repo is private, so authenticate the clone. Easiest is a **Personal
Access Token** (GitHub → Settings → Developer settings → Fine-grained tokens →
read-only access to `nhh_website`):

```bash
git clone https://<YOUR_GITHUB_PAT>@github.com/justanotherminh/nhh_website.git
cd nhh_website
```

(Alternative: create a read-only **deploy key** and clone over SSH. Ask me if you
prefer that.)

---

## 5. Configure secrets 🖥️

```bash
cp .env.prod.example .env
openssl rand -hex 32          # copy this for SECRET_KEY
nano .env                     # fill EVERY CHANGE_ME / your-... value
```

Fill in:
- `SECRET_KEY` — the `openssl` output above.
- `POSTGRES_PASSWORD` **and** the same password inside `DATABASE_URL`.
- `PAYOS_*` — your payOS **sandbox** credentials.
- `SMTP_*` — your real mail creds (for Gmail: `smtp.gmail.com`, port `587`,
  `SMTP_USE_TLS=true`, and a Google **App Password**, not your login password).
- `ADMIN_PASSWORD` — any strong value.

`BASE_URL` is already `https://www.nanghoanghon.org` and `PAYMENTS_DEV_MODE=false`.

---

## 6. Launch 🖥️

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app
```

On first boot the app container automatically: waits for Postgres → runs
`alembic upgrade head` → imports the 706 seats from the Excel → starts gunicorn.
Watch the logs until you see `Starting Gunicorn ... on :8000`. Ctrl-C stops
*following* the logs (the containers keep running).

---

## 7. Verify go-live 💻/browser

Open **https://www.nanghoanghon.org**.

- First hit may take ~10–30s while Caddy provisions the TLS cert — then a padlock.
- Walk the flow: `/tickets` → pick a seat → checkout → (payOS **sandbox** page) →
  pay with sandbox → success → the e-ticket email arrives → open the `/ve/<token>`
  ticket page and scan its QR with your phone.
- `http://` and the bare apex should both redirect to `https://www.…`.

---

## 8. Register the payOS webhook 🖥️

Do this **after** the site is live (payOS pings the URL when registering):

```bash
docker compose -f docker-compose.prod.yml exec app python -m scripts.register_webhook
```

It registers `https://www.nanghoanghon.org/payos/webhook`. From now on payOS
confirms payments to that endpoint — the source of truth that flips orders to
`paid` and books the seats.

---

## 9. Day-to-day operations 🖥️

```bash
# Logs
docker compose -f docker-compose.prod.yml logs -f app

# Deploy an update (after you push changes to GitHub)
git pull
docker compose -f docker-compose.prod.yml up -d --build

# Restart / stop / start
docker compose -f docker-compose.prod.yml restart app
docker compose -f docker-compose.prod.yml down          # stop (keeps data volumes)

# Back up everything (databases + uploads + invitation PDFs + .env) — see §10
./scripts/backup.sh
```

**Full teardown (stops Azure billing):**
```bash
az group delete --name nhh-rg    # 💻 deletes the VM, disk, IP — everything
```

## 10. Backup, and moving to another server 🖥️

`./scripts/backup.sh` writes one tarball to `backups/`. It is the complete
state of the deployment — there is nothing else on the server worth keeping.

```bash
./scripts/backup.sh          # -> backups/nhh-20260923-140455.tar.gz
```

Four things go in, and the restore fails without any one of them:

| Inside the tarball | What it is | If it's missing |
|---|---|---|
| `databases.sql` | `pg_dumpall` — every database in the cluster, plus roles and their passwords | No orders, seats, tickets. Total loss. |
| `uploads.tar` | The `uploads_data` volume: manager-uploaded images | The DB stores only filenames, so every image 404s |
| `vip_depot.tar` | The `vip_tickets_data` volume: generated invitation PDFs | Issued invitations can't be re-downloaded |
| `env` | The live `.env` | DB password, payOS keys, SMTP and admin credentials — none of it is in git |

`pg_dumpall`, not `pg_dump`: the latter takes a single database *by name*, so it
quietly covers only the one you remembered. Caddy's cert volume is deliberately
left out — Caddy re-issues on the new host, and a certificate can't be moved to a
machine that hasn't yet passed the ACME challenge.

**Keep the tarball off the server and encrypted.** It holds every production
secret and the buyers' names, emails and phone numbers.

```bash
scp -i ~/.ssh/nhh_azure azureuser@<old-ip>:~/nhh_website/backups/nhh-*.tar.gz .    # 💻 pull it down
```

### Restoring onto a new server

Do §1, §2 and §3 as written (provision, DNS, install Docker), then:

```bash
# 🖥️ on the NEW server — scp the tarball here first
git clone https://<YOUR_GITHUB_PAT>@github.com/justanotherminh/nhh_website.git && cd nhh_website
tar xzf ~/nhh-<timestamp>.tar.gz          # -> ./nhh-<timestamp>/
cp nhh-<timestamp>/env .env

# Match the code to the data: the dump carries a specific migration state, and
# `main` may have moved on since. This detaches HEAD — `git checkout main` once
# you have verified the restore, if the two are the same commit anyway.
git checkout "$(cat nhh-<timestamp>/commit.txt)"

# Database FIRST, before the app boots and starts running migrations.
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U nhh -d postgres < nhh-<timestamp>/databases.sql

# Then the rest, which restores the two file volumes into the app container.
docker compose -f docker-compose.prod.yml up -d --build
APP=$(docker compose -f docker-compose.prod.yml ps -q app)
docker run --rm --volumes-from "$APP" -v "$PWD/nhh-<timestamp>:/in" alpine \
  sh -c "tar xf /in/uploads.tar -C /app/app/static/uploads \
      && tar xf /in/vip_depot.tar -C /app/app/vip_depot"
docker compose -f docker-compose.prod.yml restart app
```

**Expect `role "nhh" already exists` and `database "nhh" already exists` while
the SQL loads.** The Postgres image creates both from `.env` before the dump
runs. They're harmless — the tables and rows land in the existing empty
database. A restore that reports *no* errors at all is the surprising one.

Then, in order:

1. **Verify before cutting over.** `curl -H 'Host: www.nanghoanghon.org' http://<new-ip>/health`,
   and check the admin dashboard's seat counts against the old server's. Do this
   while DNS still points at the old machine.
2. **Repoint DNS** (§2) at the new IP, grey cloud. Watch
   `docker compose -f docker-compose.prod.yml logs -f caddy` for the new cert.
3. **Re-register the payOS webhook** (§8) — it stores a URL, and if the hostname
   or IP changed, payments will stop being confirmed even though checkout works.
4. **Keep the old VM for a week** before `az group delete`. It costs a few
   dollars and it's the only way back if something didn't come across.

Step 3 is the one that gets skipped. The failure it causes is silent: buyers pay,
payOS has no working URL to notify, and the order sits `pending` until it expires.

---

---

## Notes & gotchas

- **Cert won't issue?** Almost always DNS: the record must be **grey cloud** and
  resolve to the VM, and ports 80/443 must be open (they are, from §1). Check
  `docker compose -f docker-compose.prod.yml logs caddy`.
- **Sandbox → production payOS:** when you're ready to take real money, swap the
  `PAYOS_*` values in `.env` for production creds, `up -d` to restart the app, and
  re-run the webhook registration (§8).
- **Emails going to spam?** Expected from a fresh domain/sender. For real volume,
  use a proper sender (SendGrid/Resend/Mailgun) and add SPF/DKIM DNS records.
- **This is single-VM, no CI/CD** — deliberately simple for a charity event. If it
  needs to scale or get a staging environment later, that's a follow-up.
