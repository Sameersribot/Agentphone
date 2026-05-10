"""
AgentLine — SignalWire Events Router (Hosted LLM Pattern)
Voice Architecture for US numbers:
  STT: SignalWire <Gather input="speech"> (real-time)
  LLM: Internal Hosted LLM (we pass speech to our LLM, generate response)
  TTS: SignalWire <Say> (instant)

  Flow: caller speaks → <Gather> STT → Internal LLM generates response
  → <Say> response → <Gather> next turn.
"""

import asyncio
import secrets
import json
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.voice.llm import llm_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signalwire", tags=["SignalWire Events"])

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

def _gather_xml(call_id: str, prompt: str = "I am listening.") -> str:
    """
    Generate XML: speak a prompt, then gather caller speech in real-time.

    Uses <Gather input="speech"> for instant STT instead of <Record> + Deepgram.
    SignalWire transcribes speech live — result arrives as SpeechResult in the callback.
    """
    gather_url = f"{settings.base_url_clean}/signalwire/gathered/{call_id}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="{gather_url}" method="POST"
            speechTimeout="3" timeout="10" language="en-US">
        <Say voice="alice">{_escape_xml(prompt)}</Say>
    </Gather>
    <Say voice="alice">I did not hear anything. Goodbye.</Say>
</Response>"""


def _listen_xml(call_id: str, prompt: str = "I am listening.") -> str:
    """Legacy: speak + record. Kept for fallback but gather is preferred."""
    record_url = f"{settings.base_url_clean}/signalwire/recorded/{call_id}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">{_escape_xml(prompt)}</Say>
    <Record action="{record_url}" method="POST"
            maxLength="30" timeout="3" finishOnKey="#"
            playBeep="false" />
    <Say voice="alice">I did not hear anything. Goodbye.</Say>
</Response>"""


async def transcribe_with_deepgram(recording_url: str) -> str:
    """Send a recording URL to Deepgram's pre-recorded API."""
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


