"""
AgentLine — Voice Pipeline
Orchestrates the full voice loop: Telnyx audio → Deepgram STT → GPT-4o → Cartesia TTS → Telnyx audio.

Architecture:
  Telnyx WS (raw mulaw audio in)
      ↓
  Deepgram (streaming STT)
      ↓ [on utterance end]
  GPT-4o (generate response)
      ↓
  Cartesia (streaming TTS → raw mulaw)
      ↓
  Telnyx WS (audio back to caller)
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


async def send_audio(ws, audio_bytes: bytes):
    """Send audio back to Telnyx over the media WebSocket."""
    payload = base64.b64encode(audio_bytes).decode("ascii")
    await ws.send_json({
        "event": "media",
        "media": {"payload": payload},
    })


async def run_pipeline(
    telnyx_ws,
    call_id: str,
    system_prompt: str,
    initial_greeting: str | None,
    voice_id: str,
    model_tier: str,
):
    """
    Main voice pipeline coroutine. One instance per active call.
    Bridges Telnyx audio ↔ Deepgram STT ↔ LLM ↔ Cartesia TTS.
    """
    conversation_history: list[dict] = []
    transcript_turns: list[dict] = []

    # 1. Send initial greeting if configured
    if initial_greeting:
        try:
            audio = await tts_cartesia(initial_greeting, voice_id)
            await send_audio(telnyx_ws, audio)
            transcript_turns.append({
                "role": "agent",
                "text": initial_greeting,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("Sent initial greeting for call %s", call_id)
        except Exception as e:
            logger.error("Failed to send greeting for call %s: %s", call_id, e)

    # 2. Set up Deepgram streaming STT
    dg_connection = create_deepgram_connection()
    utterance_buffer: list[str] = []

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

            # 4. TTS and send audio back
            try:
                audio = await tts_cartesia(reply, voice_id)
                await send_audio(telnyx_ws, audio)
            except Exception as e:
                logger.error("TTS/send failed for call %s: %s", call_id, e)

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
    options = get_stt_options()
    await dg_connection.start(options)

    # 5. Forward audio from Telnyx → Deepgram
    try:
        async for message in telnyx_ws.iter_text():
            data = json.loads(message)
            if data.get("event") == "media":
                audio_bytes = base64.b64decode(data["media"]["payload"])
                await dg_connection.send(audio_bytes)
            elif data.get("event") == "stop":
                logger.info("Telnyx sent stop event for call %s", call_id)
                break
    except Exception as e:
        logger.info("WebSocket closed for call %s: %s", call_id, e)
    finally:
        await dg_connection.finish()

        # Save transcript to DB
        async with get_db_conn() as db:
            await db.execute(
                """UPDATE calls
                   SET status='completed', transcript=$1, ended_at=now()
                   WHERE id=$2""",
                json.dumps(transcript_turns),
                call_id,
            )
        logger.info("Pipeline finished for call %s — %d turns", call_id, len(transcript_turns))
