"""Twilio API wrapper for SMS service."""

import logging
from typing import Any

from twilio.rest import Client
from config import get_settings

logger = logging.getLogger(__name__)


class TwilioService:
    """Service for interacting with Twilio API."""

    def __init__(self):
        """Initialize Twilio client."""
        settings = get_settings()
        self.project_name = settings.project_name
        self.phone_number = settings.twilio_phone_number
        self.client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    def send_message(self, to: str, body: str, media_urls: list[str] | None = None) -> dict[str, Any]:
        """Send an SMS message via Twilio.

        Args:
            to: Recipient phone number (E.164 format)
            body: Message body
            media_urls: Optional list of media URLs (for MMS)

        Returns:
            Dictionary with sid, status, and segments
        """
        try:
            message_params = {
                "from_": self.phone_number,
                "to": to,
                "body": body,
            }

            if media_urls:
                message_params["media_url"] = media_urls

            message = self.client.messages.create(**message_params)

            logger.info(f"Sent SMS to {to}: {message.sid}")

            return {
                "sid": message.sid,
                "status": message.status,
                "segments": message.num_segments or 1,
            }

        except Exception as e:
            logger.error(f"Failed to send SMS to {to}: {e}")
            raise

    def validate_phone_number(self, phone: str) -> dict[str, Any]:
        """Validate a phone number using Twilio Lookup API.

        Args:
            phone: Phone number to validate

        Returns:
            Dictionary with validation results
        """
        try:
            lookup = self.client.lookups.v1.phone_numbers(phone).fetch()
            return {
                "valid": True,
                "formatted": lookup.phone_number,
                "country_code": lookup.country_code,
            }
        except Exception as e:
            logger.warning(f"Phone number validation failed for {phone}: {e}")
            return {
                "valid": False,
                "error": str(e),
            }
