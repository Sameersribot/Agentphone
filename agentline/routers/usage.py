"""
AgentLine — Usage Router
Usage stats and billing metrics.
"""

from fastapi import APIRouter, Depends

from agentline.auth_middleware import get_current_account
from agentline.database import get_db

router = APIRouter(prefix="/v1/usage", tags=["Usage"])


@router.get("")
async def get_usage(
    period: str = "current_month",
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """Get usage statistics for the current billing period."""
    # Count calls
    call_stats = await db.fetchrow(
        """SELECT
            COUNT(*) AS total_calls,
            COUNT(*) FILTER (WHERE direction = 'outbound') AS outbound_calls,
            COUNT(*) FILTER (WHERE direction = 'inbound') AS inbound_calls,
            COALESCE(SUM(duration_seconds), 0) AS total_call_seconds
           FROM calls
           WHERE account_id = $1
             AND started_at >= date_trunc('month', now())""",
        account["id"],
    )

    # Count messages
    msg_stats = await db.fetchrow(
        """SELECT
            COUNT(*) AS total_messages,
            COUNT(*) FILTER (WHERE direction = 'outbound') AS outbound_messages,
            COUNT(*) FILTER (WHERE direction = 'inbound') AS inbound_messages
           FROM messages
           WHERE account_id = $1
             AND created_at >= date_trunc('month', now())""",
        account["id"],
    )

    # Count active numbers
    number_count = await db.fetchval(
        "SELECT COUNT(*) FROM phone_numbers WHERE account_id=$1 AND status='active'",
        account["id"],
    )

    return {
        "period": period,
        "calls": dict(call_stats) if call_stats else {},
        "messages": dict(msg_stats) if msg_stats else {},
        "active_numbers": number_count or 0,
    }
