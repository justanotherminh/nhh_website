#!/usr/bin/env bash
# Container entrypoint: wait for the DB, apply migrations, seed seats once, serve.
set -e

echo "[entrypoint] Waiting for database..."
python - <<'PY'
import sys, time
from sqlalchemy import create_engine, text
from app.config import settings

for attempt in range(60):
    try:
        with create_engine(settings.database_url).connect() as c:
            c.execute(text("SELECT 1"))
        print("[entrypoint] Database is ready")
        break
    except Exception as exc:
        print(f"[entrypoint]   ...not ready ({exc.__class__.__name__}), retrying")
        time.sleep(2)
else:
    print("[entrypoint] Database never became reachable")
    sys.exit(1)
PY

echo "[entrypoint] Applying migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] Seeding seats if the table is empty..."
python - <<'PY'
from sqlalchemy import func, select
from app.db import SessionLocal
from app.models import Seat

db = SessionLocal()
count = db.execute(select(func.count()).select_from(Seat)).scalar()
db.close()
if count == 0:
    print("[entrypoint]   No seats found -> importing from the Excel map")
    import runpy
    runpy.run_module("scripts.import_seatmap", run_name="__main__")
else:
    print(f"[entrypoint]   {count} seats already present -> skipping import")
PY

echo "[entrypoint] Starting Gunicorn (Uvicorn workers) on :8000..."
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000 \
    -w "${WEB_CONCURRENCY:-2}" \
    --access-logfile - \
    --error-logfile -
