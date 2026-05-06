"""
AgentLine — Calls Router
Initiate and manage voice calls.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.call import CallRequest
from agentline.telnyx_client import initiate_call

router = APIRouter(prefix="/v1/calls", tags=["Calls"])


@router.post("")
async def create_call(
    body: CallRequest,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Initiate an outbound voice call through the STT → LLM → TTS pipeline."""
    agent = await db.fetchrow(
        "SELECT * FROM agents WHERE id = $1 AND account_id = $2",
        body.agent_id, account["id"],
    )
    if not agent:
        raise HTTPException(404, "Agent not found.")

    if body.from_number_id:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE id=$1 AND account_id=$2 AND status='active'",
            body.from_number_id, account["id"],
        )
    else:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE agent_id=$1 AND status='active' ORDER BY created_at LIMIT 1",
            body.agent_id,
        )
    if not number:
        raise HTTPException(400, "Agent has no active phone number.")

    call_id = f"call_{secrets.token_urlsafe(12)}"
    system_prompt = body.system_prompt or agent["system_prompt"]
    now = datetime.now(timezone.utc)

    await db.execute(
        """INSERT INTO calls (id, account_id, agent_id, number_id, direction,
           from_number, to_number, system_prompt, status, started_at)
           VALUES ($1,$2,$3,$4,'outbound',$5,$6,$7,'initiated',$8)""",
        call_id, account["id"], body.agent_id, number["id"],
        number["phone_number"], body.to_number, system_prompt, now,
    )

    try:
        telnyx_call_id = await initiate_call(
            from_number=number["phone_number"],
            to_number=body.to_number,
            call_id=call_id,
        )
    except Exception as e:
        await db.execute("UPDATE calls SET status='failed' WHERE id=$1", call_id)
        raise HTTPException(502, f"Failed to initiate call: {str(e)}")

    await db.execute(
        "UPDATE calls SET telnyx_call_id=$1, status='in-progress' WHERE id=$2",
        telnyx_call_id, call_id,
    )

    return {
        "id": call_id, "agent_id": body.agent_id,
        "from_number": number["phone_number"], "to_number": body.to_number,
        "direction": "outbound", "status": "in-progress",
        "started_at": now.isoformat(),
    }


@router.get("")
async def list_calls(
    agent_id: str | None = None, status: str | None = None,
    limit: int = 50, offset: int = 0,
    account=Depends(get_current_account), db=Depends(get_db),
):
    """List calls with optional filters."""
    conditions = ["account_id = $1"]
    params: list = [account["id"]]
    idx = 2
    if agent_id:
        conditions.append(f"agent_id = ${idx}"); params.append(agent_id); idx += 1
    if status:
        conditions.append(f"status = ${idx}"); params.append(status); idx += 1
    where = " AND ".join(conditions)
    params.extend([limit, offset])
    rows = await db.fetch(
        f"SELECT * FROM calls WHERE {where} ORDER BY started_at DESC LIMIT ${idx} OFFSET ${idx+1}",
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/{call_id}")
async def get_call(call_id: str, account=Depends(get_current_account), db=Depends(get_db)):
    """Get call details including transcript."""
    row = await db.fetchrow(
        "SELECT * FROM calls WHERE id=$1 AND account_id=$2", call_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Call not found.")
    return dict(row)


@router.get("/{call_id}/transcript")
async def get_transcript(call_id: str, account=Depends(get_current_account), db=Depends(get_db)):
    """Get the transcript for a completed call."""
    row = await db.fetchrow(
        "SELECT transcript, status FROM calls WHERE id=$1 AND account_id=$2",
        call_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Call not found.")
    return {"call_id": call_id, "status": row["status"], "transcript": row["transcript"] or []}
