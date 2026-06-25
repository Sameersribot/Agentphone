"""
AgentLine — Feedback Pydantic schemas
"""

from enum import Enum
from datetime import datetime

from pydantic import BaseModel, Field


class FeedbackCategory(str, Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    DIFFICULTY = "difficulty"
    FEEDBACK = "feedback"


class FeedbackSeverity(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class FeedbackCreate(BaseModel):
    category: FeedbackCategory = Field(
        description="Type of feedback: 'bug' (something broken / not working), "
                    "'feature_request' (want a new capability), "
                    "'difficulty' (hard or confusing to use), "
                    "or 'feedback' (general comment or praise)"
    )
    message: str = Field(
        description="Detailed description of the feedback, issue, or request. "
                    "For bugs, include what you expected vs. what actually happened."
    )
    subject: str | None = Field(
        default=None,
        description="Short summary title for the feedback (optional)"
    )
    severity: FeedbackSeverity = Field(
        default=FeedbackSeverity.NORMAL,
        description="Impact level, mainly relevant for bugs: 'low', 'normal', 'high', or 'critical'"
    )
    agent_id: str | None = Field(
        default=None,
        description="ID of the AI agent this feedback relates to, if applicable"
    )
    contact_email: str | None = Field(
        default=None,
        description="Email address for follow-up about this feedback (optional)"
    )


class FeedbackOut(BaseModel):
    id: str = Field(description="Unique feedback identifier (e.g. 'fb_abc123')")
    account_id: str = Field(description="Account that submitted this feedback")
    category: str = Field(description="Feedback type: 'bug', 'feature_request', 'difficulty', or 'feedback'")
    message: str = Field(description="Full feedback message")
    subject: str | None = Field(default=None, description="Short summary title")
    severity: str = Field(description="Impact level: 'low', 'normal', 'high', or 'critical'")
    agent_id: str | None = Field(default=None, description="Related AI agent ID, if any")
    contact_email: str | None = Field(default=None, description="Follow-up email, if provided")
    status: str = Field(description="Current status: 'open', 'acknowledged', 'in_progress', 'resolved', or 'closed'")
    created_at: datetime = Field(description="When the feedback was submitted")
