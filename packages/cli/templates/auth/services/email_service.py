"""Email service for authentication emails.

Sends magic links, verification emails, and password reset emails.
Uses SMTP configuration from environment variables.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlencode

from config import get_settings

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending authentication emails.

    Sends:
    - Magic link emails for passwordless login
    - Email verification emails
    - Password reset emails
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.smtp_host = os.environ.get("SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_pass = os.environ.get("SMTP_PASS", "")
        self.from_email = os.environ.get("SMTP_FROM", self.smtp_user)
        self.from_name = os.environ.get("SMTP_FROM_NAME", "HostKit Auth")

    @property
    def is_configured(self) -> bool:
        """Check if email is configured."""
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass)

    def _send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> bool:
        """Send an email via SMTP.

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_body: HTML email body
            text_body: Plain text body (optional fallback)

        Returns:
            True if email was sent successfully
        """
        if not self.is_configured:
            logger.warning("Email not configured, skipping send")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email

            # Add plain text version
            if text_body:
                msg.attach(MIMEText(text_body, "plain"))

            # Add HTML version
            msg.attach(MIMEText(html_body, "html"))

            # Send via SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)

            logger.info(f"Email sent to {to_email}: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    def send_magic_link(
        self,
        to_email: str,
        token: str,
        redirect_url: str | None = None,
    ) -> bool:
        """Send a magic link email.

        Args:
            to_email: Recipient email
            token: The magic link token
            redirect_url: URL to redirect after authentication

        Returns:
            True if email was sent
        """
        # Build magic link URL
        params = {"token": token}
        if redirect_url:
            params["redirect"] = redirect_url

        magic_link = f"{self.settings.base_url}/auth/magic-link/verify?{urlencode(params)}"

        subject = f"Sign in to {self.settings.project_name}"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2>Sign in to {self.settings.project_name}</h2>
            <p>Click the button below to sign in. This link expires in 15 minutes.</p>
            <p style="margin: 30px 0;">
                <a href="{magic_link}" style="background: #0066cc; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">
                    Sign In
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                If you didn't request this email, you can safely ignore it.
            </p>
            <p style="color: #666; font-size: 14px;">
                Or copy this link: {magic_link}
            </p>
        </body>
        </html>
        """

        text_body = f"""
Sign in to {self.settings.project_name}

Click this link to sign in (expires in 15 minutes):
{magic_link}

If you didn't request this email, you can safely ignore it.
        """

        return self._send_email(to_email, subject, html_body, text_body)

    def send_verification_email(
        self,
        to_email: str,
        token: str,
        redirect_url: str | None = None,
    ) -> bool:
        """Send an email verification email.

        Args:
            to_email: Recipient email
            token: The verification token
            redirect_url: URL to redirect after verification

        Returns:
            True if email was sent
        """
        params = {"token": token}
        if redirect_url:
            params["redirect"] = redirect_url

        verify_link = f"{self.settings.base_url}/auth/verify-email?{urlencode(params)}"

        subject = f"Verify your email for {self.settings.project_name}"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2>Verify your email</h2>
            <p>Thanks for signing up for {self.settings.project_name}! Please verify your email address.</p>
            <p style="margin: 30px 0;">
                <a href="{verify_link}" style="background: #0066cc; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">
                    Verify Email
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This link expires in 24 hours.
            </p>
        </body>
        </html>
        """

        text_body = f"""
Verify your email for {self.settings.project_name}

Click this link to verify your email (expires in 24 hours):
{verify_link}
        """

        return self._send_email(to_email, subject, html_body, text_body)

    def send_password_reset(
        self,
        to_email: str,
        token: str,
        redirect_url: str | None = None,
    ) -> bool:
        """Send a password reset email.

        Args:
            to_email: Recipient email
            token: The reset token
            redirect_url: URL to redirect after reset

        Returns:
            True if email was sent
        """
        params = {"token": token}
        if redirect_url:
            params["redirect"] = redirect_url

        reset_link = f"{self.settings.base_url}/auth/reset-password?{urlencode(params)}"

        subject = f"Reset your password for {self.settings.project_name}"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2>Reset your password</h2>
            <p>We received a request to reset your password for {self.settings.project_name}.</p>
            <p style="margin: 30px 0;">
                <a href="{reset_link}" style="background: #0066cc; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">
                    Reset Password
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This link expires in 1 hour. If you didn't request a password reset, you can safely ignore this email.
            </p>
        </body>
        </html>
        """

        text_body = f"""
Reset your password for {self.settings.project_name}

Click this link to reset your password (expires in 1 hour):
{reset_link}

If you didn't request a password reset, you can safely ignore this email.
        """

        return self._send_email(to_email, subject, html_body, text_body)


# Singleton instance
_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    """Get the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
