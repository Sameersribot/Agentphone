"""
AgentLine — Plivo Events Router
Handles all Plivo webhooks and WebSocket streams:
  - Voice: answer URL (returns XML), hangup URL, inbound calls
  - Audio: bidirectional WebSocket stream for STT/LLM/TTS pipeline
  - SMS:   inbound message webhook

Plivo webhooks are form-encoded (not JSON).
Plivo voice responses use Plivo XML (not JSON).
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


def _xml(body: str) -> Response:
    """Return a Plivo XML response."""
    return Response(content=body, media_type="application/xml")


# ────────────────────────────────────────────────────────────
# Voice — Answer URL (outbound calls)
# Plivo POSTs here when callee picks up.
# We return XML with <Stream bidirectional> to start audio pipeline.
# ────────────────────────────────────────────────────────────

@router.post("/answer/{call_id}")
async def plivo_answer(request: Request, call_id: str):
    """
    Plivo hits this URL when an outbound call is answered.
    Returns Plivo XML that opens a bidirectional audio WebSocket.
    """
    form = await request.form()
    call_uuid = form.get("CallUUID", "")
    logger.info("Call %s answered (Plivo UUID: %s) — starting stream", call_id, call_uuid)

    # Save Plivo's call UUID
    if call_uuid:
        async with get_db_conn() as db:
            await db.execute(
                "UPDATE calls SET provider_call_id=$1, status='in-progress' WHERE id=$2",
                call_uuid, call_id,
            )

    # Build WebSocket URL for bidirectional audio
    host = settings.BASE_URL.replace("https://", "").replace("http://", "").rstrip("/")
    ws_url = f"wss://{host}/plivo/media/{call_id}"

    xml = f"""<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        {ws_url}
    </Stream>
</Response>"""

    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Hangup URL
# Plivo POSTs here when the call ends.
# ────────────────────────────────────────────────────────────

@router.post("/hangup/{call_id}")
async def plivo_hangup(request: Request, call_id: str):
    """Called by Plivo when call ends."""
    form = await request.form()
    duration = form.get("Duration", "0")
    hangup_cause = form.get("HangupCause", "unknown")

    logger.info("Call %s ended — duration: %ss, cause: %s", call_id, duration, hangup_cause)

    async with get_db_conn() as db:
        await db.execute(
            """UPDATE calls
               SET status='completed', duration_seconds=$1, ended_at=now()
               WHERE id=$2 AND status!='completed'""",
            int(duration) if duration.isdigit() else 0,
            call_id,
        )

    return _xml("<Response/>")


# ────────────────────────────────────────────────────────────
# Voice — Inbound Call
# When someone calls your Plivo number, Plivo POSTs here.
# Set this URL as Answer URL in Plivo Console → Phone Numbers.
# ────────────────────────────────────────────────────────────

@router.post("/inbound")
async def plivo_inbound_call(request: Request):
    """
    Handle inbound call to a Plivo number.
    Looks up the number → agent, creates a call record, starts streaming.
    """
    form = await request.form()
    from_number = form.get("From", "")
    to_number = form.get("To", "")
    call_uuid = form.get("CallUUID", "")

    # Normalize to E.164
    if from_number and not from_number.startswith("+"):
        from_number = f"+{from_number}"
    if to_number and not to_number.startswith("+"):
        to_number = f"+{to_number}"

    logger.info("Inbound call: %s → %s (UUID: %s)", from_number, to_number, call_uuid)

    async with get_db_conn() as db:
        # Find the number in our DB (try with and without +)
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE (phone_number=$1 OR phone_number=$2) AND status='active'",
            to_number, to_number.lstrip("+"),
        )
        if not number:
            logger.warning("Inbound call to unknown number %s — hanging up", to_number)
            return _xml("<Response><Hangup/></Response>")

        agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", number["agent_id"])

        # Create call record
        call_id = f"call_{secrets.token_urlsafe(12)}"
        await db.execute(
            """INSERT INTO calls
               (id, account_id, agent_id, number_id, provider_call_id,
                direction, from_number, to_number, system_prompt, status, started_at)
               VALUES ($1,$2,$3,$4,$5,'inbound',$6,$7,$8,'in-progress',now())""",
            call_id, number["account_id"], number["agent_id"], number["id"],
            call_uuid, from_number, to_number,
            agent["system_prompt"] if agent else "",
        )

    # Start bidirectional audio stream
    host = settings.BASE_URL.replace("https://", "").replace("http://", "").rstrip("/")
    ws_url = f"wss://{host}/plivo/media/{call_id}"

    xml = f"""<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        {ws_url}
    </Stream>
</Response>"""

    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Media WebSocket (bidirectional audio)
# Plivo connects here and streams raw audio chunks.
# We run: Plivo audio → Deepgram STT → LLM → Cartesia TTS → Plivo audio
# ────────────────────────────────────────────────────────────

@router.websocket("/media/{call_id}")
async def plivo_media_ws(websocket: WebSocket, call_id: str):
    """
    Bidirectional audio WebSocket.
    Receives mulaw 8kHz audio from Plivo, runs voice pipeline,
    sends audio back using the 'playAudio' event.
    """
    await websocket.accept()
    logger.info("Media stream connected for call %s", call_id)

    try:
        async with get_db_conn() as db:
            call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
            if not call:
                logger.error("Call %s not found — closing stream", call_id)
                await websocket.close()
                return
            agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])

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
        logger.exception("Voice pipeline error (call %s): %s", call_id, e)
    finally:
        logger.info("Media stream closed for call %s", call_id)


# ────────────────────────────────────────────────────────────
# SMS — Inbound Webhook
# Plivo POSTs form-encoded: From, To, Text, MessageUUID, Type
# Set this as Message URL in Plivo Console → Phone Numbers.
# ────────────────────────────────────────────────────────────

@router.post("/sms")
async def plivo_sms_webhook(request: Request):
    """Receive inbound SMS and dispatch to customer webhook."""
    form = await request.form()

    from_number = form.get("From", "")
    to_number = form.get("To", "")
    text = form.get("Text", "")
    message_uuid = form.get("MessageUUID", "")
    msg_type = form.get("Type", "sms")  # sms, mms, whatsapp

    # Normalize to E.164
    if from_number and not from_number.startswith("+"):
        from_number = f"+{from_number}"
    if to_number and not to_number.startswith("+"):
        to_number = f"+{to_number}"

    logger.info("Inbound %s from %s to %s: %s", msg_type, from_number, to_number, text[:80])

    async with get_db_conn() as db:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE (phone_number=$1 OR phone_number=$2)",
            to_number, to_number.lstrip("+"),
        )
        if not number:
            logger.warning("SMS to unknown number %s", to_number)
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
        "channel": msg_type,
        "agent_id": number["agent_id"],
        "number_id": number["id"],
        "from_number": from_number,
        "to_number": to_number,
        "content": text,
        "conversation_id": conv_id,
    })

    return _xml("<Response/>")
