"""
AgentLine — Auth Router
Handles agent signup (OTP via Supabase Auth) and verification.
POST /v0/agent/signup  → Supabase sends OTP email
POST /v0/agent/verify  → Verify OTP via Supabase, create account + agent + number + API key
"""

import secrets
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
import bcrypt

from agentline.database import get_db
from agentline.email_client import send_otp, verify_otp
from agentline.signalwire_client import provision_number
from agentline.billing import (
    NUMBER_PROVISION_COST,
    debit_account,
)

router = APIRouter(prefix="/v0/agent", tags=["Auth"])


class SignupRequest(BaseModel):
    human_email: EmailStr
    agent_name: str | None = None


class VerifyRequest(BaseModel):
    human_email: EmailStr
    otp_code: str
    agent_name: str | None = None


@router.post("/signup")
async def signup(body: SignupRequest, db=Depends(get_db)):
    """
    Send a 6-digit OTP to the human's email via Supabase Auth.
    The AI agent should call this first, then ask the human for the code.
    """
    # Check if account already exists in our system
    existing = await db.fetchrow(
        "SELECT id FROM accounts WHERE human_email = $1", body.human_email
    )
    if existing:
        raise HTTPException(409, "Account already exists for this email.")

    # Send OTP via Supabase Auth (Supabase handles email delivery)
    try:
        await send_otp(body.human_email)
    except Exception as e:
        error_msg = str(e)
        if "rate limit" in error_msg.lower() or "429" in error_msg:
            raise HTTPException(429, "Email rate limit exceeded. Wait a few minutes and try again.")
        raise HTTPException(502, f"Failed to send verification email: {error_msg}")

    return {
        "human_email": body.human_email,
        "message": (
            "Verification code sent via email. "
            "Ask your human for the 6-digit code, "
            "then call POST /v0/agent/verify."
        ),
    }


@router.post("/verify")
async def verify(body: VerifyRequest, db=Depends(get_db)):
    """
    Verify the OTP code via Supabase Auth. On success, provisions:
    - Account (linked to Supabase user)
    - Starter agent
    - US phone number (via Plivo)
    - API key (returned once, never shown again)
    """
    # Check if account already exists
    existing = await db.fetchrow(
        "SELECT id FROM accounts WHERE human_email = $1", body.human_email
    )
    if existing:
        raise HTTPException(409, "Account already exists for this email.")

    # Verify OTP via Supabase Auth
    try:
        auth_result = await verify_otp(body.human_email, body.otp_code)
    except Exception:
        raise HTTPException(400, "Invalid or expired verification code.")

    supabase_user = auth_result.get("user")
    supabase_user_id = supabase_user.get("id") if supabase_user else None

    # --- Create account ---
    account_id = f"acct_{secrets.token_urlsafe(12)}"
    await db.execute(
        "INSERT INTO accounts (id, human_email, supabase_user_id) VALUES ($1, $2, $3)",
        account_id,
        body.human_email,
        supabase_user_id,
    )

    # --- Create starter agent ---
    agent_id = f"agt_{secrets.token_urlsafe(12)}"
    agent_name = body.agent_name or "My Agent"
    await db.execute(
        """INSERT INTO agents (id, account_id, name, voice_mode)
           VALUES ($1, $2, $3, 'hosted')""",
        agent_id,
        account_id,
        agent_name,
    )

    # --- Provision US phone number via SignalWire ---
    try:
        number_data = await provision_number(country="US", number_type="local", agent_id=agent_id)
    except Exception as e:
        logging.getLogger(__name__).warning("Auto-provision failed during signup: %s", e)
        number_data = None

    number_id = None
    phone_number = None
    if number_data:
        number_id = f"num_{secrets.token_urlsafe(12)}"
        phone_number = number_data["phone_number"]
        try:
            await db.execute(
                """INSERT INTO phone_numbers
                   (id, account_id, agent_id, provider_id, phone_number, country, status)
                   VALUES ($1, $2, $3, $4, $5, 'US', 'active')""",
                number_id,
                account_id,
                agent_id,
                number_data["provider_id"],
                phone_number,
            )
            logging.getLogger(__name__).info(
                "Signup: saved number %s (%s) for agent %s",
                number_id, phone_number, agent_id,
            )
        except Exception as e:
            logging.getLogger(__name__).error(
                "Signup: DB INSERT failed for number %s: %s",
                phone_number, e,
            )
            number_id = None
            phone_number = None

    # --- Debit $2.00 for the auto-provisioned number ---
    if number_id:
        try:
            await debit_account(
                db,
                account_id,
                NUMBER_PROVISION_COST,
                txn_type="number_provision",
                reference_id=number_id,
                description=f"Signup: provisioned number {phone_number}",
            )
        except ValueError as e:
            logging.getLogger(__name__).warning(
                "Signup: billing debit failed for number %s: %s", phone_number, e
            )

    # --- Generate API key ---
    raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
    salt = bcrypt.gensalt()
    key_hash = bcrypt.hashpw(raw_key.encode('utf-8'), salt).decode('utf-8')
    key_id = f"key_{secrets.token_urlsafe(12)}"
    await db.execute(
        """INSERT INTO api_keys (id, account_id, key_hash, key_prefix)
           VALUES ($1, $2, $3, $4)""",
        key_id,
        account_id,
        key_hash,
        raw_key[:12],
    )

    return {
        "account_id": account_id,
        "agent_id": agent_id,
        "number_id": number_id,
        "phone_number": phone_number,
        "api_key": raw_key,  # Only time this is shown
        "supabase_user_id": supabase_user_id,
        "message": "Account created. Save your API key — it won't be shown again.",
    }
