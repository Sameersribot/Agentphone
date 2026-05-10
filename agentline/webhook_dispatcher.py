"""
AgentLine — Webhook Dispatcher (Hybrid: External URL + Mailbox)

Fires events to customer-configured webhook URLs when available.
Falls back to the internal event mailbox when no webhook is configured,
so agents can pull events via GET /v1/events without exposing a public URL.

Priority order:
  1. Agent-level external webhook URL → POST to URL
  2. Account-level external webhook URL → POST to URL
  3. No webhook configured → queue in event_mailbox (agent pulls via API)
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

    If no external webhook is configured, queues the event in the
    internal mailbox for the agent to pull via GET /v1/events.
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
        # No external webhook → queue in mailbox for pull-based access
        from agentline.routers.events import queue_event
        await queue_event(account_id, agent_id, event)
        return

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

            # If external webhook fails (4xx/5xx), also queue in mailbox as backup
            if response.status_code >= 400:
                logger.warning(
                    "Webhook returned %d — queueing in mailbox as fallback",
                    response.status_code,
                )
                from agentline.routers.events import queue_event
                await queue_event(account_id, agent_id, event)

        except httpx.HTTPError as e:
            logger.warning(
                "Webhook delivery failed for %s: %s — queueing in mailbox",
                webhook["url"],
                str(e),
            )
            # Failed delivery → queue in mailbox so event isn't lost
            from agentline.routers.events import queue_event
            await queue_event(account_id, agent_id, event)

        except Exception as e:
            logger.error(
                "Unexpected webhook error for %s: %s",
                webhook["url"],
                str(e),
            )
