"""
AgentLine — Plivo Events Router
Handles all Plivo webhooks for voice and SMS:
  - Voice: answer URL (returns XML with Speak/GetInput), hangup URL, inbound calls
  - Speech input: Plivo captures speech via GetInput and POSTs transcribed text
  - SMS: inbound message webhook

Architecture (no WebSocket needed):
  Agent provides text → Plivo <Speak> reads it on the call
  Caller speaks → Plivo <GetInput speech> captures it
  Plivo POSTs transcribed text → we save & dispatch to agent's webhook
  Agent responds via API → next call leg speaks it

Plivo webhooks are form-encoded (not JSON).
Plivo voice responses use Plivo XML (not JSON).
"""

import secrets
import json
import logging

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


# ────────────────────────────────────────────────────────────
# Voice — Answer URL (outbound calls)
# When callee answers, Plivo POSTs here.
# We speak the agent's initial_greeting / system_prompt,
# then listen for the caller's speech with GetInput.
# ────────────────────────────────────────────────────────────

@router.post("/answer/{call_id}")
async def plivo_answer(request: Request, call_id: str):
    """
    Plivo hits this URL when an outbound call is answered.
    Returns XML that speaks the greeting and starts listening.
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

    # Get the call's greeting text
    greeting = "Hello, how can I help you today?"
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if call:
            agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])
            if agent and agent.get("initial_greeting"):
                greeting = agent["initial_greeting"]
            elif call.get("system_prompt"):
                # Use a short intro based on the system prompt
                greeting = "Hello, I'm your assistant. How can I help you?"

    # Build the response: Speak greeting, then listen for speech
    input_callback = f"{settings.base_url_clean}/plivo/speech/{call_id}"
    
    xml = f"""<Response>
    <Speak voice="Polly.Aditi">{_escape_xml(greeting)}</Speak>
    <GetInput action="{input_callback}" method="POST"
              inputType="speech" speechModel="enhanced"
              speechEndTimeout="2" executionTimeout="30"
              language="en-IN" profanityFilter="false"
              log="true">
        <Speak voice="Polly.Aditi">I'm listening.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">I didn't catch that. Goodbye.</Speak>
</Response>"""

    logger.info("Call %s — speaking greeting: %s", call_id, greeting[:60])
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Speech Input Callback
# When Plivo captures speech via GetInput, it POSTs the
# transcribed text here. We save it and call the agent's
# webhook, then optionally speak a response.
# ────────────────────────────────────────────────────────────

@router.post("/speech/{call_id}")
async def plivo_speech_input(request: Request, call_id: str):
    """
    Plivo POSTs here with the transcribed speech from the caller.
    We save the transcript, dispatch a webhook, and respond.
    """
    form = await request.form()
    speech = form.get("Speech", "")
    call_uuid = form.get("CallUUID", "")
    
    logger.info("Call %s — speech received: '%s'", call_id, speech)

    if not speech:
        # No speech detected — ask again or hang up
        input_callback = f"{settings.base_url_clean}/plivo/speech/{call_id}"
        xml = f"""<Response>
    <Speak voice="Polly.Aditi">I didn't hear anything. Could you please try again?</Speak>
    <GetInput action="{input_callback}" method="POST"
              inputType="speech" speechModel="enhanced"
              speechEndTimeout="2" executionTimeout="30"
              language="en-IN" profanityFilter="false"
              log="true">
        <Speak voice="Polly.Aditi">I'm listening.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">I still couldn't hear you. Goodbye.</Speak>
