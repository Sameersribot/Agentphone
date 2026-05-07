"""
AgentLine — Numbers Router
Phone number provisioning, listing, attachment, and release.
Uses Plivo's Phone Numbers API.
"""

import secrets
import logging

from fastapi import APIRouter, Depends, HTTPException

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.number import NumberProvision, NumberOut
from agentline.plivo_client import provision_number, release_number, list_plivo_numbers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/numbers", tags=["Numbers"])


@router.post("")
async def provision(
    body: NumberProvision,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Search for and buy a phone number from Plivo, then attach it to an agent.
    Each agent can only have ONE active number.

    Request body:
      - agent_id: str (required)
      - country: str (default "IN")
      - number_type: "local" | "mobile" | "tollfree" | "fixed" | "national"
      - pattern: optional area code filter (e.g. "22" for Mumbai)
    """
    # Verify agent belongs to this account
    agent = await db.fetchrow(
        "SELECT * FROM agents WHERE id = $1 AND account_id = $2",
        body.agent_id,
        account["id"],
    )
    if not agent:
        raise HTTPException(404, "Agent not found.")

    # Enforce one number per agent
    existing = await db.fetchrow(
        "SELECT id, phone_number FROM phone_numbers WHERE agent_id = $1 AND status = 'active'",
        body.agent_id,
    )
    if existing:
        raise HTTPException(
            409,
            f"Agent already has an active number: {existing['phone_number']} (id: {existing['id']}). "
            "Release it first with DELETE /v1/numbers/{number_id} before provisioning a new one.",
        )

    # Provision via Plivo
    try:
        number_data = await provision_number(
            country=body.country,
            number_type=body.number_type,
            pattern=body.pattern,
            agent_id=body.agent_id,
        )
    except Exception as e:
        error_msg = str(e)
        if "compliance" in error_msg.lower() or "kyc" in error_msg.lower():
            raise HTTPException(
                403,
                f"KYC/compliance issue: {error_msg}. "
                "Complete compliance in Plivo Console → Phone Numbers → Compliance.",
            )
        raise HTTPException(502, f"Failed to provision number: {error_msg}")

    # Save to database
    number_id = f"num_{secrets.token_urlsafe(12)}"
    try:
        await db.execute(
            """INSERT INTO phone_numbers
               (id, account_id, agent_id, provider_id, phone_number, country, status)
               VALUES ($1, $2, $3, $4, $5, $6, 'active')""",
            number_id,
            account["id"],
            body.agent_id,
            number_data["provider_id"],
            number_data["phone_number"],
            body.country,
        )
        logger.info(
            "Number %s (%s) saved to DB for agent %s",
            number_id, number_data["phone_number"], body.agent_id,
        )
    except Exception as e:
        logger.error(
            "DB INSERT failed for number %s: %s — number was bought on Plivo but NOT saved!",
            number_data["phone_number"], e,
        )
        # Try to release the number we just bought since DB save failed
        try:
            await release_number(number_data["provider_id"])
        except Exception:
            pass
        raise HTTPException(
            500,
            f"Number {number_data['phone_number']} was provisioned on Plivo but failed to save to database: {e}. "
            "The number has been released. Please try again.",
        )

    # Verify it was actually saved
    verify = await db.fetchrow("SELECT id FROM phone_numbers WHERE id = $1", number_id)
    if not verify:
        logger.error("Number %s INSERT succeeded but verification SELECT returned nothing!", number_id)
        raise HTTPException(500, "Database write verification failed. Please try again.")

    return {
        "id": number_id,
        "agent_id": body.agent_id,
        "phone_number": number_data["phone_number"],
        "country": body.country,
        "number_type": body.number_type,
        "status": "active",
    }


@router.get("")
async def list_numbers(
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """List all phone numbers for the authenticated account."""
    rows = await db.fetch(
        """SELECT * FROM phone_numbers
           WHERE account_id = $1
           ORDER BY created_at DESC""",
        account["id"],
    )
    return [dict(r) for r in rows]


@router.get("/available")
async def list_available(
    account=Depends(get_current_account),
):
    """
    List numbers currently rented on the Plivo account.
    Useful for debugging or manually attaching numbers.
    """
    numbers = await list_plivo_numbers()
    return {"plivo_numbers": numbers}


@router.get("/{number_id}")
async def get_number(
    number_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Get details of a specific phone number."""
    row = await db.fetchrow(
        "SELECT * FROM phone_numbers WHERE id = $1 AND account_id = $2",
        number_id,
        account["id"],
    )
    if not row:
        raise HTTPException(404, "Number not found.")
    return dict(row)


@router.post("/attach")
async def attach_existing_number(
    phone_number: str,
    agent_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Manually attach a number that was bought directly from Plivo Console.
    Use this when auto-provisioning fails due to KYC or inventory issues.
    Each agent can only have ONE active number.

    Query params:
      - phone_number: E.164 format (e.g. "+919876543210")
      - agent_id: agent to attach to
    """
    agent = await db.fetchrow(
        "SELECT * FROM agents WHERE id = $1 AND account_id = $2",
        agent_id,
        account["id"],
    )
    if not agent:
        raise HTTPException(404, "Agent not found.")

    # Enforce one number per agent
    existing_agent_num = await db.fetchrow(
        "SELECT id, phone_number FROM phone_numbers WHERE agent_id = $1 AND status = 'active'",
        agent_id,
    )
    if existing_agent_num:
        raise HTTPException(
            409,
            f"Agent already has an active number: {existing_agent_num['phone_number']}. "
            "Release it first before attaching a new one.",
        )

    # Check if this phone number is already attached
    existing = await db.fetchrow(
        "SELECT id FROM phone_numbers WHERE phone_number = $1 AND status = 'active'",
        phone_number,
    )
    if existing:
        raise HTTPException(409, f"Number {phone_number} is already attached (id: {existing['id']}).")

    number_id = f"num_{secrets.token_urlsafe(12)}"
    provider_id = phone_number.lstrip("+")

    try:
        await db.execute(
            """INSERT INTO phone_numbers
               (id, account_id, agent_id, provider_id, phone_number, country, status)
               VALUES ($1, $2, $3, $4, $5, $6, 'active')""",
            number_id,
            account["id"],
            agent_id,
            provider_id,
            phone_number,
            "IN" if phone_number.startswith("+91") else "US",
        )
        logger.info("Manually attached number %s to agent %s", phone_number, agent_id)
    except Exception as e:
        logger.error("Failed to attach number %s: %s", phone_number, e)
        raise HTTPException(500, f"Failed to save number to database: {e}")

    return {
        "id": number_id,
        "agent_id": agent_id,
        "phone_number": phone_number,
        "status": "active",
        "message": "Number attached. Configure its Answer URL in Plivo Console to point to your server.",
    }


@router.patch("/{number_id}/reassign")
async def reassign_number(
    number_id: str,
    agent_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Reassign a phone number to a different agent (if target agent has no number)."""
    number = await db.fetchrow(
        "SELECT * FROM phone_numbers WHERE id = $1 AND account_id = $2",
        number_id,
        account["id"],
    )
    if not number:
        raise HTTPException(404, "Number not found.")

    agent = await db.fetchrow(
        "SELECT * FROM agents WHERE id = $1 AND account_id = $2",
        agent_id,
        account["id"],
    )
    if not agent:
        raise HTTPException(404, "Agent not found.")

    # Enforce one number per agent on the target
    existing = await db.fetchrow(
        "SELECT id, phone_number FROM phone_numbers WHERE agent_id = $1 AND status = 'active'",
        agent_id,
    )
    if existing:
        raise HTTPException(
            409,
            f"Target agent already has an active number: {existing['phone_number']}.",
        )

    await db.execute(
        "UPDATE phone_numbers SET agent_id = $1 WHERE id = $2",
        agent_id,
        number_id,
    )

    return {"number_id": number_id, "agent_id": agent_id, "reassigned": True}


@router.delete("/{number_id}")
async def release(
    number_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Release a phone number back to Plivo."""
    row = await db.fetchrow(
        "SELECT * FROM phone_numbers WHERE id = $1 AND account_id = $2",
        number_id,
        account["id"],
    )
    if not row:
        raise HTTPException(404, "Number not found.")

    try:
        await release_number(row["provider_id"])
    except Exception:
        pass  # Best effort

    await db.execute(
        """UPDATE phone_numbers
           SET status = 'released', released_at = now()
           WHERE id = $1""",
        number_id,
    )

    return {"released": True, "number_id": number_id}
