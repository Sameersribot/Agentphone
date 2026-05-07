"""
AgentLine — Plivo Events Router
Handles Plivo voice webhooks (XML-based), audio streaming WebSocket, and inbound SMS.
"""

import secrets
import json
import logging
import base64

from fastapi import APIRouter, Request, WebSocket, Form
from fastapi.responses import Response

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.webhook_dispatcher import dispatch_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plivo", tags=["Plivo Events"])


def _plivo_xml(xml_body: str) -> Response:
    """Return a Plivo XML response with the correct content type."""
    return Response(content=xml_body, media_type="application/xml")


# ────────────────────────────────────────────────────────────
# Voice — Answer URL (returns Plivo XML to start streaming)
# ────────────────────────────────────────────────────────────

@router.post("/answer/{call_id}")
async def plivo_answer(request: Request, call_id: str):
    """
    Plivo hits this URL when an outbound call is answered.
    We return XML that starts a bidirectional audio stream to our WebSocket.
    """
    logger.info("Call %s answered — returning Stream XML", call_id)

    host = settings.BASE_URL.replace("https://", "").replace("http://", "").rstrip("/")
    ws_url = f"wss://{host}/plivo/media/{call_id}"

    xml = f"""<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        {ws_url}
    </Stream>
</Response>"""

    return _plivo_xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Hangup URL
# ────────────────────────────────────────────────────────────

@router.post("/hangup/{call_id}")
async def plivo_hangup(request: Request, call_id: str):
    """Plivo notifies us that the call has ended."""
    logger.info("Call %s hung up", call_id)
    if call_id:
        async with get_db_conn() as db:
            await db.execute(
                "UPDATE calls SET status='completed', ended_at=now() WHERE id=$1 AND status!='completed'",
                call_id,
            )
    return _plivo_xml("<Response/>")


# ────────────────────────────────────────────────────────────
# Voice — Inbound call answer URL
# ────────────────────────────────────────────────────────────

@router.post("/inbound")
async def plivo_inbound_call(request: Request):
    """
    Handle an inbound call to a Plivo number.
    We look up the number, create a call record, and start streaming.
    """
    form = await request.form()
    from_number = form.get("From", "")
    to_number = form.get("To", "")
    call_uuid = form.get("CallUUID", "")

    logger.info("Inbound call from %s to %s (UUID: %s)", from_number, to_number, call_uuid)

    async with get_db_conn() as db:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE phone_number=$1 AND status='active'",
            to_number,
        )
        if not number:
            logger.warning("Inbound call to unknown number %s", to_number)
            return _plivo_xml("<Response><Hangup/></Response>")

        agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", number["agent_id"])

        # Create call record
        call_id = f"call_{secrets.token_urlsafe(12)}"
        await db.execute(
            """INSERT INTO calls (id, account_id, agent_id, number_id, provider_call_id,
               direction, from_number, to_number, system_prompt, status, started_at)
               VALUES ($1,$2,$3,$4,$5,'inbound',$6,$7,$8,'in-progress',now())""",
            call_id, number["account_id"], number["agent_id"], number["id"],
            call_uuid, from_number, to_number,
            agent["system_prompt"] if agent else "",
        )

    host = settings.BASE_URL.replace("https://", "").replace("http://", "").rstrip("/")
    ws_url = f"wss://{host}/plivo/media/{call_id}"

    xml = f"""<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        {ws_url}
    </Stream>
</Response>"""

    return _plivo_xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Media WebSocket (bidirectional audio)
# ────────────────────────────────────────────────────────────

@router.websocket("/media/{call_id}")
async def plivo_media_ws(websocket: WebSocket, call_id: str):
    """
    WebSocket endpoint that Plivo streams raw audio to (bidirectional).
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
            provider_ws=websocket,
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


# ────────────────────────────────────────────────────────────
# SMS — Inbound webhook
# ────────────────────────────────────────────────────────────

@router.post("/sms")
async def plivo_sms_webhook(request: Request):
    """Receive inbound SMS from Plivo and dispatch to customer webhook."""
    form = await request.form()

    from_number = form.get("From", "")
    to_number = form.get("To", "")
    text = form.get("Text", "")
    message_uuid = form.get("MessageUUID", "")

    logger.info("Inbound SMS from %s to %s: %s", from_number, to_number, text[:50])

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
                provider_message_id, direction, from_number, to_number, body)
               VALUES ($1,$2,$3,$4,$5,$6,'inbound',$7,$8,$9)""",
            msg_id, number["account_id"], number["agent_id"],
            number["id"], conv_id, message_uuid,
            from_number, to_number, text,
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

    return _plivo_xml("<Response/>")
