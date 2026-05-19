"""
AgentLine — LLM Wrapper
Conversation engine for voice responses using OpenAI-compatible API.
"""

import logging
import openai

from agentline.config import settings

logger = logging.getLogger(__name__)

# Initialize client — OpenAI-compatible API
client = openai.AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,
)

# Model tier mapping — all tiers use gpt-4o-mini (fast, cheap, reliable)
MODEL_MAP = {
    "turbo":    "gpt-4o-mini",
    "balanced": "gpt-4o-mini",
    "max":      "gpt-4o",
}


def _normalize_turn(turn: dict) -> dict:
    """
    Convert any transcript format to OpenAI chat format.

    Handles both:
      - Pipeline format:    {"role": "user",  "content": "hello"}
      - Transcript format:  {"role": "human", "text": "hello"}
    """
    raw_role = turn.get("role", "user")
    # Normalize role: human/user → "user", agent/assistant → "assistant"
    if raw_role in ("human", "user"):
        role = "user"
    else:
        role = "assistant"

    # Get content from either "content" or "text" key
    content = turn.get("content") or turn.get("text") or ""

    return {"role": role, "content": content}


async def llm_response(
    system_prompt: str,
    conversation_history: list[dict],
    model_tier: str = "balanced",
) -> str:
    """
    Generate a conversational response for voice output.
    Keeps responses short (max 200 tokens) for natural voice flow.
    """
    model = MODEL_MAP.get(model_tier, MODEL_MAP["balanced"])
    messages = [
        {"role": "system", "content": system_prompt or "You are a helpful voice assistant. Keep responses brief and conversational."},
    ]

    # Map any transcript format to OpenAI chat format
    for turn in conversation_history:
        messages.append(_normalize_turn(turn))

    logger.debug("LLM request: model=%s, %d messages", model, len(messages))

    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=200,
            messages=messages,
        )
        reply = response.choices[0].message.content
        logger.debug("LLM reply: %s", reply[:100] if reply else "<empty>")
        return reply
    except Exception as e:
        logger.error("OpenAI API error: %s", e)
        return "I'm sorry, I'm having trouble processing that. Could you repeat?"
