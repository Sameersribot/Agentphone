"""
AgentLine — Webhook Dispatcher
Fires signed events to customer-configured webhook URLs.
Tries agent-level webhook first, falls back to account-level.
"""

import hmac
import hashlib
import json
import logging

import httpx

from agentline.database import get_db_conn

logger = logging.getLogger(__name__)


async def dispatch_webhook(account_id: str, agent_id: str | None, event: dict):
    """
    Fire an event to the customer's configured webhook URL.
    Signs the payload with HMAC-SHA256 using the per-webhook secret.
    """
    async with get_db_conn() as db:
        # Try agent-level webhook first
        webhook = None
        if agent_id:
            webhook = await db.fetchrow(
                "SELECT * FROM webhooks WHERE agent_id = $1", agent_id
            )
        # Fall back to account-level
        if not webhook:
            webhook = await db.fetchrow(
                "SELECT * FROM webhooks WHERE account_id = $1 AND agent_id IS NULL",
                account_id,
            )

    if not webhook:
        return  # No webhook configured — skip silently

    payload = json.dumps(event, default=str).encode("utf-8")
    signature = hmac.new(
        webhook["secret"].encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                webhook["url"],
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-AgentLine-Signature": f"sha256={signature}",
                    "X-AgentLine-Event": event.get("event", "unknown"),
                },
            )
            logger.info(
                "Webhook delivered to %s — status %d",
                webhook["url"],
                response.status_code,
            )
        except httpx.HTTPError as e:
            logger.warning(
                "Webhook delivery failed for %s: %s",
                webhook["url"],
                str(e),
            )
        except Exception as e:
            logger.error(
                "Unexpected webhook error for %s: %s",
                webhook["url"],
                str(e),
            )
