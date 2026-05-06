"""
AgentLine — Telnyx Events Router
Handles Telnyx voice webhooks and media WebSocket connections.
"""

import secrets
import json
import logging

import telnyx
from fastapi import APIRouter, Request, WebSocket

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.webhook_dispatcher import dispatch_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telnyx", tags=["Telnyx Events"])


@router.post("/voice")
async def telnyx_voice_webhook(request: Request):
    """Receive Telnyx call control events."""
    body = await request.json()
    event_type = body["data"]["event_type"]
    payload = body["data"]["payload"]

    call_control_id = payload.get("call_control_id")
    client_state = payload.get("client_state")  # Our internal call_id
    call_id = client_state

    if event_type == "call.initiated":
        logger.info("Call %s initiated", call_id)

    elif event_type == "call.answered":
        # Call connected — start media streaming to our WebSocket
        logger.info("Call %s answered, starting media stream", call_id)
        call = telnyx.Call(call_control_id=call_control_id)
        call.streaming_start(
            stream_url=f"wss://{settings.BASE_URL.replace('https://', '')}/telnyx/media/{call_id}",
            stream_track="inbound_track",
        )

    elif event_type == "call.hangup":
        logger.info("Call %s hung up", call_id)
        if call_id:
            async with get_db_conn() as db:
                await db.execute(
                    "UPDATE calls SET status='completed', ended_at=now() WHERE id=$1 AND status!='completed'",
                    call_id,
                )

    elif event_type == "call.streaming.started":
        logger.info("Media streaming started for call %s", call_id)

    elif event_type == "call.streaming.stopped":
        logger.info("Media streaming stopped for call %s", call_id)

    return {"status": "ok"}


@router.websocket("/media/{call_id}")
async def telnyx_media_ws(websocket: WebSocket, call_id: str):
    """
    WebSocket endpoint that Telnyx streams raw audio to.
    Runs the full voice pipeline: STT → LLM → TTS.
    """
    await websocket.accept()
    logger.info("Media WebSocket connected for call %s", call_id)

    try:
        # Fetch call + agent config
        async with get_db_conn() as db:
            call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
            if not call:
                logger.error("Call %s not found in DB", call_id)
                await websocket.close()
                return

            agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])

        # Import here to avoid circular imports
        from agentline.voice.pipeline import run_pipeline

        await run_pipeline(
            telnyx_ws=websocket,
            call_id=call_id,
            system_prompt=call["system_prompt"] or (agent["system_prompt"] if agent else ""),
            initial_greeting=agent["initial_greeting"] if agent else None,
            voice_id=agent["voice_id"] if agent else "cartesia-sonic-english",
            model_tier=agent["model_tier"] if agent else "balanced",
        )
    except Exception as e:
        logger.exception("Voice pipeline error for call %s: %s", call_id, e)
    finally:
        logger.info("Media WebSocket closed for call %s", call_id)


@router.post("/sms")
async def telnyx_sms_webhook(request: Request):
    """Receive inbound SMS from Telnyx and dispatch to customer webhook."""
    body = await request.json()
    payload = body["data"]["payload"]

    direction = payload.get("direction", "inbound")
    if direction != "inbound":
        return {"status": "skipped"}

    from_number = payload["from"]["phone_number"]
    to_number = payload["to"][0]["phone_number"]
    text = payload.get("text", "")

    async with get_db_conn() as db:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE phone_number=$1", to_number,
        )
        if not number:
            return {"status": "unknown_number"}

        # Upsert conversation
        conv = await db.fetchrow(
            "SELECT * FROM conversations WHERE number_id=$1 AND contact_number=$2",
            number["id"], from_number,
        )
        if not conv:
            conv_id = f"conv_{secrets.token_urlsafe(12)}"
            await db.execute(
                """INSERT INTO conversations (id, account_id, agent_id, number_id, contact_number)
                   VALUES ($1,$2,$3,$4,$5)""",
                conv_id, number["account_id"], number["agent_id"],
                number["id"], from_number,
            )
        else:
            conv_id = conv["id"]

        # Save inbound message
        msg_id = f"msg_{secrets.token_urlsafe(12)}"
        await db.execute(
            """INSERT INTO messages
               (id, account_id, agent_id, number_id, conversation_id,
                direction, from_number, to_number, body)
               VALUES ($1,$2,$3,$4,$5,'inbound',$6,$7,$8)""",
            msg_id, number["account_id"], number["agent_id"],
            number["id"], conv_id, from_number, to_number, text,
        )

    # Fire webhook to customer
    await dispatch_webhook(number["account_id"], number["agent_id"], {
        "event": "agent.message",
        "channel": "sms",
        "agent_id": number["agent_id"],
        "number_id": number["id"],
        "from_number": from_number,
        "to_number": to_number,
        "content": text,
        "conversation_id": conv_id,
    })

    return {"status": "ok"}
