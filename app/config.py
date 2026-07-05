"""Application settings, loaded from environment / .env (see .env.example)."""
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

    # Seat holds (seconds)
    hold_ttl_seconds: int = 600  # 10 min while browsing
    payment_window_seconds: int = 900  # 15 min once a payOS link is created
    max_seats_per_order: int = 8

    # Admin (HTTP basic auth)
    admin_username: str = "admin"
    admin_password: str = "change-me"


settings = Settings()
