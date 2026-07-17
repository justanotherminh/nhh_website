"""Application settings, loaded from environment / .env (see .env.example)."""
import datetime as dt

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # General
    app_name: str = "NHH 2026"
    base_url: str = "http://localhost:8000"
    secret_key: str = "dev-secret-change-me"

    # Database
    database_url: str = "postgresql+psycopg://nhh:nhh@localhost:5433/nhh"

    # payOS
    payos_client_id: str = ""
    payos_api_key: str = ""
    payos_checksum_key: str = ""
    # When True (or when payOS is unconfigured), checkout uses the in-app dev-pay
    # simulator instead of a real payment link. Keep False in production.
    payments_dev_mode: bool = False

    # SMTP (defaults point at the Mailpit container from docker-compose)
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "NHH 2026 <noreply@nhh.local>"
    smtp_use_tls: bool = False  # True for real SMTP (e.g. Gmail); False for Mailpit

    # Early-bird discount (automatic, time-boxed). Percent 0 = disabled. If a
    # deadline is set, every checkout before it gets the discount; after it, none.
    # earlybird_until may be an ISO datetime; a naive value is read as Hanoi time.
    # Change these in .env and restart — no redeploy needed.
    earlybird_percent: int = 0
    earlybird_until: dt.datetime | None = None

    @field_validator("earlybird_until", mode="before")
    @classmethod
    def _blank_until_is_none(cls, v):
        # A blank EARLYBIRD_UNTIL= in .env means "no deadline set", not a parse error.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # Seat holds (seconds)
    hold_ttl_seconds: int = 600  # 10 min while browsing
    payment_window_seconds: int = 900  # 15 min once a payOS link is created
    max_seats_per_order: int = 8

    # Admin (HTTP basic auth)
    admin_username: str = "admin"
    admin_password: str = "change-me"

    # Door check-in (HTTP basic auth) — a limited credential for entrance volunteers.
    # Lets them scan/redeem tickets WITHOUT admin access to buyer data.
    checkin_username: str = "cua"
    checkin_password: str = "change-me"


settings = Settings()
