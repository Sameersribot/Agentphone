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
from agentline.routers import auth, agents, numbers, messages, calls, usage, events, signalwire_events, billing_api

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

    # Reconfigure existing numbers with correct StatusCallback for billing
    try:
        await _reconfigure_number_callbacks()
    except Exception as e:
        logger.warning("Non-fatal: failed to reconfigure number callbacks on startup: %s", e)

    logger.info("AgentLine ready.")
    yield
    logger.info("Shutting down AgentLine...")
    await close_redis()
    await close_db()
    logger.info("AgentLine stopped.")


async def _reconfigure_number_callbacks():
    """
    Ensure all active SignalWire numbers have the correct StatusCallback URL
    so inbound call hangups are properly received and billed.
    Runs once on startup — safe to call repeatedly (idempotent).
    """
    import httpx
    from agentline.config import settings
    from agentline.database import get_db_conn

    if not all([settings.SIGNALWIRE_PROJECT_ID, settings.SIGNALWIRE_TOKEN, settings.SIGNALWIRE_SPACE_URL]):
        logger.info("Skipping number callback reconfiguration — SignalWire not configured.")
        return

    sw_base = f"https://{settings.SIGNALWIRE_SPACE_URL}/api/laml/2010-04-01/Accounts/{settings.SIGNALWIRE_PROJECT_ID}"
    auth = (settings.SIGNALWIRE_PROJECT_ID, settings.SIGNALWIRE_TOKEN)
    base = settings.base_url_clean

    async with get_db_conn() as db:
        rows = await db.fetch(
            "SELECT id, phone_number, provider_id FROM phone_numbers WHERE status = 'active'"
        )

    if not rows:
        return

    logger.info("Reconfiguring StatusCallback on %d active number(s)...", len(rows))
    async with httpx.AsyncClient(timeout=10.0) as client:
        for r in rows:
            try:
                await client.post(
                    f"{sw_base}/IncomingPhoneNumbers/{r['provider_id']}.json",
                    auth=auth,
                    data={
                        "VoiceUrl": f"{base}/signalwire/inbound",
                        "VoiceMethod": "POST",
                        "SmsUrl": f"{base}/signalwire/sms",
                        "SmsMethod": "POST",
                        "StatusCallback": f"{base}/signalwire/inbound_hangup",
                        "StatusCallbackMethod": "POST",
                    },
                )
                logger.info("  ✓ %s — StatusCallback updated", r["phone_number"])
            except Exception as e:
                logger.warning("  ✗ %s — failed: %s", r["phone_number"], e)


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
app.include_router(usage.router)
app.include_router(events.router)
app.include_router(signalwire_events.router)
app.include_router(billing_api.router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "AgentLine",
        "version": "0.2.0",
        "status": "operational",
        "telephony_providers": ["signalwire"],
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
    base = settings.base_url_clean
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    return {
        "base_url_raw": settings.BASE_URL,
        "base_url_clean": base,
        "signalwire": {
            "answer_url": f"{base}/signalwire/answer/call_TEST",
            "stream_ws_url": f"{ws_base}/signalwire/stream/call_TEST",
            "hangup_url": f"{base}/signalwire/hangup/call_TEST",
            "inbound_url": f"{base}/signalwire/inbound",
            "inbound_hangup_url": f"{base}/signalwire/inbound_hangup",
            "sms_url": f"{base}/signalwire/sms",
        },
        "voice_pipeline": {
            "stt": "Deepgram Nova-2 ($0.006/min)",
            "tts": "Cartesia Sonic ($0.002/min)",
            "llm": "GPT-4o-mini / GPT-4o",
        },
    }

