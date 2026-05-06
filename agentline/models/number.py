"""
AgentLine — Phone Number Pydantic schemas
"""

from pydantic import BaseModel
from datetime import datetime


class NumberProvision(BaseModel):
    agent_id: str
    country: str = "US"
    area_code: str | None = None


class NumberOut(BaseModel):
    id: str
    account_id: str
    agent_id: str | None = None
    phone_number: str
    country: str
    status: str
    created_at: datetime
    released_at: datetime | None = None
