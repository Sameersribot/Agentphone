"""
AgentLine — Calls Router
Initiate and manage voice calls.

Agent-Controlled Relay Mode:
  1. Agent creates call → POST /v1/calls
  2. Agent polls for speech → GET /v1/calls/{id}/listen (supports long-polling)
  3. Agent sends response → POST /v1/calls/{id}/speak
  No webhook registration needed.
"""

import secrets
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from agentline.auth_middleware import get_current_account
from agentline.database import get_db, get_db_conn
from agentline.models.call import CallRequest
from agentline.plivo_client import initiate_call

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/calls", tags=["Calls"])


@router.post("")
async def create_call(
    body: CallRequest,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Initiate an outbound voice call.
    
    The call will ring the person, speak the agent's greeting,
    then wait for speech. Use /listen and /speak to interact.
    """
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
        provider_call_id = await initiate_call(
            from_number=number["phone_number"],
            to_number=body.to_number,
            call_id=call_id,
        )
    except Exception as e:
        await db.execute("UPDATE calls SET status='failed' WHERE id=$1", call_id)
        raise HTTPException(502, f"Failed to initiate call: {str(e)}")

    await db.execute(
        "UPDATE calls SET provider_call_id=$1, status='in-progress' WHERE id=$2",
        provider_call_id, call_id,
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
    """Get the full transcript for a call."""
    row = await db.fetchrow(
        "SELECT transcript, status FROM calls WHERE id=$1 AND account_id=$2",
        call_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Call not found.")
    return {"call_id": call_id, "status": row["status"], "transcript": row["transcript"] or []}


@router.post("/{call_id}/speak")
async def speak_on_call(
    call_id: str,
    body: dict,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Send text to be spoken on an active call.
    
    The Plivo wait loop will pick this up within ~3 seconds
    and speak it to the person on the phone.
    
    Body: {"text": "Sure, I can help you with that."}
    """
    text = body.get("text", "")
    if not text:
        raise HTTPException(400, "text is required")

    call = await db.fetchrow(
        "SELECT * FROM calls WHERE id=$1 AND account_id=$2",
        call_id, account["id"],
    )
    if not call:
        raise HTTPException(404, "Call not found.")
    if call["status"] not in ("in-progress", "initiated"):
        raise HTTPException(400, f"Call is {call['status']}, cannot speak on it.")

    await db.execute(
        """INSERT INTO call_responses (call_id, response_text, spoken, created_at)
           VALUES ($1, $2, false, now())""",
        call_id, text,
    )

    logger.info("Call %s — agent queued: %s", call_id, text[:80])
    return {"queued": True, "call_id": call_id, "text": text}


@router.get("/{call_id}/listen")
async def listen_from_call(
    call_id: str,
    wait: bool = Query(False, description="Long-poll: hold connection until new speech arrives (max 25s)"),
    after: int = Query(0, description="Only return transcript entries after this index"),
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Get speech from the caller on an active call.
    
    Two modes:
    - **Instant** (default): Returns current transcript immediately
    - **Long-poll** (`?wait=true`): Holds connection up to 25 seconds 
      until new speech arrives, then returns it. Perfect for agents
      that want to react in real-time without webhooks.
    
    Use `?after=N` to only get transcript entries after index N,
    so you don't re-process old messages.
    """
    # Verify ownership
    call = await db.fetchrow(
        "SELECT transcript, status FROM calls WHERE id=$1 AND account_id=$2",
        call_id, account["id"],
    )
    if not call:
        raise HTTPException(404, "Call not found.")

    if not wait:
        # Instant mode — return current state
        return _format_listen_response(call_id, call, after)

    # Long-poll mode — wait for new speech (up to 25 seconds)
    initial_len = _transcript_len(call.get("transcript"))

    for _ in range(25):  # Check once per second, max 25 seconds
        await asyncio.sleep(1)

        async with get_db_conn() as poll_db:
            call = await poll_db.fetchrow(
                "SELECT transcript, status FROM calls WHERE id=$1",
                call_id,
            )

        if not call:
            break

        current_len = _transcript_len(call.get("transcript"))

        # New speech arrived!
        if current_len > initial_len:
            return _format_listen_response(call_id, call, after)

        # Call ended
        if call["status"] == "completed":
            return _format_listen_response(call_id, call, after)

    # Timeout — return current state
    return _format_listen_response(call_id, call, after)


def _transcript_len(transcript) -> int:
    """Get the number of entries in a transcript."""
    if not transcript:
        return 0
    if isinstance(transcript, list):
        return len(transcript)
    try:
        return len(json.loads(transcript))
    except (json.JSONDecodeError, TypeError):
        return 0


def _format_listen_response(call_id: str, call, after: int = 0) -> dict:
    """Format the /listen response with transcript filtering."""
    transcript = call.get("transcript") or []
    if isinstance(transcript, str):
        try:
            transcript = json.loads(transcript)
        except (json.JSONDecodeError, TypeError):
            transcript = []

    # Filter to only new entries if after is specified
    new_entries = transcript[after:] if after > 0 else transcript

    # Get last human speech
    last_human = None
    for turn in reversed(transcript):
        if isinstance(turn, dict) and turn.get("role") == "human":
            last_human = turn
            break

    return {
        "call_id": call_id,
        "status": call["status"],
        "last_speech": last_human,
        "new_entries": new_entries,
        "total_turns": len(transcript),
        "transcript": transcript,
    }
