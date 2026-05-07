"""
AgentLine — Plivo Events Router (Hybrid Relay Mode)
Voice Architecture:
  TTS: Plivo <Speak> with Polly voices (FREE)
  STT: Plivo <Record> → Deepgram transcription (CHEAP + ACCURATE)

Flow:
  1. Agent creates call → Plivo calls person → <Speak> greeting
  2. <Record> captures caller's speech → Plivo stores recording
  3. Plivo POSTs recording URL to /plivo/recorded/{call_id}
  4. We send recording to Deepgram for transcription
  5. Transcript dispatched to agent via webhook
  6. Call enters wait loop for agent's response
  7. Agent responds via POST /v1/calls/{id}/speak
  8. <Speak> the response → <Record> again → loop
"""

import secrets
import json
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.webhook_dispatcher import dispatch_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plivo", tags=["Plivo Events"])


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _record_xml(call_id: str) -> str:
    """Generate the <Record> XML block that captures caller speech."""
    record_url = f"{settings.base_url_clean}/plivo/recorded/{call_id}"
    return f"""<Record action="{record_url}" method="POST"
            maxLength="30" timeout="5" finishOnKey="#"
            playBeep="false" redirect="true"/>"""


def _listen_xml(call_id: str, prompt: str = "I am listening.") -> str:
    """Generate XML: speak a prompt, then record the caller's response."""
    record_url = f"{settings.base_url_clean}/plivo/recorded/{call_id}"
    return f"""<Response>
    <Speak voice="Polly.Aditi">{_escape_xml(prompt)}</Speak>
    <Record action="{record_url}" method="POST"
            maxLength="30" timeout="5" finishOnKey="#"
            playBeep="false" redirect="true"/>
    <Speak voice="Polly.Aditi">I did not hear anything. Goodbye.</Speak>
</Response>"""


# ────────────────────────────────────────────────────────────
# Deepgram STT — transcribe a recording URL
# ────────────────────────────────────────────────────────────

async def transcribe_with_deepgram(recording_url: str) -> str:
    """
    Send a recording URL to Deepgram's pre-recorded API.
    Returns the transcribed text.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true&language=en",
                headers={
                    "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"url": recording_url},
            )
            response.raise_for_status()
            data = response.json()

            # Extract transcript from Deepgram response
            transcript = (
                data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("transcript", "")
            )
            return transcript.strip()
    except Exception as e:
        logger.error("Deepgram transcription failed: %s", e)
        return ""


# ────────────────────────────────────────────────────────────
# Voice — Answer URL (outbound calls)
# ────────────────────────────────────────────────────────────

@router.post("/answer/{call_id}")
async def plivo_answer(request: Request, call_id: str):
    """Call answered — speak greeting, then record caller's response."""
    form = await request.form()
    call_uuid = form.get("CallUUID", "")
    logger.info("Call %s answered (Plivo UUID: %s)", call_id, call_uuid)

    if call_uuid:
        async with get_db_conn() as db:
            await db.execute(
                "UPDATE calls SET provider_call_id=$1, status='in-progress' WHERE id=$2",
                call_uuid, call_id,
            )

    # Get greeting from agent config
    greeting = "Hello, how can I help you today?"
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if call:
            agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])
            if agent and agent.get("initial_greeting"):
                greeting = agent["initial_greeting"]

    xml = _listen_xml(call_id, greeting)
    logger.info("Call %s — greeting: %s", call_id, greeting[:60])
    logger.info("Call %s — XML:\n%s", call_id, xml)
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Recording Callback
# Plivo POSTs here when <Record> finishes.
# We get the recording URL, send to Deepgram, dispatch to agent.
# ────────────────────────────────────────────────────────────

