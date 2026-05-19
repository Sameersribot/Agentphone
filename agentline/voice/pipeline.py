"""
AgentLine — Voice Pipeline (Provider-Agnostic)
Orchestrates the full voice loop: Provider audio → Deepgram STT → LLM → Cartesia TTS → Provider audio.

Supports both SignalWire <Connect><Stream> and Plivo bidirectional WebSocket.

Architecture:
  Provider WS (raw mulaw audio in)
      ↓
  Deepgram (streaming STT)
      ↓ [on utterance end]
  LLM (generate response)
      ↓
  Cartesia (TTS → raw mulaw)
      ↓
  Provider WS (audio back to caller)

Cost savings vs SignalWire <Gather>+<Say>:
  SignalWire STT: $0.0675/min  → Deepgram: $0.006/min  (~90% cheaper)
  SignalWire TTS: $0.003/min   → Cartesia: ~$0.002/min  (comparable)
"""

import asyncio
import json
import base64
import logging
from datetime import datetime, timezone

from deepgram import LiveTranscriptionEvents

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.voice.stt import create_deepgram_connection, get_stt_options
from agentline.voice.llm import llm_response
from agentline.voice.tts import tts_cartesia

logger = logging.getLogger(__name__)

# Default Cartesia voice ID — Sonic English female (natural sounding)
DEFAULT_VOICE_ID = "a0e99841-438c-4a64-b679-ae501e7d6091"

# ── Provider-specific audio send helpers ──────────────────────────

async def _send_audio_signalwire(ws, audio_bytes: bytes, stream_sid: str):
    """Send audio back to caller via SignalWire <Connect><Stream> WebSocket."""
    payload = base64.b64encode(audio_bytes).decode("ascii")
    # SignalWire bidirectional stream expects 'media' event with streamSid
    await ws.send_json({
        "event": "media",
        "streamSid": stream_sid,
        "media": {
            "payload": payload,
        },
    })


async def _send_audio_plivo(ws, audio_bytes: bytes, _stream_sid: str = ""):
    """Send audio back to caller via Plivo bidirectional WebSocket."""
    payload = base64.b64encode(audio_bytes).decode("ascii")
    await ws.send_json({
        "event": "playAudio",
        "media": {"payload": payload, "contentType": "audio/x-mulaw;rate=8000"},
    })


# Provider send function registry
PROVIDER_SEND = {
    "signalwire": _send_audio_signalwire,
    "plivo": _send_audio_plivo,
}


async def run_pipeline(
    provider_ws,
    call_id: str,
    system_prompt: str,
    initial_greeting: str | None,
    voice_id: str,
    model_tier: str,
    provider: str = "signalwire",
):
    """
    Main voice pipeline coroutine. One instance per active call.
    Bridges Provider audio ↔ Deepgram STT ↔ LLM ↔ Cartesia TTS.

    Args:
        provider_ws: WebSocket connection to the telephony provider
        call_id: Internal call ID
        system_prompt: System prompt for the LLM
        initial_greeting: Optional greeting to speak when call starts
        voice_id: Cartesia voice ID
        model_tier: LLM model tier (turbo/balanced/max)
        provider: 'signalwire' or 'plivo'
    """
    voice_id = voice_id or DEFAULT_VOICE_ID
    send_audio = PROVIDER_SEND.get(provider, _send_audio_signalwire)

    conversation_history: list[dict] = []
    transcript_turns: list[dict] = []
    stream_sid = ""  # Set when we receive the 'start'/'connected' event

    # 1. Wait for stream start event before sending greeting
    # We'll handle this in the message loop below

    # 2. Set up Deepgram streaming STT
    dg_connection = create_deepgram_connection()
    utterance_buffer: list[str] = []

    # Flag to track if greeting was sent
    greeting_sent = asyncio.Event()
    if not initial_greeting:
        greeting_sent.set()

    # This event fires for each transcript segment
    async def on_transcript(self, result, **kwargs):
        sentence = result.channel.alternatives[0].transcript
        if not sentence:
            return

        utterance_buffer.append(sentence)

        if result.is_final and result.speech_final:
            full_utterance = " ".join(utterance_buffer).strip()
            utterance_buffer.clear()

            if not full_utterance:
                return

            logger.info("Call %s — Human: %s", call_id, full_utterance)

            transcript_turns.append({
                "role": "human",
                "text": full_utterance,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Save transcript incrementally
            async with get_db_conn() as db:
                await db.execute(
                    "UPDATE calls SET transcript=$1 WHERE id=$2",
                    json.dumps(transcript_turns), call_id,
                )

            # 3. Get LLM response
            conversation_history.append({"role": "user", "content": full_utterance})
            reply = await llm_response(system_prompt, conversation_history, model_tier)
            conversation_history.append({"role": "assistant", "content": reply})

            logger.info("Call %s — Agent: %s", call_id, reply)

            transcript_turns.append({
                "role": "agent",
                "text": reply,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Save transcript with agent reply
            async with get_db_conn() as db:
                await db.execute(
                    "UPDATE calls SET transcript=$1 WHERE id=$2",
                    json.dumps(transcript_turns), call_id,
                )

            # 4. TTS and send audio back
            try:
                audio = await tts_cartesia(reply, voice_id)
                await send_audio(provider_ws, audio, stream_sid)
            except Exception as e:
                logger.error("TTS/send failed for call %s: %s", call_id, e)

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
    options = get_stt_options()
    await dg_connection.start(options)

    # 5. Forward audio from Provider → Deepgram
    try:
        async for message in provider_ws.iter_text():
            data = json.loads(message)
            event = data.get("event", "")

            if event == "media":
                # Both SignalWire and Plivo use {"event":"media","media":{"payload":"..."}}
                audio_payload = data.get("media", {}).get("payload", "")
                if audio_payload:
                    audio_bytes = base64.b64decode(audio_payload)
                    await dg_connection.send(audio_bytes)

            elif event in ("start", "connected"):
                # SignalWire sends 'connected' then 'start' with metadata
                if "start" in data or "streamSid" in data:
                    stream_sid = data.get("start", {}).get("streamSid", "") or data.get("streamSid", "")
                logger.info(
                    "%s stream started for call %s (streamSid: %s)",
                    provider.capitalize(), call_id, stream_sid,
                )

                # Send initial greeting now that stream is ready
                if initial_greeting and not greeting_sent.is_set():
                    try:
                        audio = await tts_cartesia(initial_greeting, voice_id)
                        await send_audio(provider_ws, audio, stream_sid)
                        transcript_turns.append({
                            "role": "agent",
                            "text": initial_greeting,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        logger.info("Sent initial greeting for call %s", call_id)
                        greeting_sent.set()
                    except Exception as e:
                        logger.error("Failed to send greeting for call %s: %s", call_id, e)
                        greeting_sent.set()

            elif event == "stop":
                logger.info("%s sent stop event for call %s", provider.capitalize(), call_id)
                break

    except Exception as e:
        logger.info("WebSocket closed for call %s: %s", call_id, e)
    finally:
        await dg_connection.finish()

        # Save final transcript to DB
        async with get_db_conn() as db:
            await db.execute(
                """UPDATE calls
                   SET transcript=$1, ended_at=now()
                   WHERE id=$2""",
                json.dumps(transcript_turns),
                call_id,
            )
        logger.info("Pipeline finished for call %s — %d turns", call_id, len(transcript_turns))
