"""
AgentLine — Cartesia Streaming TTS
Text-to-speech using Cartesia's Sonic model, outputting mulaw 8kHz for telephony.
"""

import logging
import httpx
from agentline.config import settings

logger = logging.getLogger(__name__)


async def tts_cartesia(text: str, voice_id: str) -> bytes:
    """
    Convert text to mulaw 8kHz audio via Cartesia API.
    Returns raw audio bytes ready for Plivo media stream.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": settings.CARTESIA_API_KEY,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json",
            },
            json={
                "model_id": "sonic-english",
                "transcript": text,
                "voice": {"mode": "id", "id": voice_id},
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_mulaw",
                    "sample_rate": 8000,
                },
            },
        )
        response.raise_for_status()
    return response.content
