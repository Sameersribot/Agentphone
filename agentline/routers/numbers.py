"""
AgentLine — Numbers Router
Phone number provisioning, listing, attachment, and release.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.number import NumberProvision, NumberOut
from agentline.plivo_client import provision_number, release_number

router = APIRouter(prefix="/v1/numbers", tags=["Numbers"])


@router.post("")
async def provision(
    body: NumberProvision,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Provision a new phone number from Plivo and attach it to an agent.
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
            area_code=body.area_code,
            agent_id=body.agent_id,
        )
    except Exception as e:
        raise HTTPException(502, f"Failed to provision number: {str(e)}")

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


@router.patch("/{number_id}/attach")
async def attach_number(
    number_id: str,
    agent_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Attach a phone number to a different agent."""
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

    return {"number_id": number_id, "agent_id": agent_id, "attached": True}


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
        pass  # Best effort — number may already be released

    await db.execute(
        """UPDATE phone_numbers
           SET status = 'released', released_at = now()
           WHERE id = $1""",
        number_id,
    )

    return {"released": True, "number_id": number_id}
