"""Configuration for the secrets portal."""

import os
from pathlib import Path


class Settings:
    """Portal configuration settings."""

    # Paths
    MASTER_KEY_PATH: Path = Path(os.getenv("MASTER_KEY_PATH", "/etc/hostkit/master.key"))
    SECRETS_DIR: Path = Path(os.getenv("SECRETS_DIR", "/var/lib/hostkit/secrets"))
    HOSTKIT_DB_PATH: Path = Path(os.getenv("HOSTKIT_DB_PATH", "/var/lib/hostkit/hostkit.db"))

    # Server
    HOST: str = os.getenv("PORTAL_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORTAL_PORT", "8900"))

    # Rate limiting
    RATE_LIMIT: str = os.getenv("RATE_LIMIT", "10/minute")  # 10 requests per minute per IP

    # CORS (for potential frontend separation)
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else []

    # Debug mode
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"


settings = Settings()
