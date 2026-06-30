"""
AgentLine — Calls Router
Initiate and manage voice calls.
"""

import secrets
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.call import CallRequest
from agentline.signalwire_client import initiate_call as signalwire_initiate_call
from agentline.signalwire_client import hangup_call as signalwire_hangup_call
from agentline.billing import check_balance, CALL_RATE_PER_MINUTE
from agentline.voice.owner_mode import resolve_outbound_owner_overrides

# Minimum balance required to initiate a call (~5 minutes worth)
MIN_CALL_BALANCE = round(CALL_RATE_PER_MINUTE * 5, 2)  # $0.50

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/calls", tags=["Calls"])


@router.post("", operation_id="make_outbound_call")
async def create_call(
    body: CallRequest,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Make an outbound phone call from your AI agent.

    Initiates a real phone call from the AI agent's phone number to the
    specified destination. The agent uses its configured system prompt,
    voice, and greeting to conduct the conversation autonomously.

    The AI agent handles the entire call — speech-to-text, LLM reasoning,
    and text-to-speech — in real time. The call transcript is saved
    automatically and can be retrieved via GET /v1/calls/{call_id}/transcript.

    Request body:
      - agent_id: the AI agent making the call
      - to_number: destination phone number in E.164 format (e.g. "+12125551234")
      - from_number_id: (optional) specific number to call from
      - system_prompt: (optional) override the agent's default prompt for this call
      - initial_greeting: (optional) override the agent's greeting for this call
      - voice_id: (optional) override the voice for this call
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

    # ── Billing: require minimum balance before initiating call ──
    try:
        await check_balance(db, account["id"], MIN_CALL_BALANCE)
    except ValueError as e:
        raise HTTPException(
            402,
            f"Insufficient balance to make a call. Minimum ${MIN_CALL_BALANCE:.2f} required. {e}",
        )

    call_id = f"call_{secrets.token_urlsafe(12)}"
    # ── Owner task mode (outbound) ───────────────────────────────
    # v1.08: if the destination is the agent's registered owner_phone,
    # the call enters task mode — owner-mode system prompt + "Hey boss"
    # greeting — mirroring the inbound behaviour. This also causes the
    # hangup handler to emit a `call.owner_task` event (detected via the
    # OWNER_MODE_SENTINEL prefix on system_prompt) so the external agent
    # picks the transcript up as a task to execute.
    # Explicit per-call overrides from the body still take priority.
    system_prompt, initial_greeting, is_owner_call = resolve_outbound_owner_overrides(
        agent, body.to_number, body.system_prompt, body.initial_greeting,
    )
    if is_owner_call:
        logger.info("Outbound call — OWNER DETECTED (to %s)", body.to_number)
    now = datetime.now(timezone.utc)

    await db.execute(
        """INSERT INTO calls (id, account_id, agent_id, number_id, direction,
           from_number, to_number, system_prompt, initial_greeting, voice_id, status, started_at)
           VALUES ($1,$2,$3,$4,'outbound',$5,$6,$7,$8,$9,'initiated',$10)""",
        call_id, account["id"], body.agent_id, number["id"],
        number["phone_number"], body.to_number, system_prompt,
        initial_greeting,
        body.voice_id,  # Per-call voice override (None = use agent/account default)
        now,
    )

    try:
        provider_call_id = await signalwire_initiate_call(
            from_number=number["phone_number"],
            to_number=body.to_number,
            call_id=call_id,
        )
    except Exception as e:
        await db.execute("UPDATE calls SET status='failed' WHERE id=$1", call_id)
        raise HTTPException(502, f"Failed to initiate call: {str(e)}")

    await db.execute(
        "UPDATE calls SET provider_call_id=$1, status='in-progress' WHERE id=$2",
        provider_call_id, call_id
    )

    return {
        "id": call_id, "agent_id": body.agent_id,
        "from_number": number["phone_number"], "to_number": body.to_number,
        "direction": "outbound", "status": "in-progress",
        "started_at": now.isoformat(),
    }


@router.get("", operation_id="list_calls")
async def list_calls(
    agent_id: str | None = Query(None, description="Filter calls by AI agent ID"),
    status: str | None = Query(None, description="Filter by call status: 'initiated', 'in-progress', 'completed', or 'failed'"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of calls to return (1-200)"),
    offset: int = Query(0, ge=0, description="Number of calls to skip for pagination"),
    account=Depends(get_current_account), db=Depends(get_db),
):
    """
    List voice calls made by your AI agents.

    Returns call history with optional filters by agent or status.
    Each entry includes direction (inbound/outbound), duration,
    phone numbers, and current status.

    Filters:
      - agent_id: only calls for a specific AI agent
      - status: "initiated", "in-progress", "completed", or "failed"
    """
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


@router.get("/{call_id}", operation_id="get_call_details")
async def get_call(call_id: str, account=Depends(get_current_account), db=Depends(get_db)):
    """
    Get full details of a specific voice call.

    Returns the call's metadata including direction, phone numbers,
    status, duration, AI agent configuration used, and the full
    conversation transcript between the AI agent and the caller.
    """
    row = await db.fetchrow(
        "SELECT * FROM calls WHERE id=$1 AND account_id=$2", call_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Call not found.")
    return dict(row)


@router.get("/{call_id}/transcript", operation_id="get_call_transcript")
async def get_transcript(call_id: str, account=Depends(get_current_account), db=Depends(get_db)):
    """
    Get the full conversation transcript for a call.

    Returns the complete speech-to-text transcript of the phone call,
    with each turn labeled by role ("human" for the caller, "assistant"
    for the AI agent). Useful for reviewing what was said on the call,
    extracting information, or auditing AI agent behavior.
    """
    row = await db.fetchrow(
        "SELECT transcript, status FROM calls WHERE id=$1 AND account_id=$2",
        call_id, account["id"],
    )
    if not row:
        raise HTTPException(404, "Call not found.")
    return {"call_id": call_id, "status": row["status"], "transcript": row["transcript"] or []}


@router.post("/{call_id}/hangup", operation_id="hangup_call")
async def hangup_call(
    call_id: str,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Hang up an active phone call.

    Programmatically terminates an in-progress voice call. Use this
    when the AI agent needs to end the conversation, or to force-stop
    a call that is no longer needed. The call's final transcript and
    billing are processed automatically after hangup.
    """
    call = await db.fetchrow(
        "SELECT * FROM calls WHERE id=$1 AND account_id=$2",
        call_id, account["id"],
    )
    if not call:
        raise HTTPException(404, "Call not found.")
    if call["status"] == "completed":
        return {"call_id": call_id, "status": "completed", "message": "Call already ended."}

    provider_call_id = call.get("provider_call_id")
    if not provider_call_id:
        # No provider call ID means the call never connected — just mark completed
        await db.execute(
            "UPDATE calls SET status='completed', ended_at=now() WHERE id=$1",
            call_id,
        )
        return {"call_id": call_id, "status": "completed", "message": "Call was never connected, marked as completed."}

    try:
        await signalwire_hangup_call(provider_call_id)
    except Exception as e:
        logger.warning("Provider hangup failed for call %s: %s (marking completed anyway)", call_id, e)

    # Mark the call as completed in our DB
    await db.execute(
        "UPDATE calls SET status='completed', ended_at=now() WHERE id=$1 AND status!='completed'",
        call_id,
    )

    logger.info("Call %s — agent-initiated hangup", call_id)
    return {"call_id": call_id, "status": "completed", "message": "Call terminated."}
