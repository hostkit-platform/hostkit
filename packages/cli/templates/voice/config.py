"""Voice service configuration."""
import os
import configparser
from pathlib import Path
from pydantic_settings import BaseSettings


def load_twilio_config():
    """Load Twilio credentials from /etc/hostkit/twilio.ini."""
    config_path = Path("/etc/hostkit/twilio.ini")
    if config_path.exists():
        parser = configparser.ConfigParser()
        parser.read(config_path)
        if "twilio" in parser:
            return dict(parser["twilio"])
    return {}


class VoiceConfig(BaseSettings):
    """Voice service configuration from environment."""

    # API Keys
    assemblyai_api_key: str = ""
    cartesia_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Twilio (loaded from twilio.ini)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # Service
    port: int = 8900
    host: str = "0.0.0.0"

    # Paths
    hostkit_db: str = "/var/lib/hostkit/hostkit.db"
    projects_base: str = "/home"

    # Audio
    sample_rate: int = 8000  # Twilio μ-law
    chunk_size: int = 160  # 20ms at 8kHz

    class Config:
        env_file = "/etc/hostkit/voice.ini"
        env_file_encoding = "utf-8"


# Load config and merge Twilio settings
_twilio = load_twilio_config()
config = VoiceConfig(
    twilio_account_sid=_twilio.get("account_sid", ""),
    twilio_auth_token=_twilio.get("auth_token", ""),
    twilio_phone_number=_twilio.get("phone_number", "")
)
