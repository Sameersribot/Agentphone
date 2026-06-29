"""
AgentLine — Webhook Voice Brain
Lets an agent use its configured webhook as the conversational brain instead of
the hosted LLM. When an agent's voice_mode == "webhook", the voice pipeline
routes each caller utterance to the agent's webhook URL and speaks back the reply.

This mirrors agentline.voice.llm.llm_response_stream — it is an async generator
that yields sentence-sized strings — so the pipeline's speculative-execution and
TTS-streaming logic works unchanged for either brain.

Webhook contract (signed POST, same secret as event webhooks):

  Request JSON:
    {
      "event": "call.conversation",
      "call_id": "call_...",
      "agent_id": "agt_...",
      "account_id": "acc_...",
      "direction": "inbound" | "outbound",
      "user_message": "what the caller just said",
      "conversation_history": [{"role": "user"|"assistant", "content": "..."}],
      "system_prompt": "the agent's configured prompt",
      "voice_id": "female-1"
    }

  Headers:
    X-AgentLine-Signature: <hex HMAC-SHA256 of the raw body>
    X-AgentLine-Event:     call.conversation

  Expected response (HTTP 200, JSON):
    { "response": "what the agent should say next" }
  The fields "text", "reply", "message", or "answer" are also accepted. A plain
  text (non-JSON) body is treated as the reply verbatim.

  For outbound calls that reach voicemail, the webhook may return the literal
  token [VOICEMAIL_DETECTED] (optionally alongside a voicemail message) to make
  the pipeline leave the configured voicemail and hang up — same contract as the
  hosted LLM.
"""

import hashlib
import hmac
import json
import logging

import httpx

from agentline.database import get_db_conn
from agentline.voice.llm import _extract_sentence  # reuse the sentence splitter

logger = logging.getLogger(__name__)

# How long to wait for the webhook to answer. Voice calls need low latency — a
# slow/missing reply falls back to the apology string below.
WEBHOOK_TIMEOUT = 15.0

FALLBACK_REPLY = "I'm sorry, I'm having trouble processing that. Could you repeat?"


async def get_agent_webhook_config(account_id: str, agent_id: str) -> dict | None:
    """Return {"url", "secret"} for the agent's configured webhook, or None."""
    try:
        async with get_db_conn() as db:
            row = await db.fetchrow(
                "SELECT url, secret FROM webhooks WHERE account_id = $1 AND agent_id = $2",
                account_id, agent_id,
            )
        if not row:
            return None
        return {"url": row["url"], "secret": row["secret"]}
    except Exception as e:
        logger.warning("Failed to load webhook config for agent %s: %s", agent_id[:12], e)
        return None


def _parse_reply(resp: httpx.Response) -> str:
    """Extract the agent's reply text from the webhook response."""
    try:
        data = resp.json()
    except Exception:
        return (resp.text or "").strip()
    if not isinstance(data, dict):
        return str(data).strip()
    for key in ("response", "text", "reply", "message", "answer", "say"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Fallback: join any string values found
    return ""


async def webhook_response_stream(
    webhook_url: str,
    webhook_secret: str,
    call_id: str,
    agent_id: str | None,
    account_id: str | None,
    direction: str,
    user_message: str,
    conversation_history: list[dict],
    system_prompt: str | None,
    voice_id: str | None,
):
    """
    Yield the webhook's reply in sentence-sized chunks (mirrors
    llm_response_stream). POSTs the turn to the agent's webhook, parses the
    reply, and yields sentences for streaming TTS. On any failure yields a
    graceful apology so the call never dead-ends.
    """
    payload = {
        "event": "call.conversation",
        "call_id": call_id,
        "agent_id": agent_id,
        "account_id": account_id,
        "direction": direction,
        "user_message": user_message,
        "conversation_history": conversation_history,
        "system_prompt": system_prompt,
        "voice_id": voice_id,
    }
    body = json.dumps(payload, default=str)
    signature = hmac.new(
        webhook_secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
            resp = await client.post(
                webhook_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-AgentLine-Signature": signature,
                    "X-AgentLine-Event": "call.conversation",
                },
            )
    except Exception as e:
        logger.error("Webhook brain request failed for call %s: %s", call_id, e)
        yield FALLBACK_REPLY
        return

    if resp.status_code != 200:
        logger.error(
            "Webhook brain %s returned HTTP %s for call %s",
            webhook_url, resp.status_code, call_id,
        )
        yield FALLBACK_REPLY
        return

    reply = _parse_reply(resp)
    if not reply:
        logger.warning("Webhook brain returned empty reply for call %s", call_id)
        return

    logger.info("Call %s — Webhook reply: %s", call_id, reply[:120])

    # Split into sentence-sized chunks for streaming TTS (same as the LLM path)
    buffer = reply
    while True:
        sentence, buffer = _extract_sentence(buffer)
        if not sentence:
            break
        yield sentence
    if buffer.strip():
        yield buffer.strip()