def _parse_transcript(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except:
            return []
    if isinstance(raw, list):
        return raw
    return []


async def _generate_hosted_llm_response(agent: dict, transcript: list, call_id: str) -> str:
    """Generate response internally for hosted mode agents."""
    logger.info("Call %s — using Hosted Mode internal LLM", call_id)
    system_prompt = (
        (agent.get("system_prompt") if agent else None)
        or "You are a helpful voice assistant. Keep responses brief and conversational."
    )
    model_tier = (agent.get("model_tier") if agent else None) or "balanced"

    chat_history = []
    for entry in transcript:
        # We need to map human -> user and agent -> assistant
        role = "user" if entry.get("role") == "human" else "assistant"
        chat_history.append({"role": role, "content": entry.get("text", "")})

    agent_reply = await llm_response(system_prompt, chat_history, model_tier)
    logger.info("Call %s — Hosted Mode LLM response: %s", call_id, agent_reply[:80])
    return agent_reply





@router.post("/answer/{call_id}")
async def signalwire_answer(request: Request, call_id: str):
    """Call answered — speak greeting, then gather caller's speech in real-time."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    logger.info("Call %s answered (SignalWire SID: %s)", call_id, call_sid)

    if call_sid:
        async with get_db_conn() as db:
            await db.execute(
                "UPDATE calls SET provider_call_id=$1, status='in-progress' WHERE id=$2",
                call_sid, call_id,
            )

    greeting = "Hello, how can I help you today?"
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if call:
            agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])
            if agent and agent.get("initial_greeting"):
                greeting = agent["initial_greeting"]

    xml = _gather_xml(call_id, greeting)
    return _xml(xml)


# ────────────────────────────────────────────────────────────
# Voice — Gather Callback (Hosted LLM Pattern)
# ────────────────────────────────────────────────────────────

@router.post("/gathered/{call_id}")
async def signalwire_gathered(request: Request, call_id: str):
    """
    Real-time speech handler — hosted LLM pattern.

    Flow:
      1. SignalWire transcribes speech in real-time (SpeechResult)
      2. We pass speech + conversation history to our internal hosted LLM
      3. We speak the response and listen for next speech
    """
    form = await request.form()
    speech_text = form.get("SpeechResult", "").strip()
    confidence = form.get("Confidence", "0")
    call_sid = form.get("CallSid", "")

    logger.info("Call %s — gathered speech (confidence=%s): '%s'", call_id, confidence, speech_text)

    if not speech_text:
        xml = _gather_xml(call_id, "I didn't catch that. Could you say that again?")
        return _xml(xml)

    # ── Step 1: Load call + agent context ──
    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if not call:
            return _xml('<?xml version="1.0" encoding="UTF-8"?><Response><Say>Call not found.</Say></Response>')

        agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])

        # Build conversation history and add caller's speech
        transcript = _parse_transcript(call.get("transcript"))
        transcript.append({
            "role": "human",
            "text": speech_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Save transcript immediately
        await db.execute(
            "UPDATE calls SET transcript=$1 WHERE id=$2",
            json.dumps(transcript), call_id,
        )

    # Build recent history (like AgentPhone's recentHistory format)
    recent_history = []
    for entry in transcript[-10:]:  # last 10 turns for context
        recent_history.append({
            "direction": "inbound" if entry.get("role") == "human" else "outbound",
            "content": entry.get("text", ""),
            "timestamp": entry.get("timestamp", ""),
        })

    # ── Step 2: Get Agent Response (Hosted Mode) ──
    agent_reply = await _generate_hosted_llm_response(agent, transcript, call_id)

    # ── Step 4: Save agent reply to transcript ──
    transcript.append({
        "role": "agent",
        "text": agent_reply,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    async with get_db_conn() as db:
        await db.execute(
            "UPDATE calls SET transcript=$1 WHERE id=$2",
            json.dumps(transcript), call_id,
        )

    # Return agent's response + listen for next speech
    xml = _gather_xml(call_id, agent_reply)
    return _xml(xml)





@router.post("/recorded/{call_id}")
async def signalwire_recording_callback(request: Request, call_id: str):
    """SignalWire POSTs here after <Record> captures audio."""
    form = await request.form()
    recording_url = form.get("RecordingUrl", "")
    recording_duration = form.get("RecordingDuration", "0")
    call_sid = form.get("CallSid", "")

    logger.info("Call %s — recording received (%ss): %s", call_id, recording_duration, recording_url)

    if not recording_url or recording_duration == "0":
        xml = _listen_xml(call_id, "I did not hear anything. Could you try again?")
        return _xml(xml)

    # SignalWire uses HTTP auth for recording URLs if secure media is enabled.
    # Usually the URL is public unless configured otherwise. We pass it to Deepgram.
    speech_text = await transcribe_with_deepgram(recording_url)
    logger.info("Call %s — Deepgram transcript: '%s'", call_id, speech_text)

    if not speech_text:
        xml = _listen_xml(call_id, "I could not understand that. Could you please repeat?")
        return _xml(xml)

    async with get_db_conn() as db:
        call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
        if not call:
            return _xml("<Response><Say>Call not found.</Say></Response>")

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

    # Build recent history
    recent_history = []
    for entry in transcript[-10:]:
        recent_history.append({
            "direction": "inbound" if entry.get("role") == "human" else "outbound",
            "content": entry.get("text", ""),
            "timestamp": entry.get("timestamp", ""),
        })

    # ── Step 2: Get Agent Response (Hosted Mode) ──
    agent_reply = await _generate_hosted_llm_response(agent, transcript, call_id)

    # Save agent reply
    transcript.append({
        "role": "agent",
        "text": agent_reply,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    async with get_db_conn() as db:
        await db.execute(
            "UPDATE calls SET transcript=$1 WHERE id=$2",
            json.dumps(transcript), call_id,
        )

    # Return agent's response + listen for next speech
    xml = _gather_xml(call_id, agent_reply)
    return _xml(xml)


@router.post("/wait/{call_id}")
async def signalwire_wait_for_response(request: Request, call_id: str):
    """
    Server-side polling wait loop.

    Instead of using TwiML <Pause>+<Redirect> loops (which hit SignalWire's
    ~10 redirect depth limit after ~20 seconds), we hold the HTTP connection
    open and poll the DB server-side for up to 55 seconds.

    This gives the agent plenty of time to process speech and call /speak.
    """
    # Poll the DB every 2 seconds for up to 55 seconds
    for i in range(27):  # 27 iterations × 2s = 54s max
        async with get_db_conn() as db:
            # Check for queued response from the agent
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
                logger.info("Call %s — agent says (after %ds): %s", call_id, i * 2, response_text[:80])

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

                # Speak the response and gather the caller's next reply
                xml = _gather_xml(call_id, response_text)
                return _xml(xml)

            # Check if call ended externally
            call = await db.fetchrow("SELECT status FROM calls WHERE id=$1", call_id)
            if not call or call["status"] == "completed":
                return _xml("""<?xml version="1.0" encoding="UTF-8"?>
<Response><Say voice="alice">Goodbye.</Say></Response>""")

        # Wait 2 seconds before next check
        await asyncio.sleep(2)

    # Timed out waiting for agent — redirect back for another round
    logger.warning("Call %s — wait loop timed out after 54s, looping again", call_id)
    wait_url = f"{settings.base_url_clean}/signalwire/wait/{call_id}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">{wait_url}</Redirect>
</Response>"""
    return _xml(xml)


@router.post("/sms")
async def signalwire_sms_callback(request: Request):
    form = await request.form()
    from_number = form.get("From", "")
    to_number = form.get("To", "")
    text = form.get("Body", "")
    message_sid = form.get("MessageSid", "")
    msg_type = "sms"

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
            number["id"], conv_id, message_sid,
            from_number, to_number, text,
        )

    return _xml("<Response/>")


