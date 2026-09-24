#!/usr/bin/env bash
# Full-state backup of the production stack, as one encryptable tarball.
#
# Run on the server, from anywhere in the checkout:
#     ./scripts/backup.sh          ->  backups/nhh-<timestamp>.tar.gz
#
# What goes in, and why each piece has to:
#
#   databases.sql  pg_dumpall, NOT pg_dump. pg_dump takes one database by name;
#                  pg_dumpall takes every database in the cluster plus the roles
#                  and their passwords. Today that's only `nhh` — but a backup
#                  that silently covers just the database someone remembered is
#                  exactly the backup that fails you at restore time.
#   uploads.tar    Manager-uploaded images (uploads_data volume). The database
#                  stores only filenames, so without these every image on the
#                  site 404s and no SQL restore will tell you why.
#   vip_depot.tar  Generated invitation PDFs (vip_tickets_data volume).
#   env            The live .env: DB password, payOS keys, SMTP, admin and
#                  check-in credentials. Not in git, unrecoverable if lost.
#
# Deliberately NOT included: the Caddy cert volume. Caddy re-issues from
# Let's Encrypt on the new host, and a cert can't move to a machine that hasn't
# yet passed the ACME challenge anyway.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="backups/nhh-$STAMP"

[ -f .env ] || { echo "[backup] no .env here — run this from the server checkout" >&2; exit 1; }
mkdir -p "$OUT"

# .env isn't exported into this shell, so read the role name out of it.
DB_USER=$(sed -n 's/^POSTGRES_USER=//p' .env | head -1)
DB_USER=${DB_USER:-nhh}

# -T matters. Without it docker allocates a TTY and rewrites LF as CRLF, which
# corrupts a redirected dump in a way you only discover while restoring.
echo "[backup] databases (pg_dumpall as $DB_USER)..."
$COMPOSE exec -T db pg_dumpall -U "$DB_USER" > "$OUT/databases.sql"

# A dump interrupted halfway still leaves a large, plausible-looking file, so
# check for the footer pg_dumpall writes last rather than trusting exit codes.
tail -5 "$OUT/databases.sql" | grep -q 'PostgreSQL database cluster dump complete' \
  || { echo "[backup] FAILED: dump is truncated, refusing to ship it" >&2; exit 1; }

# Reach the volumes through the app container rather than by name: compose
# prefixes volume names with the project directory, which differs per server.
APP=$($COMPOSE ps -q app)
[ -n "$APP" ] || { echo "[backup] app container isn't running" >&2; exit 1; }

echo "[backup] uploads + invitation PDFs..."
docker run --rm --volumes-from "$APP" -v "$PWD/$OUT:/out" alpine \
  tar cf /out/uploads.tar -C /app/app/static/uploads .
docker run --rm --volumes-from "$APP" -v "$PWD/$OUT:/out" alpine \
  tar cf /out/vip_depot.tar -C /app/app/vip_depot .

cp .env "$OUT/env"
git rev-parse HEAD > "$OUT/commit.txt"   # which code this data belongs to

tar czf "$OUT.tar.gz" -C backups "nhh-$STAMP"
rm -rf "$OUT"
chmod 600 "$OUT.tar.gz"

echo "[backup] done: $OUT.tar.gz ($(du -h "$OUT.tar.gz" | cut -f1))"
echo "[backup] it contains secrets and buyer PII — copy it off this server and keep it encrypted."
