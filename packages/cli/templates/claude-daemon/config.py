"""Configuration management using pydantic-settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file="/etc/hostkit/claude.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service
    HOST: str = "127.0.0.1"
    PORT: int = 9000
    DEBUG: bool = False
    LOG_LEVEL: str = "info"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://hostkit_claude:password@localhost/hostkit_claude"

    # Anthropic API
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_MAX_TOKENS: int = 4096

    # Security
    SECRET_KEY: str = "change-me-in-production"
    API_KEY_PREFIX: str = "ck"

    # Rate Limiting (defaults)
    DEFAULT_RATE_LIMIT_RPM: int = 60  # requests per minute
    DEFAULT_DAILY_TOKEN_LIMIT: int = 1_000_000

    # Tool Execution
    TOOL_TIMEOUT_SECONDS: int = 30
    TOOL_MAX_OUTPUT_SIZE: int = 100_000  # bytes

    # Conversation Limits
    MAX_MESSAGES_PER_CONVERSATION: int = 1000
    MAX_CONVERSATIONS_PER_PROJECT: int = 1000


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
