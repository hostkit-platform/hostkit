"""Alert notification service for HostKit.

Provides event-driven alerting via webhooks, email, and Slack
for deployment, migration, and health check events.
"""

import hashlib
import hmac
import json
import os
import smtplib
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

from hostkit import __version__
from hostkit.config import get_config
from hostkit.database import get_db


@dataclass
class WebhookConfig:
    """Configuration for a webhook channel."""

    url: str
    secret: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class EmailConfig:
    """Configuration for an email channel."""

    to: list[str]
    from_address: str = "alerts@hostkit.dev"
    subject_prefix: str = "[HostKit]"


@dataclass
class SlackConfig:
    """Configuration for a Slack channel."""

    webhook_url: str


# Type alias for channel configs
ChannelConfig = WebhookConfig | EmailConfig | SlackConfig


@dataclass
class AlertChannel:
    """Information about an alert channel."""

    id: int
    project_name: str
    name: str
    channel_type: str
    config: ChannelConfig
    enabled: bool
    muted_until: str | None
    created_at: str
    updated_at: str

    @property
    def is_muted(self) -> bool:
        """Check if channel is currently muted."""
        if not self.muted_until:
            return False
        mute_time = datetime.fromisoformat(self.muted_until.replace("Z", "+00:00"))
        return datetime.now(mute_time.tzinfo) < mute_time if mute_time.tzinfo else datetime.utcnow() < mute_time


