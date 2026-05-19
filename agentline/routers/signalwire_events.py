"""
AgentLine — SignalWire Events Router (Hosted LLM Pattern)
Voice Architecture for US numbers:
  STT: SignalWire <Gather input="speech"> (real-time)
  LLM: Internal Hosted LLM (we pass speech to our LLM, generate response)
  TTS: SignalWire <Say> (instant)

  Flow: caller speaks → <Gather> STT → Internal LLM generates response
  → <Say> response → <Gather> next turn.
"""

import secrets
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.voice.llm import llm_response
from agentline.billing import calculate_call_cost, debit_account

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

    agent_reply = await llm_response(system_prompt, transcript, model_tier)
    if not agent_reply:
        agent_reply = "I'm sorry, I didn't catch that. Could you repeat?"
    logger.info("Call %s — Hosted Mode LLM response: %s", call_id, agent_reply[:80])
    return agent_reply


# ────────────────────────────────────────────────────────────
# Voice — Outbound Call Answered
# ────────────────────────────────────────────────────────────

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

    # ── Step 2: Get Agent Response (Hosted Mode) ──
    agent_reply = await _generate_hosted_llm_response(agent, transcript, call_id)

    # ── Step 3: Save agent reply to transcript ──
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


# ────────────────────────────────────────────────────────────
# Voice — SMS Callback
# ────────────────────────────────────────────────────────────

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
            if not call:
                return _xml("<Response/>")

            duration_secs = int(duration) if str(duration).isdigit() else 0

            await db.execute(
                """UPDATE calls SET status='completed', duration_seconds=$1, ended_at=now()
                   WHERE id=$2 AND status!='completed'""",
                duration_secs, call_id,
            )

            # ── Billing: charge $0.10/min for the call ──
            if duration_secs > 0 and call.get("account_id"):
                call_cost = calculate_call_cost(duration_secs)
                direction = call.get("direction", "unknown")
                try:
                    await debit_account(
                        db,
                        call["account_id"],
                        call_cost,
                        txn_type="call_charge",
                        reference_id=call_id,
                        description=(
                            f"{direction.capitalize()} call {duration_secs}s "
                            f"({call.get('from_number', '')} → {call.get('to_number', '')})"
                        ),
                    )
                    logger.info(
                        "Call %s — billed $%.4f for %ds (%s)",
                        call_id, call_cost, duration_secs, direction,
                    )
                except ValueError as e:
                    # Insufficient balance — log but don't block call completion
                    logger.warning(
                        "Call %s — billing failed (insufficient balance): %s", call_id, e
                    )

            # ── Push call.completed event with transcript to event mailbox ──
            transcript = _parse_transcript(call.get("transcript"))
            event_id = f"evt_{secrets.token_urlsafe(12)}"
            event_payload = {
                "call_id": call_id,
                "status": call_status,
                "direction": call.get("direction", ""),
                "from_number": call.get("from_number", ""),
                "to_number": call.get("to_number", ""),
                "duration_seconds": duration_secs,
                "transcript": transcript,
            }

            event_type = "call.completed" if call_status == "completed" else f"call.{call_status}"

            try:
                await db.execute(
                    """INSERT INTO event_mailbox
                       (event_id, account_id, agent_id, event_type, payload)
                       VALUES ($1, $2, $3, $4, $5)""",
                    event_id,
                    call.get("account_id"),
                    call.get("agent_id"),
                    event_type,
                    json.dumps(event_payload),
                )
                logger.info(
                    "Call %s — pushed %s event (transcript: %d turns)",
                    call_id, event_type, len(transcript),
                )
            except Exception as e:
                logger.error("Failed to push event for call %s: %s", call_id, e)

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

        # ── Billing: reject inbound calls if account has insufficient balance ──
        balance = await db.fetchval(
            "SELECT balance FROM accounts WHERE id = $1", number["account_id"]
        )
        if balance is not None and float(balance) < 0.10:
            logger.warning(
                "Inbound call rejected — account %s has insufficient balance ($%.2f)",
                number["account_id"], float(balance),
            )
            return _xml(
                "<Response><Say>This number is temporarily unavailable due to "
                "insufficient account balance. Please contact the account owner. "
                "Goodbye.</Say></Response>"
            )

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

    # Use <Gather> for real-time speech recognition (NOT <Record>)
    xml = _gather_xml(call_id, greeting)
    return _xml(xml)
