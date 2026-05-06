"""
AgentLine — Redis Client
Async Redis connection for caching, rate limiting, and pub/sub.
"""

import redis.asyncio as redis
from agentline.config import settings

# Module-level Redis client
_redis: redis.Redis | None = None


async def init_redis():
    """Initialize Redis connection. Call once at app startup."""
    global _redis
    _redis = redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )


async def close_redis():
    """Close Redis connection. Call at app shutdown."""
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


def get_redis() -> redis.Redis:
    """Get the Redis client instance."""
    if _redis is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis
