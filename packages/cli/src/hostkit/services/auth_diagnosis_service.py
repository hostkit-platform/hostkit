"""Auth service diagnosis and troubleshooting service for HostKit."""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class AuthDiagnosisResult:
    """Result of diagnosing an auth service."""

    project: str
    overall_health: str  # "healthy", "degraded", "critical"
    service_status: dict[str, Any] = field(default_factory=dict)
    remote_diagnostics: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project": self.project,
            "overall_health": self.overall_health,
            "service_status": self.service_status,
            "remote_diagnostics": self.remote_diagnostics,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp,
        }


class AuthDiagnosisService:
    """Diagnose auth service issues."""

    def __init__(self):
        """Initialize diagnosis service."""
        pass

    def diagnose(self, project: str, verbose: bool = False, test_endpoints: bool = False) -> AuthDiagnosisResult:
        """Run comprehensive auth service diagnostics.

        Args:
            project: Project name
            verbose: Include detailed logs
            test_endpoints: Test auth endpoints (optional)

        Returns:
            AuthDiagnosisResult with health status and recommendations
        """
        result = AuthDiagnosisResult(project=project, overall_health="healthy")

        # Check service status
        self._check_service_status(project, result)

        # Call remote diagnostics endpoint
        self._check_remote_diagnostics(project, result)

        # Determine overall health
        if result.issues:
            if any(issue.startswith("ERROR") or issue.startswith("CRITICAL") for issue in result.issues):
                result.overall_health = "critical"
            else:
                result.overall_health = "degraded"

        return result

    def _check_service_status(self, project: str, result: AuthDiagnosisResult) -> None:
        """Check systemd service status."""
        service_name = f"hostkit-{project}-auth"

        try:
            # Get service status
            status_output = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )

            is_active = status_output.returncode == 0
            result.service_status["active"] = is_active

            if is_active:
                result.service_status["status"] = "running"
            else:
                result.service_status["status"] = "inactive"
                result.issues.append("ERROR: Auth service is not running")
                result.recommendations.append(f"Start service: systemctl start {service_name}")

        except Exception as e:
            logger.error(f"Error checking service status: {e}")
            result.issues.append(f"ERROR: Cannot determine service status: {e}")

    def _check_remote_diagnostics(self, project: str, result: AuthDiagnosisResult) -> None:
        """Call remote /auth/diagnose endpoint."""
        # Try both hostkit.dev and direct IP
        urls = [
            f"https://{project}.hostkit.dev/auth/diagnose",
        ]

        for url in urls:
            try:
                response = requests.get(url, timeout=10, verify=False)  # SSL may not be ready
                if response.status_code == 200:
                    result.remote_diagnostics = response.json()

                    # Extract issues from remote checks
                    if result.remote_diagnostics.get("overall_health") != "healthy":
                        for check in result.remote_diagnostics.get("checks", []):
                            if check.get("status") == "error":
                                error_msg = check.get("message", "Unknown error")
                                suggestion = check.get("suggestion", "")
                                result.issues.append(f"ERROR: {error_msg}")
                                if suggestion:
                                    result.recommendations.append(suggestion)
                            elif check.get("status") == "warning":
                                warning_msg = check.get("message", "Unknown warning")
                                result.issues.append(f"WARNING: {warning_msg}")
                    break

            except requests.exceptions.ConnectionError:
                logger.debug(f"Cannot connect to {url}")
                result.issues.append(f"WARNING: Cannot reach auth service at {url}")
                result.recommendations.append(
                    f"Check if service is running: systemctl status hostkit-{project}-auth"
                )
            except Exception as e:
                logger.error(f"Error calling remote diagnostics: {e}")

    def _check_logs(self, project: str, result: AuthDiagnosisResult, verbose: bool = False) -> None:
        """Check auth service logs for error patterns."""
        service_name = f"hostkit-{project}-auth"

        try:
            # Get recent logs
            cmd = ["journalctl", "-u", service_name, "-n", "50", "--output=json"]
            logs_output = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )

            if logs_output.returncode == 0:
                lines = logs_output.stdout.strip().split("\n")
                for line in lines:
                    if not line:
                        continue
                    try:
                        log_entry = json.loads(line)
                        message = log_entry.get("MESSAGE", "")

                        # Detect auth-specific error patterns
                        if "jwt_verification_failed" in message.lower():
                            result.issues.append("ERROR: JWT token verification failing")
                            result.recommendations.append(
                                "Check JWT keys: hostkit auth export-key {project} --update-env"
                            )
                        elif "oauth_error" in message.lower() or "oauth callback" in message.lower():
                            result.issues.append("ERROR: OAuth provider error detected")
                            result.recommendations.append(
                                "Verify OAuth credentials: hostkit auth config {project} --show"
                            )
                        elif "smtp" in message.lower() or "email" in message.lower():
                            result.issues.append("WARNING: Email/SMTP error")
                            result.recommendations.append(
                                "Configure SMTP: hostkit env set {project}-auth SMTP_HOST=... SMTP_PORT=..."
                            )

                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            logger.error(f"Error reading logs: {e}")
