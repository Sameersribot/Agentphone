"""
AgentLine — Call & Transcript Pydantic schemas
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Literal


class CallRequest(BaseModel):
    agent_id: str
    to_number: str
    system_prompt: str | None = None
    initial_greeting: str | None = None
    voice: str | None = None
    model_tier: Literal["turbo", "balanced", "max"] = "balanced"
    from_number_id: str | None = None


class CallOut(BaseModel):
    id: str
    account_id: str
    agent_id: str
    number_id: str | None = None
    direction: str
    from_number: str
    to_number: str
    status: str
    duration_seconds: int | None = None
    transcript: list | None = None
    started_at: datetime
    ended_at: datetime | None = None


class TranscriptEntry(BaseModel):
    role: str
    text: str
    timestamp: str
