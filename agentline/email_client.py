"""
AgentLine — Supabase Auth Client
Handles OTP email sending and verification via Supabase Auth.
Supabase sends the OTP email automatically — no separate email service needed.
"""

import logging
from supabase import create_client, Client

from agentline.config import settings

logger = logging.getLogger(__name__)

# Module-level client (initialized lazily)
_supabase: Client | None = None
_supabase_admin: Client | None = None


def get_supabase() -> Client:
    """Get the Supabase client (anon key — for auth flows)."""
    global _supabase
    if _supabase is None:
        _supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    return _supabase


def get_supabase_admin() -> Client:
    """Get the Supabase admin client (service role key — for server-side operations)."""
    global _supabase_admin
    if _supabase_admin is None:
        _supabase_admin = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    return _supabase_admin


async def send_otp(email: str) -> dict:
    """
    Send a magic link / OTP to the given email via Supabase Auth.
    Supabase handles email delivery, rate limiting, and expiry.
    """
    client = get_supabase()
    try:
        response = client.auth.sign_in_with_otp({
            "email": email,
            "options": {
                "should_create_user": True,
            },
        })
        logger.info("OTP sent to %s via Supabase", email)
        return {"success": True, "email": email}
    except Exception as e:
        logger.error("Supabase OTP send failed for %s: %s", email, e)
        raise


async def verify_otp(email: str, otp_code: str) -> dict:
    """
    Verify the OTP code via Supabase Auth.
    Returns the Supabase user + session on success.
    """
    client = get_supabase()
    try:
        response = client.auth.verify_otp({
            "email": email,
            "token": otp_code,
            "type": "email",
        })
        return {
            "user": response.user,
            "session": response.session,
        }
    except Exception as e:
        logger.error("Supabase OTP verify failed for %s: %s", email, e)
        raise
