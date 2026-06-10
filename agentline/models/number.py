"""
AgentLine — Phone Number Pydantic schemas
"""

from pydantic import BaseModel
from datetime import datetime


class NumberProvision(BaseModel):
    agent_id: str
    country: str = "US"  # Default to US (SignalWire)
    number_type: str = "local"  # local, tollfree
    area_code: str | None = None  # Preferred 3-digit US area code (e.g. '212' for NYC, '415' for SF)
    pattern: str | None = None  # Legacy: loose digit match (prefer area_code instead)


class NumberOut(BaseModel):
    id: str
    account_id: str
    agent_id: str | None = None
    phone_number: str
    country: str
    status: str
    created_at: datetime
    released_at: datetime | None = None
