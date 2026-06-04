"""
AgentLine — Webhook Dispatcher
Fires webhook payloads to customer-registered webhook URLs.

Looks up webhooks from the `webhooks` table by account_id + optional agent_id,
signs the payload with the webhook secret, and POSTs it.
"""

import hashlib
import hmac
import json
import logging

import httpx

from agentline.database import get_db_conn

logger = logging.getLogger(__name__)


async def dispatch_webhook(
    account_id: str,
    agent_id: str | None,
    payload: dict,
) -> None:
    """
    Dispatch an event payload to all matching customer webhooks.

    Looks up webhooks by:
      1. Agent-specific webhooks (agent_id match)
      2. Account-level webhooks (agent_id IS NULL)

    Signs the payload with HMAC-SHA256 using the webhook's secret,
    and sends the header `X-AgentLine-Signature` for verification.
    """
    try:
        async with get_db_conn() as db:
            # Get all matching webhooks: agent-specific + account-level
            rows = await db.fetch(
                """SELECT id, url, secret FROM webhooks
                   WHERE account_id = $1
                     AND (agent_id = $2 OR agent_id IS NULL)""",
                account_id, agent_id,
            )

        if not rows:
            return

        body = json.dumps(payload, default=str)

        async with httpx.AsyncClient(timeout=10.0) as client:
            for row in rows:
                try:
                    # Sign the payload
                    signature = hmac.new(
                        row["secret"].encode(),
                        body.encode(),
                        hashlib.sha256,
                    ).hexdigest()

                    await client.post(
                        row["url"],
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-AgentLine-Signature": signature,
                            "X-AgentLine-Event": payload.get("event", "unknown"),
                        },
                    )
                    logger.info(
                        "Webhook dispatched to %s (webhook=%s, event=%s)",
                        row["url"], row["id"][:12], payload.get("event"),
                    )
                except Exception as e:
                    logger.warning(
                        "Webhook delivery failed for %s: %s", row["url"], e,
                    )
    except Exception as e:
        logger.warning("Webhook dispatch error: %s", e)


async def get_webhook_speak_response(
    account_id: str,
    agent_id: str | None,
    payload: dict,
) -> str | None:
    """
    POST to customer webhooks and wait for a response.
    Returns the 'speak' text from the first webhook that returns one,
    or None if no webhook is registered or none returns a valid 'speak' field.
    """
    try:
        async with get_db_conn() as db:
            rows = await db.fetch(
                """SELECT id, url, secret FROM webhooks
                   WHERE account_id = $1
                     AND (agent_id = $2 OR agent_id IS NULL)""",
                account_id, agent_id,
            )

        if not rows:
            return None

        body = json.dumps(payload, default=str)

        async with httpx.AsyncClient(timeout=5.0) as client:
            for row in rows:
                try:
                    signature = hmac.new(
                        row["secret"].encode(),
                        body.encode(),
                        hashlib.sha256,
                    ).hexdigest()

                    resp = await client.post(
                        row["url"],
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-AgentLine-Signature": signature,
                            "X-AgentLine-Event": payload.get("event", "unknown"),
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and "speak" in data:
                            logger.info("Webhook %s returned response to speak: %s", row["url"], data["speak"][:80])
                            return data["speak"]
                except Exception as e:
                    logger.warning("Webhook delivery/response failed for %s: %s", row["url"], e)
    except Exception as e:
        logger.warning("Webhook dispatch response error: %s", e)
    return None
