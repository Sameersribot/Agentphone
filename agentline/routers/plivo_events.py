"""
AgentLine — Plivo Events Router (Relay Mode)
Handles all Plivo webhooks for voice and SMS.

Voice Architecture (Agent-Controlled Relay — no LLM layer):
  1. Agent creates call via POST /v1/calls with greeting text
  2. Plivo calls the person → speaks the greeting
  3. Person speaks → Plivo captures speech via <GetInput>
  4. AgentLine saves transcript + dispatches webhook to agent
  5. Call enters wait loop → polls for agent's response
  6. Agent sends response via POST /v1/calls/{id}/speak
  7. AgentLine speaks the response → listens again → loop

Plivo webhooks are form-encoded (not JSON).
Plivo voice responses use Plivo XML (not JSON).
"""

import secrets
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.webhook_dispatcher import dispatch_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plivo", tags=["Plivo Events"])


def _xml(body: str) -> Response:
    """Return a Plivo XML response."""
    return Response(content=body, media_type="application/xml")


def _escape_xml(text: str) -> str:
    """Escape special characters for safe XML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ────────────────────────────────────────────────────────────
# Voice — Answer URL (outbound calls)
# When callee answers, speak greeting and start listening.
# ────────────────────────────────────────────────────────────

@router.post("/answer/{call_id}")
async def plivo_answer(request: Request, call_id: str):
    """
    Plivo hits this URL when an outbound call is answered.
    Speaks the greeting, then listens for the caller's speech.
    """
    form = await request.form()
    call_uuid = form.get("CallUUID", "")
    logger.info("Call %s answered (Plivo UUID: %s)", call_id, call_uuid)

    # Save Plivo's call UUID
    if call_uuid:
        async with get_db_conn() as db:
            await db.execute(
                "UPDATE calls SET provider_call_id=$1, status='in-progress' WHERE id=$2",
                call_uuid, call_id,
            )

    # Get the call's greeting text from agent config
    greeting = "Hello, how can I help you today?"
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if call:
            agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])
            if agent and agent.get("initial_greeting"):
                greeting = agent["initial_greeting"]

    # Speak greeting, then listen for speech
    speech_url = f"{settings.base_url_clean}/plivo/speech/{call_id}"

    xml = f"""<Response>
    <Speak voice="Polly.Aditi">{_escape_xml(greeting)}</Speak>
    <GetInput action="{speech_url}" method="POST"
              inputType="speech"
              speechEndTimeout="3" executionTimeout="30"
              language="en-US" profanityFilter="false"
              redirect="true" log="true">
        <Speak voice="Polly.Aditi">I am listening.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">I did not hear anything. Goodbye.</Speak>
</Response>"""

    logger.info("Call %s — greeting: %s", call_id, greeting[:60])
    logger.info("Call %s — XML:\n%s", call_id, xml)
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Speech Input Callback
# Plivo POSTs transcribed speech here.
# We save it, dispatch webhook to agent, then wait for
# the agent to respond via POST /v1/calls/{id}/speak.
# ────────────────────────────────────────────────────────────

@router.post("/speech/{call_id}")
async def plivo_speech_input(request: Request, call_id: str):
    """
    Plivo POSTs transcribed speech from the caller.
    Save transcript, dispatch to agent webhook, then wait for agent response.
    """
    form = await request.form()
    speech = form.get("Speech", "")
    call_uuid = form.get("CallUUID", "")

    logger.info("Call %s — caller said: '%s'", call_id, speech)

    if not speech:
        # No speech — ask again
        speech_url = f"{settings.base_url_clean}/plivo/speech/{call_id}"
        xml = f"""<Response>
    <Speak voice="Polly.Aditi">I did not hear anything. Could you try again?</Speak>
    <GetInput action="{speech_url}" method="POST"
              inputType="speech"
              speechEndTimeout="3" executionTimeout="30"
              language="en-US" profanityFilter="false"
              redirect="true" log="true">
        <Speak voice="Polly.Aditi">I am listening.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">I still could not hear you. Goodbye.</Speak>
</Response>"""
        return _xml(xml)

    # Save the caller's speech to transcript
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if not call:
            return _xml("<Response><Speak>Call not found.</Speak></Response>")

        # Parse existing transcript
        transcript = _parse_transcript(call.get("transcript"))

        # Add caller's speech
        transcript.append({
            "role": "human",
            "text": speech,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Save
        await db.execute(
            "UPDATE calls SET transcript=$1 WHERE id=$2",
            json.dumps(transcript), call_id,
        )

    # Dispatch webhook to agent — agent decides what to say next
    await dispatch_webhook(call["account_id"], call["agent_id"], {
        "event": "call.speech_received",
        "call_id": call_id,
        "provider_call_id": call_uuid or call.get("provider_call_id", ""),
        "speech_text": speech,
        "direction": call.get("direction", "outbound"),
        "from_number": call.get("from_number", ""),
        "to_number": call.get("to_number", ""),
    })

    # Now wait for agent to respond via POST /v1/calls/{id}/speak
    # Use a Redirect loop to poll for the agent's response
    wait_url = f"{settings.base_url_clean}/plivo/wait/{call_id}"

    xml = f"""<Response>
    <Speak voice="Polly.Aditi">One moment please.</Speak>
    <Redirect method="POST">{wait_url}</Redirect>
