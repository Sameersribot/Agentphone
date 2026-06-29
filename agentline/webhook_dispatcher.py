"""
AgentLine — Webhook Dispatcher
Low-level delivery layer that POSTs a signed payload to an agent's webhook.

This module is the "delivery" half of the event pipeline. The "publishing" half
lives in agentline.event_bus.publish_event(), which is the public entry point all
application code should call. dispatch_webhook() is only invoked by
publish_event() and the /v1/webhooks/test endpoint.

Webhooks are strictly per-agent (one webhook URL per agent). There is NO
account-wide webhook — an event is delivered only when the agent it belongs to
has a webhook configured.
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
    Deliver `payload` to the webhook configured for (account_id, agent_id).

    Looks up the single per-agent webhook row, signs the JSON body with
    HMAC-SHA256 using the webhook's secret, and POSTs it with headers:

      - X-AgentLine-Signature: <hex hmac of the raw body>
      - X-AgentLine-Event:      <payload["event"]>

    No-op when no webhook is configured for the agent (or when agent_id is
    None). Failures are logged, never raised, so callers (provider callback
    handlers) are never blocked.

    Note: dispatch_webhook does NOT persist to the event mailbox — that is the
    job of agentline.event_bus.publish_event(). Call that instead.
    """
    if not agent_id:
        return

    try:
        async with get_db_conn() as db:
            row = await db.fetchrow(
                """SELECT id, url, secret FROM webhooks
                   WHERE account_id = $1 AND agent_id = $2""",
                account_id, agent_id,
            )

        if not row:
            return

        body = json.dumps(payload, default=str)
        signature = hmac.new(
            row["secret"].encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                row["url"],
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-AgentLine-Signature": signature,
                    "X-AgentLine-Event": payload.get("event", "unknown"),
                },
            )

        logger.info(
            "Webhook delivered to %s (webhook=%s, agent=%s, event=%s, status=%s)",
            row["url"], row["id"][:12], agent_id[:12], payload.get("event"), resp.status_code,
        )
    except Exception as e:
        logger.warning("Webhook delivery failed for agent %s: %s", agent_id[:12], e)
