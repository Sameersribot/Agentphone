"""
AgentLine — Message & Conversation Pydantic schemas
"""

from pydantic import BaseModel
from datetime import datetime


class MessageSend(BaseModel):
    agent_id: str
    to_number: str
    body: str
    media_url: str | None = None
    from_number_id: str | None = None


class MessageOut(BaseModel):
    id: str
    account_id: str
    agent_id: str | None = None
    number_id: str | None = None
    conversation_id: str | None = None
    direction: str
    from_number: str
    to_number: str
    body: str | None = None
    media_url: str | None = None
    status: str
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    account_id: str
    agent_id: str | None = None
    number_id: str | None = None
    contact_number: str
    created_at: datetime
    last_message_at: datetime | None = None
