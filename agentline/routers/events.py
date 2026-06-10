"""
AgentLine — Events Router
Server-side event mailbox for agents that can't expose webhooks.

When calls complete, transcripts are pushed here automatically.
Agents poll GET /v1/events to receive them.
"""

import json
import logging

from fastapi import APIRouter, Depends, Query

from agentline.auth_middleware import get_current_account
from agentline.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/events", tags=["Events"])


@router.get("", operation_id="poll_events")
async def list_events(
    agent_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(50, le=200),
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Poll for events (call completions, transcripts, etc.).

    Events are returned oldest-first and auto-deleted after retrieval.
    Your agent should call this endpoint periodically to receive
    call transcripts and other notifications.

    Filters:
      - agent_id: only events for a specific agent
      - event_type: e.g. "call.completed", "call.failed"
    """
    conditions = ["account_id = $1"]
    params: list = [account["id"]]
    idx = 2

    if agent_id:
        conditions.append(f"agent_id = ${idx}")
        params.append(agent_id)
        idx += 1

    if event_type:
        conditions.append(f"event_type = ${idx}")
        params.append(event_type)
        idx += 1

    where = " AND ".join(conditions)
    params.append(limit)

    rows = await db.fetch(
        f"""SELECT id, event_id, agent_id, event_type, payload, created_at
           FROM event_mailbox
           WHERE {where}
           ORDER BY created_at ASC
           LIMIT ${idx}""",
        *params,
    )

    events = []
    ids_to_delete = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                pass

        events.append({
            "event_id": row["event_id"],
            "agent_id": row["agent_id"],
            "event_type": row["event_type"],
            "payload": payload,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })
        ids_to_delete.append(row["id"])

    # Auto-delete retrieved events (consume-once pattern)
    if ids_to_delete:
        await db.execute(
            "DELETE FROM event_mailbox WHERE id = ANY($1::int[])",
            ids_to_delete,
        )
        logger.info("Delivered %d events to account %s", len(events), account["id"][:12])

    return {
        "events": events,
        "count": len(events),
    }


@router.get("/peek", operation_id="peek_events")
async def peek_events(
    agent_id: str | None = None,
    limit: int = Query(50, le=200),
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Peek at pending events WITHOUT consuming them.
    Useful for checking if there are events before committing to process them.
    """
    conditions = ["account_id = $1"]
    params: list = [account["id"]]
    idx = 2

    if agent_id:
        conditions.append(f"agent_id = ${idx}")
        params.append(agent_id)
        idx += 1

    where = " AND ".join(conditions)
    params.append(limit)

    count = await db.fetchval(
        f"SELECT COUNT(*) FROM event_mailbox WHERE {where}",
        *params[:-1],  # exclude limit for count
    )

    rows = await db.fetch(
        f"""SELECT event_id, agent_id, event_type, created_at
           FROM event_mailbox
           WHERE {where}
           ORDER BY created_at ASC
           LIMIT ${idx}""",
        *params,
    )

    return {
        "pending_count": count,
        "events": [
            {
                "event_id": row["event_id"],
                "agent_id": row["agent_id"],
                "event_type": row["event_type"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ],
    }