</Response>"""
        return _xml(xml)

    # Save the transcribed speech to the call's transcript
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if not call:
            return _xml("<Response><Speak>Call not found.</Speak></Response>")

        # Parse existing transcript
        existing_transcript = []
        if call.get("transcript"):
            try:
                existing_transcript = json.loads(call["transcript"]) if isinstance(call["transcript"], str) else call["transcript"]
            except (json.JSONDecodeError, TypeError):
                existing_transcript = []

        # Add human's speech to transcript
        existing_transcript.append({
            "role": "human",
            "text": speech,
            "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        })

        # Save updated transcript
        await db.execute(
            "UPDATE calls SET transcript=$1 WHERE id=$2",
            json.dumps(existing_transcript),
            call_id,
        )

        # Get agent info for webhook dispatch
        agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])

    # Dispatch webhook to the agent — this is how the agent gets the speech text
    if call:
        await dispatch_webhook(call["account_id"], call["agent_id"], {
            "event": "call.speech_received",
            "call_id": call_id,
            "provider_call_id": call_uuid,
            "speech_text": speech,
            "direction": call.get("direction", "outbound"),
            "from_number": call.get("from_number", ""),
            "to_number": call.get("to_number", ""),
        })

    # Now generate a response — check if there's a queued response from the agent
    async with get_db_conn() as db:
        queued = await db.fetchrow(
            "SELECT response_text FROM call_responses WHERE call_id=$1 AND spoken=false ORDER BY created_at LIMIT 1",
            call_id,
        )
        if queued:
            response_text = queued["response_text"]
            await db.execute(
                "UPDATE call_responses SET spoken=true WHERE call_id=$1 AND response_text=$2",
                call_id, response_text,
            )
        else:
            # Use LLM to generate a response if no queued response
            try:
                from agentline.voice.llm import llm_response
                
                system_prompt = call.get("system_prompt", "") or (
                    agent.get("system_prompt", "") if agent else ""
                ) or "You are a helpful phone assistant. Keep responses brief."
                
                # Build conversation from transcript
                conversation = []
                for turn in existing_transcript:
                    if turn["role"] == "human":
                        conversation.append({"role": "user", "content": turn["text"]})
                    elif turn["role"] == "agent":
                        conversation.append({"role": "assistant", "content": turn["text"]})
                
                model_tier = agent.get("model_tier", "balanced") if agent else "balanced"
                response_text = await llm_response(system_prompt, conversation, model_tier)
            except Exception as e:
                logger.error("LLM response failed for call %s: %s", call_id, e)
                response_text = "I received your message. Is there anything else I can help with?"

    # Add agent response to transcript
    async with get_db_conn() as db:
        call_fresh = await db.fetchrow("SELECT transcript FROM calls WHERE id=$1", call_id)
        transcript = []
        if call_fresh and call_fresh.get("transcript"):
            try:
                transcript = json.loads(call_fresh["transcript"]) if isinstance(call_fresh["transcript"], str) else call_fresh["transcript"]
            except (json.JSONDecodeError, TypeError):
                transcript = []

        transcript.append({
            "role": "agent",
            "text": response_text,
            "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        })

        await db.execute(
            "UPDATE calls SET transcript=$1 WHERE id=$2",
            json.dumps(transcript),
            call_id,
        )

    # Speak the response and continue listening
    input_callback = f"{settings.base_url_clean}/plivo/speech/{call_id}"
    
    xml = f"""<Response>
    <Speak voice="Polly.Aditi">{_escape_xml(response_text)}</Speak>
    <GetInput action="{input_callback}" method="POST"
              inputType="speech" speechModel="enhanced"
              speechEndTimeout="2" executionTimeout="30"
              language="en-IN" profanityFilter="false"
              log="true">
        <Speak voice="Polly.Aditi">Please go ahead.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">Thank you for calling. Goodbye.</Speak>
</Response>"""

    logger.info("Call %s — agent responding: %s", call_id, response_text[:80])
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Respond to active call (API-driven)
# Agent sends text via API → queued for next speech turn
# ────────────────────────────────────────────────────────────

@router.post("/respond/{call_id}")
async def queue_response(request: Request, call_id: str):
    """
    Queue a text response to be spoken on an active call.
    The agent calls this endpoint with text, and it gets spoken
    on the next speech turn.
    """
    body = await request.json()
    text = body.get("text", "")
    
    if not text:
        return {"error": "text is required"}

    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT id FROM calls WHERE id=$1", call_id)
        if not call:
            return {"error": "call not found"}

        try:
            await db.execute(
                """INSERT INTO call_responses (call_id, response_text, spoken, created_at)
                   VALUES ($1, $2, false, now())""",
                call_id, text,
            )
        except Exception as e:
            # Table might not exist — create it
            logger.warning("call_responses insert failed, creating table: %s", e)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS call_responses (
                    id SERIAL PRIMARY KEY,
                    call_id TEXT REFERENCES calls(id),
                    response_text TEXT NOT NULL,
                    spoken BOOLEAN DEFAULT false,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            await db.execute(
                """INSERT INTO call_responses (call_id, response_text, spoken, created_at)
                   VALUES ($1, $2, false, now())""",
                call_id, text,
            )

    return {"queued": True, "call_id": call_id, "text": text}


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
# When someone calls your Plivo number, Plivo POSTs here.
# ────────────────────────────────────────────────────────────

@router.post("/inbound")
async def plivo_inbound_call(request: Request):
    """
    Handle inbound call to a Plivo number.
    Looks up the number → agent, creates a call record, speaks greeting.
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
        # Find the number in our DB
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

    # Speak greeting and start listening
    greeting = "Hello, how can I help you today?"
    if agent and agent.get("initial_greeting"):
        greeting = agent["initial_greeting"]

    input_callback = f"{settings.base_url_clean}/plivo/speech/{call_id}"

    xml = f"""<Response>
    <Speak voice="Polly.Aditi">{_escape_xml(greeting)}</Speak>
    <GetInput action="{input_callback}" method="POST"
              inputType="speech" speechModel="enhanced"
              speechEndTimeout="2" executionTimeout="30"
              language="en-IN" profanityFilter="false"
              log="true">
        <Speak voice="Polly.Aditi">I'm listening.</Speak>
    </GetInput>
    <Speak voice="Polly.Aditi">I didn't catch that. Goodbye.</Speak>
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


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _escape_xml(text: str) -> str:
    """Escape special characters for safe XML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
