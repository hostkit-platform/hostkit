"""Configuration settings for HostKit Booking Service.

Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Booking service configuration."""

    # Database
    database_url: str

    # Server
    port: int = 12000
    host: str = "127.0.0.1"

    # Logging
    log_level: str = "INFO"

    # Project context (set by HostKit)
    project_name: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"
        populate_by_name = True


settings = Settings()
