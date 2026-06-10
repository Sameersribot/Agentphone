"""
AgentLine — Voice Settings Router
Endpoints for managing voice preferences at the account level,
and listing available voice presets.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.voice.voices import (
    resolve_voice_id,
    list_available_voices,
    DEFAULT_VOICE_ID,
    VOICE_PRESETS,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice"])


# ── Request/Response Models ───────────────────────────────────

class VoiceSettingUpdate(BaseModel):
    """Set account-level default voice."""
    voice_id: str  # Cartesia UUID or preset name (e.g. "female-1")


class VoiceSettingOut(BaseModel):
    """Current voice settings for the account."""
    account_id: str
    default_voice_id: str | None
    resolved_voice_id: str  # What will actually be used (after resolution)


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/v1/voices", operation_id="list_available_voices")
async def get_available_voices():
    """
    List all available voice presets.

    Returns named presets that can be used as `voice_id` in agent
    configuration or per-call overrides. You can also pass any
    valid Cartesia voice UUID directly.
    """
    return {
        "voices": list_available_voices(),
        "default_voice_id": DEFAULT_VOICE_ID,
        "usage_hint": (
            "Pass a preset name (e.g. 'female-1') or a Cartesia UUID as voice_id "
            "when creating/updating agents or making calls."
        ),
    }


@router.get("/v1/account/voice", operation_id="get_account_voice")
async def get_account_voice(
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Get the current account-level default voice setting.

    This voice is used for all agents under this account unless
    overridden at the agent or per-call level.
    """
    row = await db.fetchrow(
        "SELECT default_voice_id FROM accounts WHERE id = $1",
        account["id"],
    )
    raw_voice = row["default_voice_id"] if row else None

    return VoiceSettingOut(
        account_id=account["id"],
        default_voice_id=raw_voice,
        resolved_voice_id=resolve_voice_id(raw_voice),
    )


@router.patch("/v1/account/voice", operation_id="set_account_voice")
async def update_account_voice(
    body: VoiceSettingUpdate,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Set the account-level default voice.

    This becomes the default for ALL agents under this account,
    unless an agent has its own voice_id set, or a specific call
    overrides it.

    Voice resolution priority:
      1. Per-call voice_id (POST /v1/calls)
      2. Agent voice_id (PATCH /v1/agents/{id})
      3. Account default (this endpoint)  ← you are here
      4. System default (Barbershop Man)

    Accepts:
      - A preset name: "female-1", "female-2", "male-1"
      - A Cartesia voice UUID: "e07c00bc-4134-4eae-9ea4-1a55fb45746b"
    """
    # Validate the voice_id resolves to something valid
    resolved = resolve_voice_id(body.voice_id)

    await db.execute(
        "UPDATE accounts SET default_voice_id = $1 WHERE id = $2",
        body.voice_id,  # Store the raw value (preset name or UUID)
        account["id"],
    )

    logger.info(
        "Account %s — set default voice to '%s' (resolves to %s)",
        account["id"], body.voice_id, resolved,
    )

    return VoiceSettingOut(
        account_id=account["id"],
        default_voice_id=body.voice_id,
        resolved_voice_id=resolved,
    )


@router.delete("/v1/account/voice", operation_id="reset_account_voice")
async def reset_account_voice(
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Reset account voice to the system default.

    Removes the account-level override so all agents fall back to
    the system default voice (unless they have their own voice_id set).
    """
    await db.execute(
        "UPDATE accounts SET default_voice_id = NULL WHERE id = $1",
        account["id"],
    )

    logger.info("Account %s — reset default voice to system default", account["id"])

    return {
        "account_id": account["id"],
        "default_voice_id": None,
        "resolved_voice_id": DEFAULT_VOICE_ID,
        "message": "Account voice reset to system default.",
    }
