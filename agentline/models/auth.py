"""
AgentLine — Auth Pydantic schemas
Request models for self-service signup/login (email OTP) and API key management.
"""

from pydantic import BaseModel, EmailStr, Field


class OtpRequest(BaseModel):
    email: EmailStr = Field(
        description="Email address to send the one-time code to. "
                    "The same email identifies the account for both agents and humans."
    )


class VerifyRequest(BaseModel):
    email: EmailStr = Field(description="Email address the one-time code was sent to")
    otp: str = Field(description="One-time code received via email")
