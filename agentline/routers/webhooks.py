"""
AgentLine — Webhooks Router
CRUD API for webhook registration.

Agents register a webhook URL to receive real-time push notifications
for events like call.received, sms.received, and call.completed.
Supports both account-level and agent-specific webhooks.
"""

import secrets
import logging

from fastapi import APIRouter, Depends, HTTPException

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.webhook import WebhookCreate, WebhookOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["Webhooks"])


@router.post("", response_model=WebhookOut)
async def create_webhook(
    body: WebhookCreate,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Register a webhook URL to receive real-time event notifications.

    When calls arrive or SMS messages are received, AgentLine will POST
    a JSON payload to your URL with an HMAC-SHA256 signature in the
    `X-AgentLine-Signature` header.

    You can register:
    - **Account-level** webhooks (omit agent_id) — receives all events
    - **Agent-specific** webhooks (set agent_id) — receives only that agent's events

    The `secret` is auto-generated and returned only on creation.
    Use it to verify the `X-AgentLine-Signature` header on incoming webhooks.

    Events sent:
    - `call.received` — inbound call just arrived
    - `call.completed` — call ended (includes transcript)
    - `sms.received` — inbound SMS arrived
    """
    # Validate agent_id belongs to this account
    if body.agent_id:
        agent = await db.fetchrow(
            "SELECT id FROM agents WHERE id=$1 AND account_id=$2",
            body.agent_id, account["id"],
        )
        if not agent:
            raise HTTPException(404, "Agent not found.")

    webhook_id = f"wh_{secrets.token_urlsafe(12)}"
    webhook_secret = f"whsec_{secrets.token_urlsafe(32)}"

    await db.execute(
        """INSERT INTO webhooks (id, account_id, agent_id, url, secret)
           VALUES ($1, $2, $3, $4, $5)""",
        webhook_id, account["id"], body.agent_id, body.url, webhook_secret,
    )

    row = await db.fetchrow("SELECT * FROM webhooks WHERE id=$1", webhook_id)

    logger.info(
        "Webhook created: %s → %s (account=%s, agent=%s)",
        webhook_id, body.url, account["id"][:12],
        body.agent_id or "account-level",
    )

    return WebhookOut(
        id=row["id"],
        account_id=row["account_id"],
        agent_id=row["agent_id"],
        url=row["url"],
        secret=row["secret"],
        created_at=row["created_at"],
    )


@router.get("")
async def list_webhooks(
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    List all registered webhooks for your account.

    Returns both account-level and agent-specific webhooks.
    The `secret` is masked — only the first 8 characters are shown.
    """
    rows = await db.fetch(
        "SELECT * FROM webhooks WHERE account_id=$1 ORDER BY created_at DESC",
        account["id"],
    )

    return [
        {
            "id": row["id"],
            "account_id": row["account_id"],
            "agent_id": row["agent_id"],
            "url": row["url"],
            "secret": row["secret"][:12] + "..." if row["secret"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


@router.get("/{webhook_id}")
async def get_webhook(
    webhook_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Get a single webhook by ID. The secret is masked."""
    row = await db.fetchrow(
        "SELECT * FROM webhooks WHERE id=$1 AND account_id=$2",
        webhook_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Webhook not found.")

    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "agent_id": row["agent_id"],
        "url": row["url"],
        "secret": row["secret"][:12] + "..." if row["secret"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Delete a webhook. AgentLine will stop sending events to this URL.
    """
    row = await db.fetchrow(
        "SELECT id FROM webhooks WHERE id=$1 AND account_id=$2",
        webhook_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Webhook not found.")

    await db.execute("DELETE FROM webhooks WHERE id=$1", webhook_id)

    logger.info("Webhook deleted: %s (account=%s)", webhook_id, account["id"][:12])

    return {"deleted": True, "webhook_id": webhook_id}
