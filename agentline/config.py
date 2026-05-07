"""
AgentLine — Configuration
Loads environment variables with validation via pydantic-settings.
Database URL is derived from SUPABASE_URL automatically.
"""

from pydantic_settings import BaseSettings
from pydantic import computed_field
from functools import lru_cache


class Settings(BaseSettings):
    # Supabase (Auth + Database)
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Plivo
    PLIVO_AUTH_ID: str = ""
    PLIVO_AUTH_TOKEN: str = ""
    PLIVO_APP_ID: str = ""  # Plivo Application ID for voice

    # Voice Pipeline
    DEEPGRAM_API_KEY: str = ""
    CARTESIA_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # App
    SECRET_KEY: str = "change-me-in-production"
    BASE_URL: str = "http://localhost:8000"
    WEBHOOK_SECRET_SALT: str = "change-me-in-production"

    # Database — override if needed, otherwise derived from Supabase URL
    DATABASE_URL: str = ""

    @property
    def db_dsn(self) -> str:
        """Returns asyncpg-compatible DSN from DATABASE_URL."""
        if self.DATABASE_URL:
            # asyncpg requires postgresql:// instead of postgres://
            url = self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
            url = url.replace("postgres://", "postgresql://")
            return url
        return "postgresql://agentline:secret@localhost:5432/agentline"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