@router.post("/recorded/{call_id}")
async def plivo_recording_callback(request: Request, call_id: str):
    """
    Plivo POSTs here after <Record> captures audio.
    Download recording → Deepgram STT → save transcript → webhook → wait loop.
    """
    form = await request.form()
    recording_url = form.get("RecordUrl", "")
    recording_duration = form.get("RecordingDuration", "0")
    call_uuid = form.get("CallUUID", "")

    logger.info("Call %s — recording received (%ss): %s", call_id, recording_duration, recording_url)

    # Skip if no recording (caller hung up or silence)
    if not recording_url or recording_duration == "0":
        logger.info("Call %s — empty recording, asking again", call_id)
        xml = _listen_xml(call_id, "I did not hear anything. Could you try again?")
        return _xml(xml)

    # Transcribe with Deepgram
    speech_text = await transcribe_with_deepgram(recording_url)
    logger.info("Call %s — Deepgram transcript: '%s'", call_id, speech_text)

    if not speech_text:
        xml = _listen_xml(call_id, "I could not understand that. Could you please repeat?")
        return _xml(xml)

    # Save to transcript
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if not call:
            return _xml("<Response><Speak>Call not found.</Speak></Response>")

        transcript = _parse_transcript(call.get("transcript"))
        transcript.append({
            "role": "human",
            "text": speech_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await db.execute(
            "UPDATE calls SET transcript=$1 WHERE id=$2",
            json.dumps(transcript), call_id,
        )

    # Dispatch to agent's webhook
    await dispatch_webhook(call["account_id"], call["agent_id"], {
        "event": "call.speech_received",
        "call_id": call_id,
        "provider_call_id": call_uuid or call.get("provider_call_id", ""),
        "speech_text": speech_text,
        "direction": call.get("direction", "outbound"),
        "from_number": call.get("from_number", ""),
        "to_number": call.get("to_number", ""),
    })

    # Enter wait loop for agent's response
    wait_url = f"{settings.base_url_clean}/plivo/wait/{call_id}"
    xml = f"""<Response>
    <Speak voice="Polly.Aditi">One moment please.</Speak>
    <Redirect method="POST">{wait_url}</Redirect>
</Response>"""

    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Wait Loop
# Polls for agent's response queued via POST /v1/calls/{id}/speak
# ────────────────────────────────────────────────────────────

@router.post("/wait/{call_id}")
async def plivo_wait_for_response(request: Request, call_id: str):
    """Check if agent has queued a response. If yes, speak + listen. If no, wait."""
    async with get_db_conn() as db:
        # Check for queued response
        queued = await db.fetchrow(
            """SELECT id, response_text FROM call_responses
               WHERE call_id=$1 AND spoken=false
               ORDER BY created_at ASC LIMIT 1""",
            call_id,
        )

        if queued:
            await db.execute(
                "UPDATE call_responses SET spoken=true WHERE id=$1",
                queued["id"],
            )

            response_text = queued["response_text"]
            logger.info("Call %s — agent says: %s", call_id, response_text[:80])

            # Add to transcript
            call = await db.fetchrow("SELECT transcript FROM calls WHERE id=$1", call_id)
            transcript = _parse_transcript(call.get("transcript") if call else None)
            transcript.append({
                "role": "agent",
                "text": response_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await db.execute(
                "UPDATE calls SET transcript=$1 WHERE id=$2",
                json.dumps(transcript), call_id,
            )

            # Speak response, then record again
            xml = _listen_xml(call_id, response_text)
            return _xml(xml)

        # Check if call still active
        call = await db.fetchrow("SELECT status FROM calls WHERE id=$1", call_id)
        if not call or call["status"] == "completed":
            return _xml("<Response><Speak>Goodbye.</Speak></Response>")

    # No response yet — wait 3 seconds and check again
    wait_url = f"{settings.base_url_clean}/plivo/wait/{call_id}"
    xml = f"""<Response>
    <Wait length="3"/>
    <Redirect method="POST">{wait_url}</Redirect>
</Response>"""
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Hangup URL
# ────────────────────────────────────────────────────────────

@router.post("/hangup/{call_id}")
async def plivo_hangup(request: Request, call_id: str):
    form = await request.form()
    duration = form.get("Duration", "0")
    hangup_cause = form.get("HangupCause", "unknown")
    logger.info("Call %s ended — duration: %ss, cause: %s", call_id, duration, hangup_cause)

    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        await db.execute(
            """UPDATE calls SET status='completed', duration_seconds=$1, ended_at=now()
               WHERE id=$2 AND status!='completed'""",
            int(duration) if duration.isdigit() else 0, call_id,
        )

    if call:
        await dispatch_webhook(call["account_id"], call["agent_id"], {
            "event": "call.completed",
            "call_id": call_id,
            "duration": int(duration) if duration.isdigit() else 0,
            "hangup_cause": hangup_cause,
        })

    return _xml("<Response/>")


# ────────────────────────────────────────────────────────────
# Voice — Inbound Call
# ────────────────────────────────────────────────────────────

@router.post("/inbound")
async def plivo_inbound_call(request: Request):
    form = await request.form()
    from_number = form.get("From", "")
    to_number = form.get("To", "")
    call_uuid = form.get("CallUUID", "")

    if from_number and not from_number.startswith("+"):
        from_number = f"+{from_number}"
    if to_number and not to_number.startswith("+"):
        to_number = f"+{to_number}"

    logger.info("Inbound call: %s -> %s (UUID: %s)", from_number, to_number, call_uuid)

    async with get_db_conn() as db:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE (phone_number=$1 OR phone_number=$2) AND status='active'",
            to_number, to_number.lstrip("+"),
        )
        if not number:
            return _xml("<Response><Hangup/></Response>")

        agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", number["agent_id"])

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

    greeting = "Hello, how can I help you today?"
    if agent and agent.get("initial_greeting"):
        greeting = agent["initial_greeting"]

    if number:
        await dispatch_webhook(number["account_id"], number["agent_id"], {
            "event": "call.inbound",
            "call_id": call_id,
            "from_number": from_number,
            "to_number": to_number,
        })

    xml = _listen_xml(call_id, greeting)
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# SMS — Inbound Webhook
# ────────────────────────────────────────────────────────────

@router.post("/sms")
async def plivo_sms_webhook(request: Request):
    form = await request.form()
    from_number = form.get("From", "")
    to_number = form.get("To", "")
    text = form.get("Text", "")
    message_uuid = form.get("MessageUUID", "")
    msg_type = form.get("Type", "sms")

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
            return {"status": "unknown_number"}

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


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _parse_transcript(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
