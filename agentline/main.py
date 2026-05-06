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
from agentline.routers import auth, agents, numbers, messages, calls, webhooks, usage, telnyx_events

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
    version="0.1.0",
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
app.include_router(telnyx_events.router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "AgentLine",
        "version": "0.1.0",
        "status": "operational",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}
