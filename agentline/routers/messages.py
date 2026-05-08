"""
AgentLine — Messages Router
Send and list SMS messages, manage conversations.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.message import MessageSend, MessageOut
from agentline.plivo_client import send_sms as plivo_send_sms
from agentline.signalwire_client import send_sms as signalwire_send_sms

router = APIRouter(prefix="/v1/messages", tags=["Messages"])


@router.post("")
async def send_message(
    body: MessageSend,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Send an outbound SMS from an agent's number."""
    # Get agent
    agent = await db.fetchrow(
        "SELECT * FROM agents WHERE id = $1 AND account_id = $2",
        body.agent_id,
        account["id"],
    )
    if not agent:
        raise HTTPException(404, "Agent not found.")

    # Get the agent's phone number
    if body.from_number_id:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE id = $1 AND account_id = $2 AND status = 'active'",
            body.from_number_id,
            account["id"],
        )
    else:
        number = await db.fetchrow(
            """SELECT * FROM phone_numbers
               WHERE agent_id = $1 AND status = 'active'
               ORDER BY created_at LIMIT 1""",
            body.agent_id,
        )

    if not number:
        raise HTTPException(400, "Agent has no active phone number.")

    # Upsert conversation
    conv = await db.fetchrow(
        "SELECT * FROM conversations WHERE number_id = $1 AND contact_number = $2",
        number["id"],
        body.to_number,
    )
    if not conv:
        conv_id = f"conv_{secrets.token_urlsafe(12)}"
        await db.execute(
            """INSERT INTO conversations
               (id, account_id, agent_id, number_id, contact_number, last_message_at)
               VALUES ($1, $2, $3, $4, $5, now())""",
            conv_id,
            account["id"],
            body.agent_id,
            number["id"],
            body.to_number,
        )
    else:
        conv_id = conv["id"]
        await db.execute(
            "UPDATE conversations SET last_message_at = now() WHERE id = $1",
            conv_id,
        )

    # Send via the provider that owns the from_number
    use_signalwire = number["country"] == "US"
    try:
        if use_signalwire:
            result = await signalwire_send_sms(
                from_number=number["phone_number"],
                to_number=body.to_number,
                body=body.body,
                media_url=body.media_url,
            )
        else:
            result = await plivo_send_sms(
                from_number=number["phone_number"],
                to_number=body.to_number,
                body=body.body,
                media_url=body.media_url,
            )
    except Exception as e:
        raise HTTPException(502, f"SMS delivery failed: {str(e)}")

    # Save message record
    msg_id = f"msg_{secrets.token_urlsafe(12)}"
    now = datetime.now(timezone.utc)
    await db.execute(
        """INSERT INTO messages
           (id, account_id, agent_id, number_id, conversation_id,
            provider_message_id, direction, from_number, to_number, body, media_url, status, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,'outbound',$7,$8,$9,$10,$11,$12)""",
        msg_id,
        account["id"],
        body.agent_id,
        number["id"],
        conv_id,
        result.get("provider_message_id"),
        number["phone_number"],
        body.to_number,
        body.body,
        body.media_url,
        result.get("status", "sent"),
        now,
    )

    return {
        "id": msg_id,
        "conversation_id": conv_id,
        "agent_id": body.agent_id,
        "from_number": number["phone_number"],
        "to_number": body.to_number,
        "body": body.body,
        "direction": "outbound",
        "status": result.get("status", "sent"),
        "created_at": now.isoformat(),
    }


@router.get("")
async def list_messages(
    agent_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """List messages with optional agent/conversation filter."""
    conditions = ["m.account_id = $1"]
    params = [account["id"]]
    idx = 2

    if agent_id:
        conditions.append(f"m.agent_id = ${idx}")
        params.append(agent_id)
        idx += 1

    if conversation_id:
        conditions.append(f"m.conversation_id = ${idx}")
        params.append(conversation_id)
        idx += 1

    where = " AND ".join(conditions)
    params.extend([limit, offset])

    rows = await db.fetch(
        f"""SELECT m.* FROM messages m
            WHERE {where}
            ORDER BY m.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}""",
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/conversations")
async def list_conversations(
    agent_id: str | None = None,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """List all conversations, optionally filtered by agent."""
    if agent_id:
        rows = await db.fetch(
            """SELECT * FROM conversations
               WHERE account_id = $1 AND agent_id = $2
               ORDER BY last_message_at DESC""",
            account["id"],
            agent_id,
        )
    else:
        rows = await db.fetch(
            """SELECT * FROM conversations
               WHERE account_id = $1
               ORDER BY last_message_at DESC""",
            account["id"],
        )
    return [dict(r) for r in rows]
