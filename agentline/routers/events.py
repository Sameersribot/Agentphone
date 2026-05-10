"""
AgentLine — Events Router (Mailbox Mode)

Server-side event mailbox — eliminates the need for agents to expose
a public webhook URL. Events are queued in the database and the agent
pulls them via long-polling.

This solves the core problem: agents running on localhost (127.0.0.1)
can't receive inbound webhooks from AgentLine's cloud servers. With
mailbox mode, the agent simply polls GET /v1/events?wait=true and
gets events pushed to it without needing any public endpoint.

Flow:
  1. Speech/SMS/call events fire internally
  2. dispatch_event() stores them in the `event_mailbox` table
  3. Agent polls GET /v1/events?wait=true (long-poll, 25s max)
  4. Agent processes event and responds via POST /v1/calls/{id}/speak
  5. Events auto-expire after 5 minutes (server cleanup)
"""

import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from agentline.auth_middleware import get_current_account
from agentline.database import get_db, get_db_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/events", tags=["Events"])


@router.get("")
async def poll_events(
    agent_id: str | None = Query(None, description="Filter events for a specific agent"),
    wait: bool = Query(False, description="Long-poll: hold up to 25s for new events"),
    ack: str | None = Query(None, description="Acknowledge (delete) events up to this event ID"),
    account=Depends(get_current_account),
):
    """
    Pull pending events for your agent(s).

    **Instant mode** (default): Returns all pending events immediately.
    **Long-poll** (`?wait=true`): Holds connection up to 25 seconds
    until a new event arrives. Perfect for agents that want real-time
    events without exposing a public webhook URL.

    Pass `?ack=evt_xxx` to acknowledge all events up to that ID,
    removing them from the queue.

    This is the recommended integration pattern for agents running
    on localhost, behind firewalls, or without a public domain.
    """
    account_id = account["id"]

    # Acknowledge previously seen events
    if ack:
        async with get_db_conn() as db:
            await db.execute(
                """DELETE FROM event_mailbox
                   WHERE account_id = $1 AND id <= (
                       SELECT id FROM event_mailbox WHERE event_id = $2 AND account_id = $1
                   )""",
                account_id, ack,
            )

    if not wait:
        return await _fetch_events(account_id, agent_id)

    # Long-poll: check every second for up to 25 seconds
    initial = await _fetch_events(account_id, agent_id)
    if initial["events"]:
        return initial

    for _ in range(25):
        await asyncio.sleep(1)
        result = await _fetch_events(account_id, agent_id)
        if result["events"]:
            return result

    # Timeout — return empty
    return {"events": [], "pending": 0}


@router.post("/ack")
async def acknowledge_events(
    body: dict,
    account=Depends(get_current_account),
):
    """
    Acknowledge (delete) events by their IDs.

    Body: {"event_ids": ["evt_xxx", "evt_yyy"]}

    Acknowledged events are permanently removed from the mailbox.
    """
    event_ids = body.get("event_ids", [])
    if not event_ids:
        raise HTTPException(400, "event_ids is required")

    async with get_db_conn() as db:
        await db.execute(
            "DELETE FROM event_mailbox WHERE event_id = ANY($1) AND account_id = $2",
            event_ids, account["id"],
        )

    return {"acknowledged": len(event_ids)}


@router.delete("")
async def clear_events(
    agent_id: str | None = Query(None, description="Clear events for a specific agent only"),
    account=Depends(get_current_account),
):
    """Clear all pending events (or just for a specific agent)."""
    async with get_db_conn() as db:
        if agent_id:
            count = await db.fetchval(
                "DELETE FROM event_mailbox WHERE account_id = $1 AND agent_id = $2 RETURNING count(*)",
                account["id"], agent_id,
            )
        else:
            count = await db.fetchval(
                "DELETE FROM event_mailbox WHERE account_id = $1 RETURNING count(*)",
                account["id"],
            )
    return {"cleared": count or 0}


# ────────────────────────────────────────────────────────────
# Internal: Queue an event into the mailbox
# ────────────────────────────────────────────────────────────

async def queue_event(account_id: str, agent_id: str | None, event: dict):
    """
    Store an event in the mailbox for the agent to pull.
    Called internally by the webhook dispatcher when no external
    webhook URL is configured (mailbox mode).
    """
    event_id = f"evt_{secrets.token_urlsafe(12)}"
    event["event_id"] = event_id
    event["timestamp"] = datetime.now(timezone.utc).isoformat()

    try:
        async with get_db_conn() as db:
            await db.execute(
                """INSERT INTO event_mailbox
                   (event_id, account_id, agent_id, event_type, payload, created_at)
                   VALUES ($1, $2, $3, $4, $5, now())""",
                event_id,
                account_id,
                agent_id,
                event.get("event", "unknown"),
                json.dumps(event, default=str),
            )

            # Cleanup: remove events older than 5 minutes to prevent unbounded growth
            await db.execute(
                """DELETE FROM event_mailbox
                   WHERE account_id = $1
                   AND created_at < now() - INTERVAL '5 minutes'""",
                account_id,
            )

        logger.info("Event %s queued for agent %s: %s", event_id, agent_id, event.get("event"))
    except Exception as e:
        logger.error("Failed to queue event for agent %s: %s", agent_id, e)


# ────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────

async def _fetch_events(account_id: str, agent_id: str | None = None) -> dict:
    """Fetch all pending events from the mailbox."""
    async with get_db_conn() as db:
        if agent_id:
            rows = await db.fetch(
                """SELECT event_id, agent_id, event_type, payload, created_at
                   FROM event_mailbox
                   WHERE account_id = $1 AND agent_id = $2
                   ORDER BY created_at ASC
                   LIMIT 50""",
                account_id, agent_id,
            )
        else:
            rows = await db.fetch(
                """SELECT event_id, agent_id, event_type, payload, created_at
                   FROM event_mailbox
                   WHERE account_id = $1
                   ORDER BY created_at ASC
                   LIMIT 50""",
                account_id,
            )

    events = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (json.JSONDecodeError, TypeError):
            payload = {}
        events.append({
            "event_id": row["event_id"],
            "agent_id": row["agent_id"],
            "event_type": row["event_type"],
            "data": payload,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })

    return {"events": events, "pending": len(events)}
