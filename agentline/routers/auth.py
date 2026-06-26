"""
AgentLine — Auth Router
Self-service signup & login via email OTP, plus API key management.

A single OTP flow serves both AI agents and humans:
  1. POST /v1/auth/otp     — request a one-time code to an email
  2. POST /v1/auth/verify  — verify the code; the account is auto-created on
                             first verification (with a $2.50 sign-up bonus) and
                             a fresh API key is minted and returned.

Because the same email always resolves to the same account (accounts.human_email
is UNIQUE), an agent that signs up and a human that later signs in with that
email land on the identical account. API keys are bcrypt-hashed and never stored
in plaintext, so each verification mints a brand-new key.

Authenticated endpoints let an account mint additional keys, list them, and
revoke them.
"""

import logging
import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request

from agentline.auth_middleware import get_current_account
from agentline.billing import credit_account
from agentline.database import get_db
from agentline.email_client import send_otp, verify_otp
from agentline.models.auth import OtpRequest, VerifyRequest
from agentline.redis_client import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["Auth"])

SIGNUP_BONUS = 2.50  # USD credited once, on first verification

# Rate-limit windows (seconds)
RL_WINDOW = 600
# Limits within the window
RL_OTP_PER_EMAIL = 3
RL_OTP_PER_IP = 5
RL_VERIFY_PER_EMAIL = 5


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

async def _rate_limit(key: str, limit: int) -> None:
    """
    Increment a Redis counter for `key`; raise 429 if it exceeds `limit`.
    Fails open (allows) when Redis is unavailable — the app runs without Redis.
    """
    redis = get_redis()
    if redis is None:
        return
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, RL_WINDOW)
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a few minutes and try again.",
            )
    except HTTPException:
        raise
    except Exception as e:  # Redis hiccup — don't block auth
        logger.warning("Rate-limit check failed (allowing): %s", e)


