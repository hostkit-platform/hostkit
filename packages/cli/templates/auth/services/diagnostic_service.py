"""Diagnostic service for auth service health checks."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticCheck:
    """A single diagnostic check result."""

    name: str
    status: str  # "ok", "warning", "error"
    message: str
    suggestion: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "suggestion": self.suggestion,
            "details": self.details,
        }


@dataclass
class DiagnosticResult:
    """Complete diagnostic result."""

    overall_health: str  # "healthy", "degraded", "critical"
    checks: list[DiagnosticCheck] = field(default_factory=list)
    configuration: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "overall_health": self.overall_health,
            "checks": [c.to_dict() for c in self.checks],
            "configuration": self.configuration,
            "timestamp": self.timestamp,
        }


class DiagnosticService:
    """Service for diagnosing auth service health."""

    def __init__(self):
        """Initialize diagnostic service."""
        self.settings = get_settings()
        self.checks: list[DiagnosticCheck] = []

    async def run_diagnostics(self, db: AsyncSession) -> DiagnosticResult:
        """Run all diagnostic checks."""
        logger.info("Starting diagnostic checks")

        # Run all checks
        await self._check_database(db)
        await self._check_jwt_keys()
        self._check_oauth_configuration()
        self._check_email_configuration()
        self._check_base_url()
        self._check_cors_configuration()

        # Determine overall health
        critical_checks = [c for c in self.checks if c.status == "error"]
        warning_checks = [c for c in self.checks if c.status == "warning"]

        if critical_checks:
            overall_health = "critical"
        elif warning_checks:
            overall_health = "degraded"
        else:
            overall_health = "healthy"

        # Build safe configuration (redact secrets)
        safe_config = {
            "project_name": self.settings.project_name,
            "base_url": self.settings.base_url,
            "auth_service_port": self.settings.auth_service_port,
            "email_enabled": self.settings.email_enabled,
            "magic_link_enabled": self.settings.magic_link_enabled,
            "anonymous_enabled": self.settings.anonymous_enabled,
            "google_enabled": self.settings.google_enabled,
            "apple_enabled": self.settings.apple_enabled,
            "jwt_keys_configured": bool(self.settings.jwt_private_key and self.settings.jwt_public_key),
        }

        return DiagnosticResult(
            overall_health=overall_health,
            checks=self.checks,
            configuration=safe_config,
        )

    async def _check_database(self, db: AsyncSession) -> None:
        """Check database connectivity and schema."""
        try:
            # Test connectivity
            await db.execute(text("SELECT 1"))

            # Check for required tables
            result = await db.execute(
                text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name IN ('users', 'sessions', 'oauth_accounts', 'magic_links')
                """)
            )
            tables = {row[0] for row in result.fetchall()}
            required_tables = {"users", "sessions", "oauth_accounts", "magic_links"}

            if tables == required_tables:
                self.checks.append(
                    DiagnosticCheck(
                        name="database",
                        status="ok",
                        message="Database connected and all required tables exist",
                        details={"tables": sorted(tables)},
                    )
                )
            else:
                missing = required_tables - tables
                self.checks.append(
                    DiagnosticCheck(
                        name="database",
                        status="error",
                        message=f"Missing database tables: {', '.join(sorted(missing))}",
                        suggestion="Run database migrations (auth service should do this on startup)",
                        details={"found_tables": sorted(tables), "missing_tables": sorted(missing)},
                    )
                )

        except Exception as e:
            logger.error(f"Database check failed: {e}", exc_info=True)
            self.checks.append(
                DiagnosticCheck(
                    name="database",
                    status="error",
                    message=f"Database connection failed: {str(e)}",
                    suggestion="Check AUTH_DB_URL environment variable and PostgreSQL connectivity",
                    details={"error": str(e)},
                )
            )

    async def _check_jwt_keys(self) -> None:
        """Check JWT key configuration."""
        issues = []
        details = {"private_key_path": self.settings.jwt_private_key_path, "public_key_path": self.settings.jwt_public_key_path}

        # Check private key
        if not self.settings.jwt_private_key_path:
            issues.append("JWT_PRIVATE_KEY_PATH not set")
        else:
            private_key_path = Path(self.settings.jwt_private_key_path)
            if not private_key_path.exists():
                issues.append(f"Private key file not found: {self.settings.jwt_private_key_path}")
                details["private_key_exists"] = False
            else:
                details["private_key_exists"] = True
                # Check if it's valid PEM
                try:
                    key_content = private_key_path.read_text()
                    if not key_content.strip().startswith("-----BEGIN"):
                        issues.append("Private key does not appear to be valid PEM format")
                    else:
                        details["private_key_valid_pem"] = True
                except Exception as e:
                    issues.append(f"Error reading private key: {e}")

        # Check public key
        if not self.settings.jwt_public_key_path:
            issues.append("JWT_PUBLIC_KEY_PATH not set")
        else:
            public_key_path = Path(self.settings.jwt_public_key_path)
            if not public_key_path.exists():
                issues.append(f"Public key file not found: {self.settings.jwt_public_key_path}")
                details["public_key_exists"] = False
            else:
                details["public_key_exists"] = True
                # Check if it's valid PEM
                try:
                    key_content = public_key_path.read_text()
                    if not key_content.strip().startswith("-----BEGIN"):
                        issues.append("Public key does not appear to be valid PEM format")
                    else:
                        details["public_key_valid_pem"] = True
                except Exception as e:
                    issues.append(f"Error reading public key: {e}")

        if issues:
            self.checks.append(
                DiagnosticCheck(
                    name="jwt_keys",
                    status="error",
                    message="; ".join(issues),
                    suggestion="Regenerate JWT keys: hostkit auth export-key {project} --update-env",
                    details=details,
                )
            )
        else:
            self.checks.append(
                DiagnosticCheck(
                    name="jwt_keys",
                    status="ok",
                    message="JWT keys are properly configured",
                    details=details,
                )
            )

    def _check_oauth_configuration(self) -> None:
        """Check OAuth provider configuration."""
        details: dict[str, Any] = {}
        warnings = []

        # Google OAuth
        if self.settings.google_enabled:
            if not self.settings.google_client_id and not self.settings.google_web_client_id:
                warnings.append("Google OAuth enabled but CLIENT_ID not set")
            if not self.settings.google_client_secret:
                warnings.append("Google OAuth enabled but CLIENT_SECRET not set")
            details["google_configured"] = bool(self.settings.google_client_id or self.settings.google_web_client_id) and bool(
                self.settings.google_client_secret
            )
        else:
            details["google_configured"] = False

        # Apple OAuth
        if self.settings.apple_enabled:
            if not self.settings.apple_team_id:
                warnings.append("Apple Sign-In enabled but TEAM_ID not set")
            if not self.settings.apple_key_id:
                warnings.append("Apple Sign-In enabled but KEY_ID not set")
            if not self.settings.apple_private_key_content:
                warnings.append("Apple Sign-In enabled but private key not set")
            details["apple_configured"] = self.settings.apple_enabled and bool(self.settings.apple_private_key_content)
        else:
            details["apple_configured"] = False

        if warnings:
            self.checks.append(
                DiagnosticCheck(
                    name="oauth",
                    status="warning",
                    message="; ".join(warnings),
                    suggestion="Configure OAuth providers: hostkit auth config {project} --google-client-id=xxx",
                    details=details,
                )
            )
        else:
            self.checks.append(
                DiagnosticCheck(
                    name="oauth",
                    status="ok",
                    message="OAuth configuration is valid",
                    details=details,
                )
            )

    def _check_email_configuration(self) -> None:
        """Check email/SMTP configuration."""
        details = {"email_enabled": self.settings.email_enabled}
        warnings = []

        if self.settings.email_enabled:
            # Note: SMTP config is loaded from environment variables in the email service
            # We can't directly check here without duplicating that logic, so we note
            # that email is enabled and suggest checking SMTP env vars
            details["email_feature"] = "enabled"
            warnings.append("Email enabled - ensure SMTP_* environment variables are configured")

            self.checks.append(
                DiagnosticCheck(
                    name="email",
                    status="warning",
                    message="; ".join(warnings) if warnings else "Email enabled",
                    suggestion="Verify SMTP configuration: hostkit env get {project}-auth | grep SMTP",
                    details=details,
                )
            )
        else:
            details["email_feature"] = "disabled"
            self.checks.append(
                DiagnosticCheck(
                    name="email",
                    status="ok",
                    message="Email authentication is disabled",
                    details=details,
                )
            )

    def _check_base_url(self) -> None:
        """Check base URL configuration."""
        details: dict[str, Any] = {}

        if not self.settings.base_url:
            self.checks.append(
                DiagnosticCheck(
                    name="base_url",
                    status="warning",
                    message="BASE_URL is not configured",
                    suggestion="Set BASE_URL environment variable: hostkit env set {project}-auth BASE_URL=https://myapp.hostkit.dev",
                    details={"configured": False},
                )
            )
        elif not self.settings.base_url.startswith("https://"):
            self.checks.append(
                DiagnosticCheck(
                    name="base_url",
                    status="warning",
                    message=f"BASE_URL does not use HTTPS: {self.settings.base_url}",
                    suggestion="OAuth requires HTTPS. Update to: https://...",
                    details={"configured": True, "value": self.settings.base_url, "uses_https": False},
                )
            )
        else:
            details["configured"] = True
            details["value"] = self.settings.base_url
            details["uses_https"] = True
            self.checks.append(
                DiagnosticCheck(
                    name="base_url",
                    status="ok",
                    message=f"BASE_URL properly configured: {self.settings.base_url}",
                    details=details,
                )
            )

    def _check_cors_configuration(self) -> None:
        """Check CORS configuration."""
        details = {"configured": bool(self.settings.auth_cors_origins)}

        if self.settings.auth_cors_origins:
            origins = [o.strip() for o in self.settings.auth_cors_origins.split(",")]
            details["origins"] = origins
            self.checks.append(
                DiagnosticCheck(
                    name="cors",
                    status="ok",
                    message=f"CORS configured for {len(origins)} origin(s)",
                    details=details,
                )
            )
        else:
            self.checks.append(
                DiagnosticCheck(
                    name="cors",
                    status="warning",
                    message="No CORS origins configured beyond default (localhost:3000 + BASE_URL)",
                    suggestion="If you have additional frontend domains, set AUTH_CORS_ORIGINS",
                    details=details,
                )
            )