</Response>"""

    logger.info("Call %s — waiting for agent response", call_id)
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Wait Loop
# Polls for agent's response. When found, speaks it and
# goes back to listening. If timeout, hangs up gracefully.
# ────────────────────────────────────────────────────────────

@router.post("/wait/{call_id}")
async def plivo_wait_for_response(request: Request, call_id: str):
    """
    Polling endpoint — checks if the agent has queued a response.
    If yes: speak it and go back to listening.
    If no: wait 3 seconds and check again (up to ~60 seconds total).
    """
    form = await request.form()

    # Check for a queued response from the agent
    async with get_db_conn() as db:
        queued = await db.fetchrow(
            """SELECT id, response_text FROM call_responses
               WHERE call_id=$1 AND spoken=false
               ORDER BY created_at ASC LIMIT 1""",
            call_id,
        )

        if queued:
            # Mark as spoken
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

            # Speak the response, then listen again
            speech_url = f"{settings.base_url_clean}/plivo/speech/{call_id}"

            xml = f"""<Response>
    <Speak voice="Polly.Aditi">{_escape_xml(response_text)}</Speak>
    <GetInput action="{speech_url}" method="POST"
              inputType="speech"
              speechEndTimeout="3" executionTimeout="30"
              language="en-US" profanityFilter="false"
              redirect="true" log="true">
        <Speak voice="Polly.Aditi">I am listening.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">Thank you for calling. Goodbye.</Speak>
</Response>"""
            return _xml(xml)

        # Check if call is still active
        call = await db.fetchrow(
            "SELECT status FROM calls WHERE id=$1", call_id,
        )
        if not call or call["status"] == "completed":
            return _xml("<Response><Speak>Goodbye.</Speak></Response>")

    # No response yet — wait 3 seconds then check again
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
    """Called by Plivo when call ends."""
    form = await request.form()
    duration = form.get("Duration", "0")
    hangup_cause = form.get("HangupCause", "unknown")

    logger.info("Call %s ended — duration: %ss, cause: %s", call_id, duration, hangup_cause)

    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        await db.execute(
            """UPDATE calls
               SET status='completed', duration_seconds=$1, ended_at=now()
               WHERE id=$2 AND status!='completed'""",
            int(duration) if duration.isdigit() else 0,
            call_id,
        )

    # Dispatch call completed webhook
    if call:
        await dispatch_webhook(call["account_id"], call["agent_id"], {
            "event": "call.completed",
            "call_id": call_id,
            "duration": int(duration) if duration.isdigit() else 0,
            "hangup_cause": hangup_cause,
            "direction": call.get("direction", "unknown"),
        })

    return _xml("<Response/>")


# ────────────────────────────────────────────────────────────
# Voice — Inbound Call
# When someone calls your Plivo number.
# ────────────────────────────────────────────────────────────

@router.post("/inbound")
async def plivo_inbound_call(request: Request):
    """
    Handle inbound call. Look up number → agent, create call record,
    speak greeting, start listening.
    """
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
            logger.warning("Inbound call to unknown number %s", to_number)
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

    speech_url = f"{settings.base_url_clean}/plivo/speech/{call_id}"

    xml = f"""<Response>
    <Speak voice="Polly.Aditi">{_escape_xml(greeting)}</Speak>
    <GetInput action="{speech_url}" method="POST"
              inputType="speech"
              speechEndTimeout="3" executionTimeout="30"
              language="en-US" profanityFilter="false"
              redirect="true" log="true">
        <Speak voice="Polly.Aditi">I am listening.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">I did not hear anything. Goodbye.</Speak>
</Response>"""

    # Dispatch inbound call webhook
    if number:
        await dispatch_webhook(number["account_id"], number["agent_id"], {
            "event": "call.inbound",
            "call_id": call_id,
            "from_number": from_number,
            "to_number": to_number,
        })

    return _xml(xml)


# ────────────────────────────────────────────────────────────
# SMS — Inbound Webhook
# ────────────────────────────────────────────────────────────

@router.post("/sms")
async def plivo_sms_webhook(request: Request):
    """Receive inbound SMS and dispatch to customer webhook."""
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
            logger.warning("SMS to unknown number %s", to_number)
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
    """Safely parse transcript JSON from DB."""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