def _hash_key(raw_key: str) -> str:
    """Bcrypt-hash a raw API key, returning a UTF-8 string compatible with auth_middleware."""
    return bcrypt.hashpw(raw_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def _mint_api_key(db, account_id: str) -> dict:
    """
    Generate, hash, and store a new API key for an account.
    Returns {api_key (plaintext, shown once), key_id, key_prefix}.
    """
    raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
    key_id = f"key_{secrets.token_urlsafe(12)}"
    key_prefix = raw_key[:12]
    await db.execute(
        "INSERT INTO api_keys (id, account_id, key_hash, key_prefix) VALUES ($1, $2, $3, $4)",
        key_id, account_id, _hash_key(raw_key), key_prefix,
    )
    logger.info("Minted API key %s (prefix %s) for account %s", key_id, key_prefix, account_id)
    return {"api_key": raw_key, "key_id": key_id, "key_prefix": key_prefix}


# ────────────────────────────────────────────────────────────
# 1. Request OTP (signup / login — same entry point)
# ────────────────────────────────────────────────────────────

@router.post("/otp", operation_id="auth_send_otp")
async def request_otp(body: OtpRequest, request: Request):
    """
    Send a one-time code to an email address.

    This is the single entry point for both sign-up and sign-in: an AI agent
    calling it for the first time will have an account created on verification,
    while a human (or returning agent) using the same email simply signs in.
    """
    email = body.email.strip().lower()
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit: per-email and per-IP
    await _rate_limit(f"rl:otp:email:{email}", RL_OTP_PER_EMAIL)
    await _rate_limit(f"rl:otp:ip:{client_ip}", RL_OTP_PER_IP)

    try:
        await send_otp(email)
    except Exception as e:
        logger.error("Failed to send OTP to %s: %s", email, e)
        raise HTTPException(status_code=502, detail="Failed to send one-time code.")

    return {"otp_sent": True, "email": email}


# ────────────────────────────────────────────────────────────
# 2. Verify OTP → create account (if new) → mint API key
# ────────────────────────────────────────────────────────────

@router.post("/verify", operation_id="auth_verify_otp")
async def verify_and_issue_key(body: VerifyRequest, db=Depends(get_db)):
    """
    Verify the one-time code and return an API key.

    On first verification for an email, a new account is created and credited
    with a one-time sign-up bonus. On every verification (new or existing
    account) a fresh API key is minted and returned — store it securely, it is
    shown only once.

    Agents and humans use the exact same flow; the same email always maps to the
    same account.
    """
    email = body.email.strip().lower()
    await _rate_limit(f"rl:verify:email:{email}", RL_VERIFY_PER_EMAIL)

    # 1. Verify the OTP via Supabase Auth
    try:
        result = await verify_otp(email, body.otp)
    except Exception as e:
        logger.warning("OTP verification failed for %s: %s", email, e)
        raise HTTPException(status_code=401, detail="Invalid or expired one-time code.")

    user = result.get("user") or {}
    supabase_user_id = user.get("id")

    # 2. Find or create the account
    account = await db.fetchrow(
        "SELECT id, balance FROM accounts WHERE human_email = $1", email
    )

    if account is None:
        # New account — create with zero balance, then credit the sign-up bonus
        # via the billing ledger so the credit is fully auditable.
        row = await db.fetchrow(
            "INSERT INTO accounts (human_email, supabase_user_id, balance) "
            "VALUES ($1, $2, 0.0000) RETURNING id",
            email, supabase_user_id,
        )
        account_id = row["id"]
        is_new_account = True
        balance = await credit_account(
            db, account_id, SIGNUP_BONUS,
            txn_type="signup_bonus",
            description=f"Sign-up bonus of ${SIGNUP_BONUS:.2f}",
        )
        logger.info("Created account %s for %s with $%.2f bonus", account_id, email, SIGNUP_BONUS)
    else:
        account_id = account["id"]
        is_new_account = False
        balance = float(account["balance"])
        # Backfill supabase_user_id for legacy accounts that lack it
        if supabase_user_id:
            await db.execute(
                "UPDATE accounts SET supabase_user_id = $1 "
                "WHERE id = $2 AND supabase_user_id IS NULL",
                supabase_user_id, account_id,
            )

    # 3. Mint a fresh API key
    key = await _mint_api_key(db, account_id)

    return {
        "account_id": account_id,
        "email": email,
        "is_new_account": is_new_account,
        "balance": round(balance, 4),
        "currency": "USD",
        "api_key": key["api_key"],  # plaintext — shown only once
        "key_id": key["key_id"],
        "key_prefix": key["key_prefix"],
    }


# ────────────────────────────────────────────────────────────
# 3. API key management (authenticated)
# ────────────────────────────────────────────────────────────

@router.post("/keys", operation_id="auth_create_api_key")
async def create_api_key(
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Mint an additional API key for the authenticated account.

    Useful when an agent wants a separate key (e.g. for a new environment)
    without going through the email OTP flow again. The plaintext key is shown
    only once in the response.
    """
    key = await _mint_api_key(db, account["id"])
    return {
        "api_key": key["api_key"],
        "key_id": key["key_id"],
        "key_prefix": key["key_prefix"],
        "message": "Store this key securely — it cannot be retrieved again.",
    }


@router.get("/keys", operation_id="auth_list_api_keys")
async def list_api_keys(
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    List all API keys for the authenticated account.

    Only metadata (prefix, creation time, revocation time) is returned — never
    the secret hash or plaintext.
    """
    rows = await db.fetch(
        """SELECT id, key_prefix, created_at, revoked_at
           FROM api_keys
           WHERE account_id = $1
           ORDER BY created_at DESC""",
        account["id"],
    )
    return {
        "keys": [
            {
                "id": r["id"],
                "key_prefix": r["key_prefix"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
                "active": r["revoked_at"] is None,
                "is_current": r["id"] == account["key_id"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.delete("/keys/{key_id}", operation_id="auth_revoke_api_key")
async def revoke_api_key(
    key_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Revoke an API key by its ID.

    The key currently used to authenticate this request cannot be revoked this
    way (use a different active key, then revoke the old one). Revocation is
    permanent.
    """
    if key_id == account["key_id"]:
        raise HTTPException(
            status_code=400,
            detail="You cannot revoke the API key currently authenticating this request.",
        )

    result = await db.execute(
        "UPDATE api_keys SET revoked_at = now() "
        "WHERE id = $1 AND account_id = $2 AND revoked_at IS NULL",
        key_id, account["id"],
    )
    if result == "UPDATE 0":
        raise HTTPException(
            status_code=404,
            detail="Key not found, not yours, or already revoked.",
        )

    logger.info("Revoked API key %s for account %s", key_id, account["id"])
    return {"revoked": True, "key_id": key_id}
