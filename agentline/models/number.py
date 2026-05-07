"""
AgentLine — Phone Number Pydantic schemas
"""

from pydantic import BaseModel
from datetime import datetime


class NumberProvision(BaseModel):
    agent_id: str
    country: str = "IN"  # Default to India since Plivo India account
    number_type: str = "local"  # local, mobile, tollfree, fixed, national
    pattern: str | None = None  # Area code filter (e.g. '22' for Mumbai)


class NumberOut(BaseModel):
    id: str
    account_id: str
    agent_id: str | None = None
    phone_number: str
    country: str
    status: str
    created_at: datetime
    released_at: datetime | None = None
