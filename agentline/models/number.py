"""
AgentLine — Phone Number Pydantic schemas
"""

from pydantic import BaseModel
from datetime import datetime


class NumberProvision(BaseModel):
    agent_id: str
    country: str = "US"  # Default to US (SignalWire)
    number_type: str = "local"  # local, tollfree
    pattern: str | None = None  # Area code filter (e.g. '212' for NYC)


class NumberOut(BaseModel):
    id: str
    account_id: str
    agent_id: str | None = None
    phone_number: str
    country: str
    status: str
    created_at: datetime
    released_at: datetime | None = None
