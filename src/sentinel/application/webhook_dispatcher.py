"""
Outbound webhook dispatcher for Sentinel-Q.

Sends HTTP POST notifications to tenant-configured URLs when a monitored
target transitions state (up → down, down → up).

Design decisions:
  - Fire-and-forget: failures are logged, never propagated to the scheduler.
  - No retries, no DLQ, no delivery store (V1 scope).
  - httpx.AsyncClient with 5s timeout — CPU-friendly.
  - HMAC-SHA256 signature when webhook.secret is set.
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger


def _build_payload(
    event: str,
    target_id: int,
    target_name: str,
    target_url: str,
    tenant_id: int,
) -> dict:
    """Build the standard webhook event payload."""
    return {
        "event": event,
        "target": {
            "id": target_id,
            "name": target_name,
            "url": target_url,
        },
        "tenant_id": tenant_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _sign_payload(body: bytes, secret: str) -> str:
    """Return 'sha256=<hex>' HMAC-SHA256 signature of body using secret."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def dispatch_webhook(
    *,
    webhook_id: int,
    url: str,
    secret: Optional[str],
    event: str,
    target_id: int,
    target_name: str,
    target_url: str,
    tenant_id: int,
) -> None:
    """
    Send a single outbound webhook POST.

    Never raises — all failures are caught and logged.
    Safe to call from the scheduler without await wrapping.
    """
    payload = _build_payload(event, target_id, target_name, target_url, tenant_id)
    body = json.dumps(payload, separators=(",", ":")).encode()

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Sentinel-Event": event,
    }
    if secret:
        headers["X-Sentinel-Signature"] = _sign_payload(body, secret)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, content=body, headers=headers)
        logger.debug(
            f"Webhook dispatched — id={webhook_id} event={event} "
            f"target_id={target_id} tenant_id={tenant_id} status={response.status_code}"
        )
    except httpx.TimeoutException:
        logger.warning(
            f"Webhook timeout — id={webhook_id} event={event} "
            f"target_id={target_id} tenant_id={tenant_id} url={url}"
        )
    except Exception as exc:
        logger.error(
            f"Webhook failed — id={webhook_id} event={event} "
            f"target_id={target_id} tenant_id={tenant_id} url={url} error={exc!r}"
        )


async def dispatch_webhooks_for_event(
    *,
    event: str,
    target_id: int,
    target_name: str,
    target_url: str,
    tenant_id: int,
) -> None:
    """
    Load active webhooks for tenant + event from DB and dispatch all of them.

    Opens its own DB session — safe to call from the scheduler background loop.
    If DB access fails, logs and returns silently.
    """
    from sentinel.infrastructure.database import SessionLocal
    from sentinel.infrastructure.repository import TargetRepository

    try:
        with SessionLocal() as db:
            repo = TargetRepository(db)
            webhooks = repo.get_active_webhooks_for_event(tenant_id, event)
    except Exception as exc:
        logger.error(
            f"Webhook DB lookup failed — event={event} tenant_id={tenant_id} error={exc!r}"
        )
        return

    if not webhooks:
        logger.debug(
            f"No active webhooks for tenant_id={tenant_id} event={event}"
        )
        return

    for webhook in webhooks:
        await dispatch_webhook(
            webhook_id=webhook.id,
            url=webhook.url,
            secret=webhook.secret,
            event=event,
            target_id=target_id,
            target_name=target_name,
            target_url=target_url,
            tenant_id=tenant_id,
        )
