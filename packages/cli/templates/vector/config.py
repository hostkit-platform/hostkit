"""Configuration management using pydantic-settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file="/etc/hostkit/vector.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service
    HOST: str = "127.0.0.1"
    PORT: int = 8901
    DEBUG: bool = False
    LOG_LEVEL: str = "info"

    # Database
    SERVICE_DATABASE_URL: str = "postgresql+asyncpg://hostkit_vector:password@localhost/hostkit_vector"
    PROJECT_DATABASE_TEMPLATE: str = "postgresql+asyncpg://hostkit_vector:{password}@localhost/{project}_vector"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/1"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536

    # Security
    SECRET_KEY: str = "change-me-in-production"
    API_KEY_PREFIX: str = "vk"

    # Limits
    MAX_SYNC_TEXT_TOKENS: int = 10000
    MAX_CHUNKS_PER_DOCUMENT: int = 1000
    MAX_COLLECTIONS_PER_PROJECT: int = 100
    MAX_FILE_SIZE_MB: int = 50

    # Chunking
    CHUNK_TARGET_TOKENS: int = 500
    CHUNK_OVERLAP_TOKENS: int = 50
    CHUNK_MIN_TOKENS: int = 100


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
