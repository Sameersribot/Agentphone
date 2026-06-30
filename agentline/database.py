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

        # Auto-create core tables (billing, accounts, etc.)
        async with _pool.acquire() as conn:
            # Auto-create billing infrastructure
            await conn.execute("""
                ALTER TABLE accounts
                    ADD COLUMN IF NOT EXISTS balance NUMERIC(12,4) NOT NULL DEFAULT 10.0000
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS billing_ledger (
                    id              SERIAL PRIMARY KEY,
                    account_id      TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    amount          NUMERIC(12,4) NOT NULL,
                    balance_after   NUMERIC(12,4) NOT NULL,
                    txn_type        TEXT NOT NULL,
                    reference_id    TEXT,
                    description     TEXT,
                    created_at      TIMESTAMPTZ DEFAULT now()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_billing_ledger_account
                    ON billing_ledger(account_id, created_at DESC)
            """)
            logger.info("billing tables verified")

            # Auto-add initial_greeting column to calls (per-call greeting override)
            await conn.execute("""
                ALTER TABLE calls
                    ADD COLUMN IF NOT EXISTS initial_greeting TEXT
            """)
            logger.info("calls.initial_greeting column verified")

            # Auto-create feedback table (agent-submitted feedback, bugs, feature requests)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                    account_id      TEXT REFERENCES accounts(id) ON DELETE CASCADE,
                    agent_id        TEXT REFERENCES agents(id) ON DELETE SET NULL,
                    category        TEXT NOT NULL,
                    severity        TEXT NOT NULL DEFAULT 'normal',
                    subject         TEXT,
                    message         TEXT NOT NULL,
                    contact_email   TEXT,
                    status          TEXT NOT NULL DEFAULT 'open',
                    created_at      TIMESTAMPTZ DEFAULT now(),
                    updated_at      TIMESTAMPTZ DEFAULT now()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_account
                    ON feedback(account_id, created_at DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_status
                    ON feedback(status, created_at DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_category
                    ON feedback(category, created_at DESC)
            """)
            logger.info("feedback table verified")

            # One webhook per agent (no account-wide). Wrapped so a pre-existing
            # duplicate row can never block app startup — the app layer enforces
            # one-webhook-per-agent regardless; this index is defense-in-depth.
            try:
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_webhooks_one_per_agent
                        ON webhooks(account_id, agent_id)
                """)
                logger.info("webhooks per-agent index verified")
            except Exception as ix_err:
                logger.warning("Non-fatal: could not create webhooks unique index: %s", ix_err)
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
