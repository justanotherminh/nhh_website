"""FastAPI application entrypoint."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.routers import pages, seatmap, seats

app = FastAPI(title=settings.app_name)

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)

app.include_router(pages.router)
app.include_router(seatmap.router)
app.include_router(seats.router)


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
