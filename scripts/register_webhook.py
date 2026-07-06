"""One-time: register this deployment's webhook URL with payOS.

payOS delivers payment confirmations to a single registered URL. Run this once
after the site is live on its real HTTPS domain:

    docker compose -f docker-compose.prod.yml exec app python -m scripts.register_webhook

It derives the URL from BASE_URL, so make sure that points at the public site.
"""
from __future__ import annotations

import sys

from app.config import settings
from app.services import payos_client


def main() -> int:
    if not payos_client.is_configured():
        print("payOS credentials are not configured (check PAYOS_* in .env).")
        return 1

    webhook_url = f"{settings.base_url}/payos/webhook"
    print(f"Registering webhook: {webhook_url}")
    try:
        result = payos_client._client().confirmWebhook(webhook_url)
    except Exception as exc:
        print(f"Failed to register webhook: {exc}")
        return 1
    print(f"Success. payOS response: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
