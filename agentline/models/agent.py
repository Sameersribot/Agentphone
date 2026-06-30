"""
AgentLine — Agent Pydantic schemas
"""

from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime



class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Display name for the AI voice agent")
    system_prompt: str | None = Field(default=None, description="Default instructions for the agent's personality and behavior on ALL calls (inbound and outbound). Can be overridden per-call via POST /v1/calls.")
    initial_greeting: str | None = Field(default=None, description="Default opening line spoken on ALL calls (inbound and outbound), e.g. 'Hello, how can I help you today?'. Can be overridden per-call via POST /v1/calls.")
    voice_id: str | None = Field(default=None, description="TTS voice preset name (e.g. 'female-1', 'male-1') or Cartesia voice UUID; defaults to system voice if not set")
    transfer_number: str | None = Field(default=None, description="Phone number in E.164 format to transfer calls to (e.g. a human operator fallback)")
    voicemail_message: str | None = Field(default=None, description="Message the AI agent leaves if the call goes to voicemail")
    owner_phone: str | None = Field(default=None, description="Owner's phone number in E.164 format (e.g. '+12125551234'). Calls from this number enter task mode — the agent treats speech as executable instructions.")


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="New display name for the AI voice agent")
    system_prompt: str | None = Field(default=None, description="Updated default instructions for ALL future calls (does not affect calls already in progress)")
    initial_greeting: str | None = Field(default=None, description="Updated default greeting for ALL future calls (inbound and outbound)")
    voice_id: str | None = Field(default=None, description="New TTS voice preset name or Cartesia voice UUID")
    transfer_number: str | None = Field(default=None, description="Updated transfer phone number in E.164 format")
    voicemail_message: str | None = Field(default=None, description="Updated voicemail message")
    owner_phone: str | None = Field(default=None, description="Updated owner phone number in E.164 format for task mode")


class AgentOut(BaseModel):
    id: str = Field(description="Unique agent identifier (e.g. 'agt_abc123')")
    account_id: str = Field(description="Account that owns this agent")
    name: str = Field(description="Display name of the AI voice agent")
    system_prompt: str | None = Field(default=None, description="Default system prompt for all calls (can be overridden per-call)")
    initial_greeting: str | None = Field(default=None, description="Default greeting for all calls (can be overridden per-call)")
    voice_id: str | None = Field(default=None, description="TTS voice preset or Cartesia UUID")
    transfer_number: str | None = Field(default=None, description="Phone number for call transfers")
    voicemail_message: str | None = Field(default=None, description="Message left on voicemail")
    owner_phone: str | None = Field(default=None, description="Owner's phone number for task mode")
    created_at: datetime = Field(description="When the agent was created")
