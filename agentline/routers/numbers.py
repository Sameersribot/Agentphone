"""
AgentLine — Numbers Router
Phone number provisioning, listing, attachment, and release.
Uses Plivo's Phone Numbers API.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.number import NumberProvision, NumberOut
from agentline.plivo_client import provision_number, release_number, list_plivo_numbers

router = APIRouter(prefix="/v1/numbers", tags=["Numbers"])


@router.post("")
async def provision(
    body: NumberProvision,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Search for and buy a phone number from Plivo, then attach it to an agent.

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
                "Complete compliance in Plivo Console → Phone Numbers → Compliance."
            )
        raise HTTPException(502, f"Failed to provision number: {error_msg}")

    number_id = f"num_{secrets.token_urlsafe(12)}"
    await db.execute(
        """INSERT INTO phone_numbers
           (id, account_id, agent_id, provider_id, phone_number, country)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        number_id,
        account["id"],
        body.agent_id,
        number_data["provider_id"],
        number_data["phone_number"],
        body.country,
    )

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

    # Check if already attached
    existing = await db.fetchrow(
        "SELECT id FROM phone_numbers WHERE phone_number = $1",
        phone_number,
    )
    if existing:
        raise HTTPException(409, f"Number {phone_number} is already attached.")

    number_id = f"num_{secrets.token_urlsafe(12)}"
    provider_id = phone_number.lstrip("+")

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
    """Reassign a phone number to a different agent."""
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
