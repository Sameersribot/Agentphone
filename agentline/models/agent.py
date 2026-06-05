"""
AgentLine — Agent Pydantic schemas
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Literal


class AgentCreate(BaseModel):
    name: str
    system_prompt: str | None = None
    initial_greeting: str | None = None
    voice_id: str | None = None  # Cartesia UUID or preset name (e.g. "female-1"); None = system default
    model_tier: Literal["turbo", "balanced", "max"] = "balanced"
    transfer_number: str | None = None
    voicemail_message: str | None = None
    knowledge_base: str | None = None  # Dynamic context the agent injects — appended to system_prompt at call time


class AgentUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    initial_greeting: str | None = None
    voice_id: str | None = None
    model_tier: Literal["turbo", "balanced", "max"] | None = None
    transfer_number: str | None = None
    voicemail_message: str | None = None
    knowledge_base: str | None = None


class AgentOut(BaseModel):
    id: str
    account_id: str
    name: str
    system_prompt: str | None = None
    initial_greeting: str | None = None
    voice_id: str | None = None
    model_tier: str
    transfer_number: str | None = None
    voicemail_message: str | None = None
    knowledge_base: str | None = None
    created_at: datetime
