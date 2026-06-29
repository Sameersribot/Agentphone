"""
AgentLine — Event Bus
The single, canonical entry point for publishing events in AgentLine.

Every event — call lifecycle, SMS, billing, and future agent-driven data — flows
through `publish_event()`. It fans out to two delivery channels that always stay
in sync:

  1. event_mailbox  — durable, consume-once queue polled via GET /v1/events
  2. webhook        — signed HTTP POST to the account's configured webhook URL

Future features MUST call publish_event() rather than inserting into event_mailbox
or firing webhooks directly. This keeps every delivery channel consistent and makes
any new event type instantly available to both pollers and webhook receivers.

Usage:
    from agentline.event_bus import publish_event

    await publish_event(
        account_id=account["id"],
        agent_id=agent_id,
        event_type="my_feature.thing_happened",
        payload={"foo": "bar"},
    )
"""

import json
import logging
import secrets

from agentline.database import get_db_conn
from agentline.webhook_dispatcher import dispatch_webhook

logger = logging.getLogger(__name__)


async def publish_event(
    account_id: str,
    agent_id: str | None,
    event_type: str,
    payload: dict,
) -> None:
    """
    Publish an event to ALL delivery channels (mailbox + webhook).

    This is THE function to call when anything noteworthy happens in AgentLine.
    It is fire-and-forget and never raises: each channel is isolated, so a
    webhook failure never blocks the mailbox insert (or vice versa), protecting
    callers that must return a response (e.g. provider callback handlers).

    Args:
        account_id: Owning account.
        agent_id:   Related agent if any (None for account-level events). Passed
                    through into the payload so receivers know which agent fired.
        event_type: Dotted event name, e.g. "call.completed", "sms.received".
        payload:    Event-specific data (bare — the "event" key is added
                    automatically for the webhook envelope).
    """
    event_id = f"evt_{secrets.token_urlsafe(12)}"
    body = json.dumps(payload, default=str)

    # 1. Persist to the event mailbox (consume-once polling queue for /v1/events)
    try:
        async with get_db_conn() as db:
            await db.execute(
                """INSERT INTO event_mailbox
                   (event_id, account_id, agent_id, event_type, payload)
                   VALUES ($1, $2, $3, $4, $5)""",
                event_id, account_id, agent_id, event_type, body,
            )
    except Exception as e:
        logger.error("publish_event[%s]: mailbox insert failed: %s", event_type, e)

    # 2. Fire to the configured webhook (signed, best-effort)
    try:
        await dispatch_webhook(account_id, agent_id, {"event": event_type, **payload})
    except Exception as e:
        logger.warning("publish_event[%s]: webhook dispatch failed: %s", event_type, e)
