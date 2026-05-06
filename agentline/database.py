"""
AgentLine — Database
Async PostgreSQL connection pool using asyncpg directly.
We use raw asyncpg for maximum performance with direct SQL queries.
"""

import asyncpg
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from agentline.config import settings

logger = logging.getLogger(__name__)

# Module-level pool reference
_pool: asyncpg.Pool | None = None


async def init_db():
    """Initialize the connection pool. Call once at app startup."""
    global _pool
    dsn = settings.db_dsn
    try:
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        # Test the connection
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        logger.info("Database connected successfully")
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        logger.warning("Server starting WITHOUT database — fix DATABASE_URL in .env")
        _pool = None


async def close_db():
    """Close the connection pool. Call at app shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_db() -> asyncpg.Connection:
    """
    FastAPI dependency that yields a connection from the pool.
    Usage: db = Depends(get_db)
    """
    if _pool is None:
        raise RuntimeError("Database not available. Check DATABASE_URL in .env")
    async with _pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def get_db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Context manager for getting a DB connection outside of FastAPI routes.
    Usage: async with get_db_conn() as db: ...
    """
    if _pool is None:
        raise RuntimeError("Database not available. Check DATABASE_URL in .env")
    async with _pool.acquire() as conn:
        yield conn