@dataclass
class AlertEvent:
    """An alert event to be sent."""

    event_type: str
    event_status: str
    project: str
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class AlertServiceError(Exception):
    """Base exception for alert service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Maximum channels per project
MAX_CHANNELS_PER_PROJECT = 10


class AlertService:
    """Service for managing alert channels and sending notifications."""

    # Supported channel types
    CHANNEL_TYPES = ("webhook", "email", "slack")

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self._vps_ip = self.config.vps_ip

    def _parse_channel_config(self, config_json: str, channel_type: str) -> ChannelConfig:
        """Parse channel config from JSON based on channel type."""
        data = json.loads(config_json)

        if channel_type == "webhook":
            return WebhookConfig(
                url=data.get("url", ""),
                secret=data.get("secret"),
                headers=data.get("headers", {}),
            )
        elif channel_type == "email":
            return EmailConfig(
                to=data.get("to", []),
                from_address=data.get("from_address", "alerts@hostkit.dev"),
                subject_prefix=data.get("subject_prefix", "[HostKit]"),
            )
        elif channel_type == "slack":
            return SlackConfig(
                webhook_url=data.get("webhook_url", ""),
            )
        else:
            # Fallback for unknown types
            return WebhookConfig(url=data.get("url", ""))

    def _serialize_channel_config(self, config: ChannelConfig) -> str:
        """Serialize channel config to JSON."""
        if isinstance(config, WebhookConfig):
            data: dict[str, Any] = {"url": config.url}
            if config.secret:
                data["secret"] = config.secret
            if config.headers:
                data["headers"] = config.headers
        elif isinstance(config, EmailConfig):
            data = {
                "to": config.to,
                "from_address": config.from_address,
                "subject_prefix": config.subject_prefix,
            }
        elif isinstance(config, SlackConfig):
            data = {"webhook_url": config.webhook_url}
        else:
            data = {}
        return json.dumps(data)

    def _validate_project(self, project_name: str) -> None:
        """Validate project exists."""
        project = self.db.get_project(project_name)
        if not project:
            raise AlertServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

    def _validate_url(self, url: str) -> None:
        """Validate webhook URL."""
        if not url.startswith(("http://", "https://")):
            raise AlertServiceError(
                code="INVALID_URL",
                message="Webhook URL must start with http:// or https://",
                suggestion="Provide a valid HTTP(S) URL",
            )

    def _validate_email(self, email: str) -> None:
        """Validate email address format."""
        if "@" not in email or "." not in email.split("@")[-1]:
            raise AlertServiceError(
                code="INVALID_EMAIL",
                message=f"Invalid email address: {email}",
                suggestion="Provide a valid email address",
            )

    def add_channel(
        self,
        project_name: str,
        name: str,
        channel_type: str,
        # Webhook params
        url: str | None = None,
        secret: str | None = None,
        headers: dict[str, str] | None = None,
        # Email params
        to_emails: list[str] | None = None,
        from_address: str | None = None,
        subject_prefix: str | None = None,
        # Slack params
        webhook_url: str | None = None,
    ) -> AlertChannel:
        """Add a new alert channel.

        Args:
            project_name: Name of the project
            name: Unique name for the channel
            channel_type: Type of channel (webhook, email, slack)
            url: Webhook URL (for webhook type)
            secret: Optional HMAC secret for signing (for webhook type)
            headers: Optional custom headers (for webhook type)
            to_emails: List of email recipients (for email type)
            from_address: Sender email address (for email type)
            subject_prefix: Subject prefix (for email type)
            webhook_url: Slack webhook URL (for slack type)

        Returns:
            AlertChannel with channel details
        """
        self._validate_project(project_name)

        # Check channel limit
        count = self.db.count_alert_channels(project_name)
        if count >= MAX_CHANNELS_PER_PROJECT:
            raise AlertServiceError(
                code="CHANNEL_LIMIT_REACHED",
                message=f"Maximum of {MAX_CHANNELS_PER_PROJECT} channels per project",
                suggestion="Remove unused channels before adding new ones",
            )

        # Check if channel already exists
        existing = self.db.get_alert_channel(project_name, name)
        if existing:
            raise AlertServiceError(
                code="CHANNEL_EXISTS",
                message=f"Channel '{name}' already exists for project '{project_name}'",
                suggestion="Use a different name or remove the existing channel",
            )

        # Validate channel type
        if channel_type not in self.CHANNEL_TYPES:
            raise AlertServiceError(
                code="INVALID_CHANNEL_TYPE",
                message=f"Invalid channel type: {channel_type}",
                suggestion=f"Supported types: {', '.join(self.CHANNEL_TYPES)}",
            )

        # Create config based on channel type
        config: ChannelConfig
        if channel_type == "webhook":
            if not url:
                raise AlertServiceError(
                    code="MISSING_URL",
                    message="Webhook URL is required",
                    suggestion="Provide --url parameter",
                )
            self._validate_url(url)
            config = WebhookConfig(
                url=url,
                secret=secret,
                headers=headers or {},
            )
        elif channel_type == "email":
            if not to_emails or len(to_emails) == 0:
                raise AlertServiceError(
                    code="MISSING_RECIPIENTS",
                    message="At least one email recipient is required",
                    suggestion="Provide --to parameter",
                )
            for email in to_emails:
                self._validate_email(email)
            config = EmailConfig(
                to=to_emails,
                from_address=from_address or "alerts@hostkit.dev",
                subject_prefix=subject_prefix or "[HostKit]",
            )
        elif channel_type == "slack":
            if not webhook_url:
                raise AlertServiceError(
                    code="MISSING_WEBHOOK_URL",
                    message="Slack webhook URL is required",
                    suggestion="Provide --webhook-url parameter",
                )
            self._validate_url(webhook_url)
            config = SlackConfig(webhook_url=webhook_url)
        else:
            raise AlertServiceError(
                code="INVALID_CHANNEL_TYPE",
                message=f"Invalid channel type: {channel_type}",
            )

        # Create channel
        channel_record = self.db.create_alert_channel(
            project_name=project_name,
            name=name,
            channel_type=channel_type,
            config=self._serialize_channel_config(config),
        )

        return AlertChannel(
            id=channel_record["id"],
            project_name=channel_record["project_name"],
            name=channel_record["name"],
            channel_type=channel_record["channel_type"],
            config=config,
            enabled=bool(channel_record["enabled"]),
            muted_until=channel_record.get("muted_until"),
            created_at=channel_record["created_at"],
            updated_at=channel_record["updated_at"],
        )

    def get_channel(self, project_name: str, name: str) -> AlertChannel:
        """Get a channel by project and name."""
        channel_record = self.db.get_alert_channel(project_name, name)
        if not channel_record:
            raise AlertServiceError(
                code="CHANNEL_NOT_FOUND",
                message=f"Channel '{name}' not found for project '{project_name}'",
                suggestion="Run 'hostkit alert channel list' to see available channels",
            )

        return AlertChannel(
            id=channel_record["id"],
            project_name=channel_record["project_name"],
            name=channel_record["name"],
            channel_type=channel_record["channel_type"],
            config=self._parse_channel_config(channel_record["config"], channel_record["channel_type"]),
            enabled=bool(channel_record["enabled"]),
            muted_until=channel_record.get("muted_until"),
            created_at=channel_record["created_at"],
            updated_at=channel_record["updated_at"],
        )

    def list_channels(self, project_name: str) -> list[AlertChannel]:
        """List all channels for a project."""
        self._validate_project(project_name)

        channels = self.db.list_alert_channels(project_name=project_name)
        return [
            AlertChannel(
                id=ch["id"],
                project_name=ch["project_name"],
                name=ch["name"],
                channel_type=ch["channel_type"],
                config=self._parse_channel_config(ch["config"], ch["channel_type"]),
                enabled=bool(ch["enabled"]),
                muted_until=ch.get("muted_until"),
                created_at=ch["created_at"],
                updated_at=ch["updated_at"],
            )
            for ch in channels
        ]

    def remove_channel(self, project_name: str, name: str) -> dict[str, Any]:
        """Remove a channel."""
        # Verify channel exists
        channel = self.get_channel(project_name, name)

        # Delete
        self.db.delete_alert_channel(project_name, name)

        return {
            "project": project_name,
            "channel": name,
            "deleted_at": datetime.utcnow().isoformat(),
        }

    def enable_channel(self, project_name: str, name: str) -> AlertChannel:
        """Enable a channel."""
        channel = self.get_channel(project_name, name)
        self.db.update_alert_channel(project_name, name, enabled=True)
        channel.enabled = True
        return channel

    def disable_channel(self, project_name: str, name: str) -> AlertChannel:
        """Disable a channel."""
        channel = self.get_channel(project_name, name)
        self.db.update_alert_channel(project_name, name, enabled=False)
        channel.enabled = False
        return channel

    def mute_channel(
        self,
        project_name: str,
        name: str,
        duration_minutes: int = 60,
    ) -> AlertChannel:
        """Mute a channel for a specified duration.

        Args:
            project_name: Project name
            name: Channel name
            duration_minutes: Duration to mute in minutes (default: 60)

        Returns:
            Updated AlertChannel
        """
        channel = self.get_channel(project_name, name)
        mute_until = (datetime.utcnow() + timedelta(minutes=duration_minutes)).isoformat() + "Z"
        self.db.update_alert_channel(project_name, name, muted_until=mute_until)
        channel.muted_until = mute_until
        return channel

    def unmute_channel(self, project_name: str, name: str) -> AlertChannel:
        """Unmute a channel.

        Args:
            project_name: Project name
            name: Channel name

        Returns:
            Updated AlertChannel
        """
        channel = self.get_channel(project_name, name)
        self.db.update_alert_channel(project_name, name, clear_mute=True)
        channel.muted_until = None
        return channel

    def mute_project(self, project_name: str, duration_minutes: int = 60) -> list[AlertChannel]:
        """Mute all channels for a project.

        Args:
            project_name: Project name
            duration_minutes: Duration to mute in minutes (default: 60)

        Returns:
            List of updated AlertChannels
        """
        channels = self.list_channels(project_name)
        mute_until = (datetime.utcnow() + timedelta(minutes=duration_minutes)).isoformat() + "Z"
        for ch in channels:
            self.db.update_alert_channel(project_name, ch.name, muted_until=mute_until)
            ch.muted_until = mute_until
        return channels

    def unmute_project(self, project_name: str) -> list[AlertChannel]:
        """Unmute all channels for a project.

        Args:
            project_name: Project name

        Returns:
            List of updated AlertChannels
        """
        channels = self.list_channels(project_name)
        for ch in channels:
            self.db.update_alert_channel(project_name, ch.name, clear_mute=True)
            ch.muted_until = None
        return channels

    def _sign_payload(self, payload: bytes, secret: str, timestamp: int) -> str:
        """Generate HMAC-SHA256 signature for payload."""
        message = f"{timestamp}.{payload.decode()}"
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={signature}"

    def _send_webhook(
        self,
        url: str,
        payload: dict[str, Any],
        secret: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> tuple[bool, str | None]:
        """Send a webhook notification.

        Returns:
            Tuple of (success, error_message)
        """
        payload_bytes = json.dumps(payload).encode("utf-8")
        timestamp = int(time.time())

        # Build headers
        request_headers = {
            "Content-Type": "application/json",
            "User-Agent": f"HostKit/{__version__}",
            "X-HostKit-Timestamp": str(timestamp),
        }

        # Add signature if secret is configured
        if secret:
            signature = self._sign_payload(payload_bytes, secret, timestamp)
            request_headers["X-HostKit-Signature"] = signature

        # Add custom headers
        if headers:
            request_headers.update(headers)

        try:
            req = urllib.request.Request(
                url,
                data=payload_bytes,
                headers=request_headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if 200 <= response.status < 300:
                    return True, None
                else:
                    return False, f"HTTP {response.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            return False, f"URL error: {e.reason}"
        except TimeoutError:
            return False, "Request timed out"
        except Exception as e:
            return False, str(e)

    def _send_email(
        self,
        config: EmailConfig,
        event: AlertEvent,
    ) -> tuple[bool, str | None]:
        """Send an email notification.

        Returns:
            Tuple of (success, error_message)
        """
        # Build email content
        subject = f"{config.subject_prefix} {event.event_type.title()} {event.event_status}: {event.project}"

        # Plain text body
        body_lines = [
            f"Project: {event.project}",
            f"Event: {event.event_type}",
            f"Status: {event.event_status.upper()}",
            "",
            "Details:",
        ]

        for key, value in event.data.items():
            body_lines.append(f"  {key}: {value}")

        body_lines.extend([
            "",
            f"Timestamp: {event.timestamp}",
            "",
            "---",
            "Sent by HostKit Alert Service",
        ])

        body = "\n".join(body_lines)

        # Create message
        msg = MIMEMultipart()
        msg["From"] = config.from_address
        msg["To"] = ", ".join(config.to)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        try:
            # Send via local Postfix (no auth needed for local delivery)
            with smtplib.SMTP("localhost", 25) as smtp:
                smtp.send_message(msg)
            return True, None
        except Exception as e:
            return False, f"Email send failed: {e}"

    def _send_slack(
        self,
        config: SlackConfig,
        event: AlertEvent,
    ) -> tuple[bool, str | None]:
        """Send a Slack notification using Block Kit.

        Returns:
            Tuple of (success, error_message)
        """
        # Color-code by status
        color = "#36a64f" if event.event_status == "success" else "#dc3545"
        status_emoji = ":white_check_mark:" if event.event_status == "success" else ":x:"

        # Build Block Kit payload
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{event.event_type.title()}: {event.project}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Status:*\n{status_emoji} {event.event_status.title()}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Project:*\n{event.project}",
                    },
                ],
            },
        ]

        # Add event data as fields
        if event.data:
            data_fields = []
            for key, value in event.data.items():
                data_fields.append(f"*{key}:* {value}")
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": " | ".join(data_fields[:5]),  # Limit to 5 fields
                },
            })

        # Add context with timestamp
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Sent by HostKit at {event.timestamp}",
                },
            ],
        })

        payload = {
            "blocks": blocks,
            "attachments": [{"color": color}],
        }

        # Send to Slack webhook
        payload_bytes = json.dumps(payload).encode("utf-8")

        try:
            req = urllib.request.Request(
                config.webhook_url,
                data=payload_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if 200 <= response.status < 300:
                    return True, None
                else:
                    return False, f"HTTP {response.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            return False, f"URL error: {e.reason}"
        except TimeoutError:
            return False, "Request timed out"
        except Exception as e:
            return False, str(e)

    def test_channel(self, project_name: str, name: str) -> dict[str, Any]:
        """Send a test notification to a channel.

        Returns:
            Dict with test results
        """
        channel = self.get_channel(project_name, name)

        # Build test event
        event = AlertEvent(
            event_type="test",
            event_status="success",
            project=project_name,
            data={"message": "This is a test notification from HostKit"},
        )

        # Build payload (for webhook and history)
        payload = self._build_payload(event)

        # Check if muted (but still allow test - just note it)
        muted_note = ""
        if channel.is_muted:
            muted_note = f" (Note: Channel is muted until {channel.muted_until})"

        # Send based on channel type
        if channel.channel_type == "webhook":
            config = channel.config
            if isinstance(config, WebhookConfig):
                success, error = self._send_webhook(
                    url=config.url,
                    payload=payload,
                    secret=config.secret,
                    headers=config.headers,
                )
            else:
                success, error = False, "Invalid webhook config"
        elif channel.channel_type == "email":
            config = channel.config
            if isinstance(config, EmailConfig):
                success, error = self._send_email(config, event)
            else:
                success, error = False, "Invalid email config"
        elif channel.channel_type == "slack":
            config = channel.config
            if isinstance(config, SlackConfig):
                success, error = self._send_slack(config, event)
            else:
                success, error = False, "Invalid slack config"
        else:
            success = False
            error = f"Unsupported channel type: {channel.channel_type}"

        # Record in history
        self.db.create_alert_history(
            project_name=project_name,
            event_type="test",
            event_status="success",
            channel_name=name,
            notification_sent=success,
            notification_error=error,
            payload=json.dumps(payload),
        )

        result = {
            "project": project_name,
            "channel": name,
            "channel_type": channel.channel_type,
            "success": success,
            "error": error,
            "tested_at": datetime.utcnow().isoformat(),
        }

        if muted_note:
            result["note"] = muted_note

        return result

    def _build_payload(self, event: AlertEvent) -> dict[str, Any]:
        """Build webhook payload from event."""
        return {
            "event": {
                "type": event.event_type,
                "status": event.event_status,
                "project": event.project,
                "timestamp": event.timestamp,
            },
            "data": event.data,
            "hostkit": {
                "version": __version__,
                "vps": self._vps_ip,
            },
        }

    def send_alert(
        self,
        project_name: str,
        event_type: str,
        event_status: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Send an alert to all enabled channels for a project.

        Args:
            project_name: Name of the project
            event_type: Type of event (deploy, migrate, health, etc.)
            event_status: Status (success, failure)
            data: Event-specific data

        Returns:
            Dict with send results
        """
        # Get enabled channels
        channels = self.db.list_alert_channels(
            project_name=project_name,
            enabled_only=True,
        )

        if not channels:
            return {
                "project": project_name,
                "event_type": event_type,
                "channels_notified": 0,
                "message": "No enabled channels configured",
            }

        # Build event
        event = AlertEvent(
            event_type=event_type,
            event_status=event_status,
            project=project_name,
            data=data,
        )

        # Build payload (for webhook and history)
        payload = self._build_payload(event)
        payload_json = json.dumps(payload)

        # Send to each channel
        results = []
        muted_count = 0

        for ch in channels:
            channel_type = ch["channel_type"]
            config = self._parse_channel_config(ch["config"], channel_type)

            # Check if channel is muted
            muted_until = ch.get("muted_until")
            if muted_until:
                mute_time = datetime.fromisoformat(muted_until.replace("Z", "+00:00"))
                is_muted = datetime.utcnow() < mute_time.replace(tzinfo=None) if mute_time.tzinfo else datetime.utcnow() < mute_time
                if is_muted:
                    muted_count += 1
                    # Record in history as muted
                    self.db.create_alert_history(
                        project_name=project_name,
                        event_type=event_type,
                        event_status=event_status,
                        channel_name=ch["name"],
                        notification_sent=False,
                        notification_error="Channel is muted",
                        payload=payload_json,
                    )
                    results.append({
                        "channel": ch["name"],
                        "success": False,
                        "muted": True,
                        "error": "Channel is muted",
                    })
                    continue

            # Send based on channel type
            if channel_type == "webhook":
                if isinstance(config, WebhookConfig):
                    success, error = self._send_webhook(
                        url=config.url,
                        payload=payload,
                        secret=config.secret,
                        headers=config.headers,
                    )
                else:
                    success, error = False, "Invalid webhook config"
            elif channel_type == "email":
                if isinstance(config, EmailConfig):
                    success, error = self._send_email(config, event)
                else:
                    success, error = False, "Invalid email config"
            elif channel_type == "slack":
                if isinstance(config, SlackConfig):
                    success, error = self._send_slack(config, event)
                else:
                    success, error = False, "Invalid slack config"
            else:
                success = False
                error = f"Unsupported channel type: {channel_type}"

            # Record in history
            self.db.create_alert_history(
                project_name=project_name,
                event_type=event_type,
                event_status=event_status,
                channel_name=ch["name"],
                notification_sent=success,
                notification_error=error,
                payload=payload_json,
            )

            results.append({
                "channel": ch["name"],
                "success": success,
                "error": error,
            })

        return {
            "project": project_name,
            "event_type": event_type,
            "event_status": event_status,
            "channels_notified": sum(1 for r in results if r.get("success")),
            "channels_failed": sum(1 for r in results if not r.get("success") and not r.get("muted")),
            "channels_muted": muted_count,
            "results": results,
            "sent_at": datetime.utcnow().isoformat(),
        }

    def get_history(
        self,
        project_name: str,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get alert history for a project."""
        self._validate_project(project_name)

        history = self.db.list_alert_history(
            project_name=project_name,
            event_type=event_type,
            limit=limit,
        )

        return [
            {
                "id": h["id"],
                "event_type": h["event_type"],
                "event_status": h["event_status"],
                "channel": h["channel_name"],
                "sent": bool(h["notification_sent"]),
                "error": h["notification_error"],
                "created_at": h["created_at"],
            }
            for h in history
        ]


# Convenience function for sending alerts from other modules
def send_alert(
    project_name: str,
    event_type: str,
    event_status: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Send an alert to all enabled channels for a project.

    This is a convenience function that creates an AlertService instance
    and sends the alert. Use this from other modules like deploy.py.

    Args:
        project_name: Name of the project
        event_type: Type of event (deploy, migrate, health, etc.)
        event_status: Status (success, failure)
        data: Event-specific data

    Returns:
        Dict with send results
    """
    service = AlertService()
    return service.send_alert(project_name, event_type, event_status, data)
