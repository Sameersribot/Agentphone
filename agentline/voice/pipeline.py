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
from agentline.voice.voices import resolve_voice_id, DEFAULT_VOICE_ID

logger = logging.getLogger(__name__)


# ── Provider-specific audio send helpers ──────────────────────────

async def _send_audio_signalwire(ws, audio_bytes: bytes, stream_sid: str):
    """Send audio back to caller via SignalWire <Connect><Stream> WebSocket."""
    if not audio_bytes:
        return
    payload = base64.b64encode(audio_bytes).decode("ascii")
    msg = {
        "event": "media",
        "streamSid": stream_sid,
        "media": {
            "payload": payload,
        },
    }
    await ws.send_json(msg)
    logger.debug("Sent %d bytes audio to SignalWire (streamSid: %s)", len(audio_bytes), stream_sid[:8])


async def _send_audio_plivo(ws, audio_bytes: bytes, _stream_sid: str = ""):
    """Send audio back to caller via Plivo bidirectional WebSocket."""
    if not audio_bytes:
        return
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
        voice_id: Cartesia voice ID (UUID or preset name — resolved before use)
        model_tier: LLM model tier (turbo/balanced/max)
        provider: 'signalwire' or 'plivo'
    """
    voice_id = resolve_voice_id(voice_id)
    send_audio = PROVIDER_SEND.get(provider, _send_audio_signalwire)

    conversation_history: list[dict] = []
    transcript_turns: list[dict] = []
    stream_sid = ""  # Set when we receive the 'start' event with metadata
    greeting_sent = False

    # Set up Deepgram streaming STT
    dg_connection = create_deepgram_connection()
    utterance_buffer: list[str] = []

    # This event fires for each transcript segment
    async def on_transcript(self, result, **kwargs):
        # We only care about finalized transcript segments
        if not result.is_final:
            return

        sentence = result.channel.alternatives[0].transcript
        if sentence:
            utterance_buffer.append(sentence)

        if result.speech_final:
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
            try:
                async with get_db_conn() as db:
                    await db.execute(
                        "UPDATE calls SET transcript=$1 WHERE id=$2",
                        json.dumps(transcript_turns), call_id,
                    )
            except Exception as e:
                logger.warning("Failed to save transcript for call %s: %s", call_id, e)

            # Get LLM response
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
            try:
                async with get_db_conn() as db:
                    await db.execute(
                        "UPDATE calls SET transcript=$1 WHERE id=$2",
                        json.dumps(transcript_turns), call_id,
                    )
            except Exception as e:
                logger.warning("Failed to save transcript for call %s: %s", call_id, e)

            # TTS and send audio back
            try:
                audio = await tts_cartesia(reply, voice_id)
                await send_audio(provider_ws, audio, stream_sid)
            except Exception as e:
                logger.error("TTS/send failed for call %s: %s", call_id, e)

    # ── Deepgram lifecycle event handlers ──
    dg_ready = asyncio.Event()

    async def on_dg_open(self, open_response, **kwargs):
        logger.info("Call %s — Deepgram WebSocket OPEN (connection ready)", call_id)
        dg_ready.set()

    async def on_dg_error(self, error, **kwargs):
        logger.error("Call %s — Deepgram ERROR: %s", call_id, error)

    async def on_dg_close(self, *args, **kwargs):
        logger.info("Call %s — Deepgram WebSocket CLOSED", call_id)

    async def on_utterance_end(self, utterance_end, **kwargs):
        logger.info("Call %s — Deepgram UtteranceEnd event (buffer: %s)", call_id, utterance_buffer)
        # Fallback: if we have buffered text but speech_final never fired, flush now
        if utterance_buffer:
            full_utterance = " ".join(utterance_buffer).strip()
            utterance_buffer.clear()
            if full_utterance:
                logger.info("Call %s — Human (via UtteranceEnd): %s", call_id, full_utterance)

                transcript_turns.append({
                    "role": "human",
                    "text": full_utterance,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                try:
                    async with get_db_conn() as db:
                        await db.execute(
                            "UPDATE calls SET transcript=$1 WHERE id=$2",
                            json.dumps(transcript_turns), call_id,
                        )
                except Exception as e:
                    logger.warning("Failed to save transcript for call %s: %s", call_id, e)

                conversation_history.append({"role": "user", "content": full_utterance})
                reply = await llm_response(system_prompt, conversation_history, model_tier)
                conversation_history.append({"role": "assistant", "content": reply})

                logger.info("Call %s — Agent: %s", call_id, reply)

                transcript_turns.append({
                    "role": "agent",
                    "text": reply,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                try:
                    async with get_db_conn() as db:
                        await db.execute(
                            "UPDATE calls SET transcript=$1 WHERE id=$2",
                            json.dumps(transcript_turns), call_id,
                        )
                except Exception as e:
                    logger.warning("Failed to save transcript for call %s: %s", call_id, e)

                try:
                    audio = await tts_cartesia(reply, voice_id)
                    await send_audio(provider_ws, audio, stream_sid)
                except Exception as e:
                    logger.error("TTS/send failed for call %s: %s", call_id, e)

    dg_connection.on(LiveTranscriptionEvents.Open, on_dg_open)
    dg_connection.on(LiveTranscriptionEvents.Error, on_dg_error)
    dg_connection.on(LiveTranscriptionEvents.Close, on_dg_close)
    dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
    dg_connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
    options = get_stt_options()
    result = await dg_connection.start(options)
    logger.info("Deepgram STT start() returned for call %s: %s", call_id, result)

    media_frame_count = 0

    # Forward audio from Provider → Deepgram and handle stream lifecycle
    try:
        async for message in provider_ws.iter_text():
            data = json.loads(message)
            event = data.get("event", "")

            if event == "media":
                # Both SignalWire and Plivo send audio in {"event":"media","media":{"payload":"..."}}
                audio_payload = data.get("media", {}).get("payload", "")
                if audio_payload:
                    audio_bytes = base64.b64decode(audio_payload)
                    try:
                        await dg_connection.send(audio_bytes)
                        media_frame_count += 1
                        if media_frame_count in (1, 10, 50, 100):
                            logger.info(
                                "Call %s — forwarded %d media frames to Deepgram (%d bytes this frame)",
                                call_id, media_frame_count, len(audio_bytes),
                            )
                    except Exception as e:
                        logger.error("Call %s — failed to send audio to Deepgram: %s", call_id, e)

            elif event == "connected":
                # SignalWire sends 'connected' first — just the WebSocket handshake
                # Do NOT send greeting yet — we need streamSid from 'start' event
                logger.info("WebSocket connected for call %s (waiting for stream start...)", call_id)

            elif event == "start":
                # SignalWire sends 'start' with stream metadata including streamSid
                # This is when the audio stream is actually ready
                start_data = data.get("start", {})
                stream_sid = start_data.get("streamSid", "") or data.get("streamSid", "")
                logger.info(
                    "Stream started for call %s (streamSid: %s, tracks: %s)",
                    call_id, stream_sid,
                    start_data.get("tracks", "unknown"),
                )

                # NOW send the initial greeting — stream is ready
                if initial_greeting and not greeting_sent:
                    try:
                        logger.info("Sending greeting for call %s with voice %s", call_id, voice_id)
                        audio = await tts_cartesia(initial_greeting, voice_id)
                        await send_audio(provider_ws, audio, stream_sid)
                        transcript_turns.append({
                            "role": "agent",
                            "text": initial_greeting,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        greeting_sent = True
                        logger.info("Greeting sent for call %s (%d bytes audio)", call_id, len(audio))
                    except Exception as e:
                        logger.error("Failed to send greeting for call %s: %s", call_id, e)
                        # Don't mark as sent — but don't retry either to avoid loops
                        greeting_sent = True

            elif event == "stop":
                logger.info("%s sent stop event for call %s (received %d media frames total)", provider.capitalize(), call_id, media_frame_count)
                break

            else:
                logger.debug("Call %s — unknown event: %s", call_id, event)

    except Exception as e:
        logger.info("WebSocket closed for call %s: %s (received %d media frames)", call_id, e, media_frame_count)
    finally:
        try:
            await dg_connection.finish()
        except Exception as e:
            logger.debug("Deepgram finish error (expected on disconnect): %s", e)

        # Save final transcript to DB
        try:
            async with get_db_conn() as db:
                await db.execute(
                    """UPDATE calls
                       SET transcript=$1, ended_at=now()
                       WHERE id=$2""",
                    json.dumps(transcript_turns),
                    call_id,
                )
        except Exception as e:
            logger.warning("Failed to save final transcript for call %s: %s", call_id, e)

        logger.info("Pipeline finished for call %s — %d turns", call_id, len(transcript_turns))
