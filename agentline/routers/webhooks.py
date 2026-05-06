"""
AgentLine — Webhooks Router
Configure webhook endpoints for event delivery.
"""

import secrets
import hmac
import hashlib

from fastapi import APIRouter, Depends, HTTPException

from agentline.auth_middleware import get_current_account
from agentline.config import settings
from agentline.database import get_db
from agentline.models.webhook import WebhookCreate, WebhookUpdate

router = APIRouter(prefix="/v1/webhooks", tags=["Webhooks"])


def _generate_webhook_secret(account_id: str) -> str:
    """Derive a per-webhook signing secret."""
    raw = f"{settings.WEBHOOK_SECRET_SALT}:{account_id}:{secrets.token_urlsafe(16)}"
    return hmac.new(
        settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()[:40]


@router.post("")
async def create_webhook(
    body: WebhookCreate,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Register a webhook URL for event delivery."""
    webhook_id = f"wh_{secrets.token_urlsafe(12)}"
    secret = _generate_webhook_secret(account["id"])

    if body.agent_id:
        agent = await db.fetchrow(
            "SELECT id FROM agents WHERE id=$1 AND account_id=$2",
            body.agent_id, account["id"],
        )
        if not agent:
            raise HTTPException(404, "Agent not found.")

    await db.execute(
        """INSERT INTO webhooks (id, account_id, agent_id, url, secret)
           VALUES ($1,$2,$3,$4,$5)""",
        webhook_id, account["id"], body.agent_id, body.url, secret,
    )

    return {
        "id": webhook_id, "url": body.url,
        "agent_id": body.agent_id, "secret": secret,
        "message": "Save this secret — use it to verify webhook signatures.",
    }


@router.get("")
async def list_webhooks(account=Depends(get_current_account), db=Depends(get_db)):
    """List all configured webhooks."""
    rows = await db.fetch(
        "SELECT * FROM webhooks WHERE account_id=$1 ORDER BY created_at DESC",
        account["id"],
    )
    return [dict(r) for r in rows]


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Delete a webhook."""
    row = await db.fetchrow(
        "SELECT * FROM webhooks WHERE id=$1 AND account_id=$2",
        webhook_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Webhook not found.")
    await db.execute("DELETE FROM webhooks WHERE id=$1", webhook_id)
    return {"deleted": True, "webhook_id": webhook_id}
