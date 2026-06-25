"""
AgentLine — Feedback Router
Lets AI agents send feedback, report bugs, request features,
or flag difficulties they encounter while using the platform.
"""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from agentline.auth_middleware import get_current_account
from agentline.database import get_db
from agentline.models.feedback import FeedbackCreate, FeedbackOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feedback", tags=["Feedback"])


@router.post("", response_model=FeedbackOut, operation_id="submit_feedback")
async def submit_feedback(
    body: FeedbackCreate,
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    Submit feedback, report a bug, or request a feature.

    Use this endpoint to tell the AgentLine team about anything you
    encounter while using the platform:

      - **bug** — something is broken or not working as expected
      - **feature_request** — you wish a capability existed
      - **difficulty** — something was hard or confusing to use
      - **feedback** — general comments, suggestions, or praise

    Your feedback is reviewed by the team. Include as much detail as
    possible — for bugs, describe what you expected vs. what happened
    and any steps to reproduce it. You can check back on submitted
    feedback via GET /v1/feedback to see its status.
    """
    feedback_id = f"fb_{secrets.token_urlsafe(12)}"
    now = datetime.now(timezone.utc)

    # If tied to an agent, make sure it belongs to this account
    if body.agent_id:
        agent = await db.fetchrow(
            "SELECT id FROM agents WHERE id = $1 AND account_id = $2",
            body.agent_id,
            account["id"],
        )
        if not agent:
            raise HTTPException(404, "Agent not found.")

    await db.execute(
        """INSERT INTO feedback
           (id, account_id, agent_id, category, severity,
            subject, message, contact_email, status, created_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10)""",
        feedback_id,
        account["id"],
        body.agent_id,
        body.category.value,
        body.severity.value,
        body.subject,
        body.message,
        body.contact_email,
        "open",
        now,
    )

    logger.info(
        "Feedback %s submitted by account %s — category=%s severity=%s",
        feedback_id, account["id"][:12], body.category.value, body.severity.value,
    )

    return FeedbackOut(
        id=feedback_id,
        account_id=account["id"],
        category=body.category.value,
        message=body.message,
        subject=body.subject,
        severity=body.severity.value,
        agent_id=body.agent_id,
        contact_email=body.contact_email,
        status="open",
        created_at=now,
    )


@router.get("", operation_id="list_feedback")
async def list_feedback(
    category: str | None = Query(None, description="Filter by feedback type: 'bug', 'feature_request', 'difficulty', or 'feedback'"),
    status: str | None = Query(None, description="Filter by status: 'open', 'acknowledged', 'in_progress', 'resolved', 'closed'"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of feedback entries to return (1-200)"),
    offset: int = Query(0, ge=0, description="Number of entries to skip for pagination"),
    account=Depends(get_current_account),
    db=Depends(get_db),
):
    """
    List feedback you have submitted.

    Returns your feedback history with optional filters by category
    (bug, feature_request, difficulty, feedback) and status (open,
    acknowledged, in_progress, resolved, closed). Useful for tracking
    progress on bugs or feature requests you reported.
    """
    conditions = ["account_id = $1"]
    params: list = [account["id"]]
    idx = 2

    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    where = " AND ".join(conditions)
    params.extend([limit, offset])

    rows = await db.fetch(
        f"""SELECT id, category, severity, subject, message,
                  agent_id, contact_email, status, created_at, updated_at
           FROM feedback
           WHERE {where}
           ORDER BY created_at DESC
           LIMIT ${idx} OFFSET ${idx + 1}""",
        *params,
    )
    return {"feedback": [dict(r) for r in rows], "count": len(rows)}
