"""FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal, engine
from app.routers import admin, checkout, pages, seatmap, seats, ticket_view, webhook
from app.services import orders

log = logging.getLogger("scheduler")


def _sweep_stale_orders() -> None:
    """Periodic job: cancel pending orders whose payment window has elapsed."""
    db = SessionLocal()
    try:
        orders.expire_stale_orders(db)
    except Exception:
        log.exception("expire_stale_orders sweep failed")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _sweep_stale_orders,
        "interval",
        seconds=60,
        id="expire_stale_orders",
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    log.info("Started stale-order sweeper (every 60s)")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)

app.include_router(pages.router)
app.include_router(seatmap.router)
app.include_router(seats.router)
app.include_router(checkout.router)
app.include_router(webhook.router)
app.include_router(ticket_view.router)
app.include_router(admin.router)


@app.get("/health")
def health() -> dict:
    """Liveness + DB connectivity check."""
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "app": settings.app_name, "db": db_ok}