# ────────────────────────────────────────────────────────────
# Voice — Hangup (StatusCallback)
# ────────────────────────────────────────────────────────────

@router.post("/hangup/{call_id}")
async def signalwire_hangup(request: Request, call_id: str):
    """SignalWire POSTs here when the call ends (StatusCallback)."""
    form = await request.form()
    call_status = form.get("CallStatus", "")
    duration = form.get("CallDuration", form.get("Duration", "0"))
    call_sid = form.get("CallSid", "")

    logger.info("Call %s status=%s duration=%ss (SID: %s)", call_id, call_status, duration, call_sid)

    # Only act on terminal states
    if call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
        async with get_db_conn() as db:
            call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
            await db.execute(
                """UPDATE calls SET status='completed', duration_seconds=$1, ended_at=now()
                   WHERE id=$2 AND status!='completed'""",
                int(duration) if str(duration).isdigit() else 0, call_id,
            )

    return _xml("<Response/>")


# ────────────────────────────────────────────────────────────
# Voice — Inbound Call on a SignalWire US number
# ────────────────────────────────────────────────────────────

@router.post("/inbound")
async def signalwire_inbound_call(request: Request):
    """Handle incoming calls on SignalWire US numbers."""
    form = await request.form()
    from_number = form.get("From", "")
    to_number = form.get("To", "")
    call_sid = form.get("CallSid", "")

    if from_number and not from_number.startswith("+"):
        from_number = f"+{from_number}"
    if to_number and not to_number.startswith("+"):
        to_number = f"+{to_number}"

    logger.info("Inbound call (SignalWire): %s -> %s (SID: %s)", from_number, to_number, call_sid)

    async with get_db_conn() as db:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE (phone_number=$1 OR phone_number=$2) AND status='active'",
            to_number, to_number.lstrip("+"),
        )
        if not number:
            return _xml("<Response><Say>This number is not configured. Goodbye.</Say></Response>")

        agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", number["agent_id"])

        call_id = f"call_{secrets.token_urlsafe(12)}"
        await db.execute(
            """INSERT INTO calls
               (id, account_id, agent_id, number_id, provider_call_id,
                direction, from_number, to_number, system_prompt, status, started_at)
               VALUES ($1,$2,$3,$4,$5,'inbound',$6,$7,$8,'in-progress',now())""",
            call_id, number["account_id"], number["agent_id"], number["id"],
            call_sid, from_number, to_number,
            agent["system_prompt"] if agent else "",
        )

    greeting = "Hello, how can I help you today?"
    if agent and agent.get("initial_greeting"):
        greeting = agent["initial_greeting"]



    xml = _listen_xml(call_id, greeting)
    return _xml(xml)
