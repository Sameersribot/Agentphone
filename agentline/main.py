"""
AgentLine — FastAPI Application Entry Point
Mounts all routers and manages startup/shutdown lifecycle.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentline.database import init_db, close_db
from agentline.redis_client import init_redis, close_redis
from agentline.routers import auth, agents, numbers, messages, calls, webhooks, usage, plivo_events, signalwire_events

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agentline")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of database and Redis connections."""
    logger.info("Starting AgentLine...")
    await init_db()
    await init_redis()
    logger.info("AgentLine ready.")
    yield
    logger.info("Shutting down AgentLine...")
    await close_redis()
    await close_db()
    logger.info("AgentLine stopped.")


app = FastAPI(
    title="AgentLine",
    description="AI-native telephony platform — give your agent a phone number, voice, and SMS.",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(numbers.router)
app.include_router(messages.router)
app.include_router(calls.router)
app.include_router(webhooks.router)
app.include_router(usage.router)
app.include_router(plivo_events.router)
app.include_router(signalwire_events.router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "AgentLine",
        "version": "0.2.0",
        "status": "operational",
        "telephony_providers": ["plivo", "signalwire"],
    }


@app.get("/health", tags=["Health"])
async def health():
    status = "healthy"
    try:
        from agentline.database import get_db_conn
        async with get_db_conn() as db:
            await db.fetchval("SELECT 1")
    except Exception as e:
        status = f"unhealthy: {e}"
    
    return {"status": status}


@app.get("/debug/urls", tags=["Health"])
async def debug_urls():
    """Show the callback URLs that providers will receive — useful for debugging."""
    from agentline.config import settings
    return {
        "base_url_raw": settings.BASE_URL,
        "base_url_clean": settings.base_url_clean,
        "plivo": {
            "answer_url": f"{settings.base_url_clean}/plivo/answer/call_TEST",
            "record_url": f"{settings.base_url_clean}/plivo/recorded/call_TEST",
            "wait_url": f"{settings.base_url_clean}/plivo/wait/call_TEST",
            "hangup_url": f"{settings.base_url_clean}/plivo/hangup/call_TEST",
            "inbound_url": f"{settings.base_url_clean}/plivo/inbound",
            "sms_url": f"{settings.base_url_clean}/plivo/sms",
        },
        "signalwire": {
            "answer_url": f"{settings.base_url_clean}/signalwire/answer/call_TEST",
            "record_url": f"{settings.base_url_clean}/signalwire/recorded/call_TEST",
            "wait_url": f"{settings.base_url_clean}/signalwire/wait/call_TEST",
            "hangup_url": f"{settings.base_url_clean}/signalwire/hangup/call_TEST",
            "inbound_url": f"{settings.base_url_clean}/signalwire/inbound",
            "sms_url": f"{settings.base_url_clean}/signalwire/sms",
        },
    }
