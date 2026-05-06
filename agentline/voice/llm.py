"""
AgentLine — LLM Wrapper
GPT-4o conversation engine for voice responses.
"""

import logging
import openai

from agentline.config import settings

logger = logging.getLogger(__name__)

# Initialize client
client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# Model tier mapping
MODEL_MAP = {
    "turbo":    "gpt-4o-mini",
    "balanced": "gpt-4o",
    "max":      "gpt-4o",
}


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
    messages.extend(conversation_history)

    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=200,
            messages=messages,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("OpenAI API error: %s", e)
        return "I'm sorry, I'm having trouble processing that. Could you repeat?"
