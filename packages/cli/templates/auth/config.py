"""Configuration settings for HostKit Auth Service.

Loads settings from environment variables with validation.
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class AuthSettings(BaseSettings):
    """Auth service configuration."""

    # Required settings
    auth_db_url: str

    # JWT Keys - paths to PEM files
    jwt_private_key_path: str = ""
    jwt_public_key_path: str = ""

    # Server configuration
    auth_service_port: int = 9001
    project_name: str = ""
    base_url: str = ""  # e.g., https://myproject.hostkit.dev

    # Token expiration
    access_token_expire_minutes: int = 60  # 1 hour
    refresh_token_expire_days: int = 30
    magic_link_expire_minutes: int = 15

    # CORS
    auth_cors_origins: str = ""  # Comma-separated list

    # OAuth configuration (optional)
    google_client_id: str = ""
    google_web_client_id: str = ""
    google_client_secret: str = ""
    apple_client_id: str = ""
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key_path: str = ""
    apple_private_key: str = ""  # Can also be set directly
    apple_bundle_id: str = ""  # iOS Bundle ID for native apps

    # Central OAuth proxy
    oauth_proxy_url: str = "https://auth.hostkit.dev"

    # Feature flags
    email_enabled: bool = True
    magic_link_enabled: bool = True
    anonymous_enabled: bool = True

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        extra = "ignore"
        populate_by_name = True

    @property
    def jwt_private_key(self) -> str:
        """Load JWT private key from file."""
        if self.jwt_private_key_path:
            path = Path(self.jwt_private_key_path)
            if path.exists():
                return path.read_text()
        return ""

    @property
    def jwt_public_key(self) -> str:
        """Load JWT public key from file."""
        if self.jwt_public_key_path:
            path = Path(self.jwt_public_key_path)
            if path.exists():
                return path.read_text()
        return ""

    @property
    def google_enabled(self) -> bool:
        """Check if Google OAuth is configured."""
        return bool(self.google_client_id or self.google_web_client_id)

    @property
    def google_web_client(self) -> str:
        """Get the Google web client ID (for OAuth flows)."""
        return self.google_web_client_id or self.google_client_id

    @property
    def google_callback_url(self) -> str:
        """Get the Google OAuth callback URL."""
        return f"{self.base_url}/auth/oauth/google/callback"

    @property
    def apple_enabled(self) -> bool:
        """Check if Apple Sign-In is configured."""
        return bool(self.apple_client_id and self.apple_team_id and self.apple_key_id)

    @property
    def apple_private_key_content(self) -> str:
        """Get Apple private key content (from env or file)."""
        if self.apple_private_key:
            return self.apple_private_key
        if self.apple_private_key_path:
            path = Path(self.apple_private_key_path)
            if path.exists():
                return path.read_text()
        return ""


# Alias for backwards compatibility
Settings = AuthSettings


@lru_cache
def get_settings() -> AuthSettings:
    """Get cached settings instance."""
    return AuthSettings()
