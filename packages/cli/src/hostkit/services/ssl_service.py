"""SSL certificate management for HostKit using Let's Encrypt/Certbot."""

import re
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db

# Rate limiting constants
SSL_MAX_ATTEMPTS_PER_DAY = 3
SSL_COOLDOWN_HOURS = 1  # Hours to wait after a failure


# Dev domain patterns that don't require DNS validation
DEV_DOMAIN_SUFFIXES = (".nip.io", ".sslip.io", ".localhost", ".local")


@dataclass
class CertificateInfo:
    """Information about an SSL certificate."""

    domain: str
    issuer: str
    valid_from: str
    valid_until: str
    days_remaining: int
    serial: str
    subject_alt_names: list[str]
    certificate_path: str
    key_path: str


class SSLError(Exception):
    """Base exception for SSL errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class SSLService:
    """Service for managing SSL certificates via Let's Encrypt/Certbot."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.letsencrypt_live = Path("/etc/letsencrypt/live")
        self.certbot_bin = "certbot"

    def _is_dev_domain(self, domain: str) -> bool:
        """Check if domain is a development domain."""
        return any(domain.endswith(suffix) for suffix in DEV_DOMAIN_SUFFIXES)

    def _resolve_domain(self, domain: str) -> str | None:
        """Resolve a domain to its IP address."""
        try:
            result = socket.getaddrinfo(domain, None, socket.AF_INET)
            if result:
                return result[0][4][0]
        except socket.gaierror:
            pass
        return None

    def _validate_dns_for_ssl(self, domain: str) -> None:
        """Validate DNS before attempting SSL provisioning.

        Raises:
            SSLError: If DNS validation fails
        """
        # Skip validation for dev domains (they can't get real SSL anyway)
        if self._is_dev_domain(domain):
            raise SSLError(
                code="DEV_DOMAIN",
                message=f"Cannot provision SSL for dev domain '{domain}'",
                suggestion="Dev domains like nip.io don't support SSL. Use a real domain.",
            )

        resolved_ip = self._resolve_domain(domain)

        if resolved_ip is None:
            raise SSLError(
                code="DNS_RESOLUTION_FAILED",
                message=f"Domain '{domain}' could not be resolved",
                suggestion=f"Ensure the domain has an A record pointing to {self.config.vps_ip}",
            )

        if resolved_ip != self.config.vps_ip:
            raise SSLError(
                code="DNS_MISMATCH",
                message=(
                    f"Domain '{domain}' resolves to {resolved_ip}, expected {self.config.vps_ip}"
                ),
                suggestion=f"Update the domain's A record to point to {self.config.vps_ip}",
            )

    def _check_rate_limit(self, project: str) -> None:
        """Check if project has exceeded SSL provisioning rate limits.

        Raises:
            SSLError: If rate limit exceeded or in cooldown
        """
        # Check attempts in last 24 hours
        attempts_today = self.db.get_ssl_attempts_count(project, hours=24)
        if attempts_today >= SSL_MAX_ATTEMPTS_PER_DAY:
            raise SSLError(
                code="RATE_LIMIT_EXCEEDED",
                message=(
                    f"SSL rate limit exceeded:"
                    f" {attempts_today}/{SSL_MAX_ATTEMPTS_PER_DAY}"
                    f" attempts in last 24 hours"
                ),
                suggestion="Wait until tomorrow or contact administrator",
            )

        # Check for recent failure cooldown
        last_failure = self.db.get_last_ssl_failure(project)
        if last_failure:
            failure_time = datetime.fromisoformat(last_failure["attempted_at"])
            cooldown_end = failure_time + timedelta(hours=SSL_COOLDOWN_HOURS)
            now = datetime.utcnow()

            if now < cooldown_end:
                remaining = cooldown_end - now
                minutes = int(remaining.total_seconds() / 60)
                raise SSLError(
                    code="COOLDOWN_ACTIVE",
                    message=(
                        f"SSL cooldown active: {minutes} minutes remaining after failed attempt"
                    ),
                    suggestion=f"Wait {minutes} minutes before retrying",
                )

    def list_certificates(self) -> list[CertificateInfo]:
        """List all SSL certificates managed by Certbot."""
        certificates = []

        try:
            result = subprocess.run(
                [self.certbot_bin, "certificates"],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                # No certificates or error
                return certificates

            # Parse certbot output
            current_cert: dict[str, Any] = {}
            for line in result.stdout.split("\n"):
                line = line.strip()

                if line.startswith("Certificate Name:"):
                    if current_cert:
                        certificates.append(self._parse_cert_info(current_cert))
                    current_cert = {"name": line.split(":", 1)[1].strip()}

                elif line.startswith("Serial Number:"):
                    current_cert["serial"] = line.split(":", 1)[1].strip()

                elif line.startswith("Key Type:"):
                    current_cert["key_type"] = line.split(":", 1)[1].strip()

                elif line.startswith("Domains:"):
                    domains_str = line.split(":", 1)[1].strip()
                    current_cert["domains"] = [d.strip() for d in domains_str.split()]

                elif line.startswith("Expiry Date:"):
                    # Parse: "Expiry Date: 2025-03-12 10:30:00+00:00 (VALID: 89 days)"
                    match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if match:
                        current_cert["expiry"] = match.group(1)
                    # Extract days remaining
                    days_match = re.search(r"\(VALID: (\d+) days?\)", line)
                    if days_match:
                        current_cert["days_remaining"] = int(days_match.group(1))
                    else:
                        current_cert["days_remaining"] = 0

                elif line.startswith("Certificate Path:"):
                    current_cert["cert_path"] = line.split(":", 1)[1].strip()

                elif line.startswith("Private Key Path:"):
                    current_cert["key_path"] = line.split(":", 1)[1].strip()

            # Don't forget the last certificate
            if current_cert:
                certificates.append(self._parse_cert_info(current_cert))

        except subprocess.TimeoutExpired:
            raise SSLError(
                code="LIST_TIMEOUT",
                message="Listing certificates timed out",
            )
        except FileNotFoundError:
            raise SSLError(
                code="CERTBOT_NOT_FOUND",
                message="Certbot is not installed",
                suggestion="Install certbot with 'apt install certbot python3-certbot-nginx'",
            )

        return certificates

    def _parse_cert_info(self, cert_data: dict[str, Any]) -> CertificateInfo:
        """Parse certificate data into CertificateInfo."""
        domains = cert_data.get("domains", [])
        primary_domain = domains[0] if domains else cert_data.get("name", "unknown")

        return CertificateInfo(
            domain=primary_domain,
            issuer="Let's Encrypt",
            valid_from="",  # Not provided by certbot certificates
            valid_until=cert_data.get("expiry", ""),
            days_remaining=cert_data.get("days_remaining", 0),
            serial=cert_data.get("serial", ""),
            subject_alt_names=domains,
            certificate_path=cert_data.get(
                "cert_path", f"/etc/letsencrypt/live/{primary_domain}/fullchain.pem"
            ),
            key_path=cert_data.get(
                "key_path", f"/etc/letsencrypt/live/{primary_domain}/privkey.pem"
            ),
        )

    def get_certificate(self, domain: str) -> CertificateInfo:
        """Get information about a specific certificate."""
        certificates = self.list_certificates()

        for cert in certificates:
            if cert.domain == domain or domain in cert.subject_alt_names:
                return cert

        raise SSLError(
            code="CERTIFICATE_NOT_FOUND",
            message=f"No certificate found for domain '{domain}'",
            suggestion=f"Run 'hostkit ssl provision {domain}' to get a certificate",
        )

    def get_certificate_status(self, domain: str) -> dict[str, Any]:
        """Get detailed status of a certificate."""
        try:
            cert = self.get_certificate(domain)

            # Determine health status
            if cert.days_remaining < 0:
                status = "expired"
            elif cert.days_remaining < 7:
                status = "critical"
            elif cert.days_remaining < 30:
                status = "warning"
            else:
                status = "valid"

            return {
                "domain": cert.domain,
                "status": status,
                "days_remaining": cert.days_remaining,
                "expires": cert.valid_until,
                "issuer": cert.issuer,
                "serial": cert.serial,
                "alt_names": cert.subject_alt_names,
                "certificate_path": cert.certificate_path,
                "key_path": cert.key_path,
            }

        except SSLError:
            return {
                "domain": domain,
                "status": "not_provisioned",
                "days_remaining": 0,
                "expires": None,
                "issuer": None,
                "serial": None,
                "alt_names": [],
                "certificate_path": None,
                "key_path": None,
            }

    def provision(
        self, domain: str, email: str | None = None, *, skip_dns: bool = False
    ) -> dict[str, Any]:
        """Provision a new SSL certificate for a domain using Let's Encrypt."""
        # Verify domain is configured in HostKit
        domain_record = self.db.get_domain(domain)
        if not domain_record:
            raise SSLError(
                code="DOMAIN_NOT_CONFIGURED",
                message=f"Domain '{domain}' is not configured in HostKit",
                suggestion=f"First add the domain with 'hostkit nginx add <project> {domain}'",
            )

        project = domain_record["project"]

        # Check rate limits before proceeding
        self._check_rate_limit(project)

        # Validate DNS before attempting SSL (fail fast)
        if not skip_dns:
            self._validate_dns_for_ssl(domain)

        # Check if certificate already exists
        try:
            existing = self.get_certificate(domain)
            if existing.days_remaining > 30:
                raise SSLError(
                    code="CERTIFICATE_EXISTS",
                    message=(
                        f"Certificate for '{domain}' already"
                        f" exists and is valid for"
                        f" {existing.days_remaining} days"
                    ),
                    suggestion="Use 'hostkit ssl renew' to renew early if needed",
                )
        except SSLError as e:
            if e.code != "CERTIFICATE_NOT_FOUND":
                raise

        # Get admin email from config if not provided
        if not email:
            email = self.config.admin_email
            if not email:
                raise SSLError(
                    code="EMAIL_REQUIRED",
                    message="Admin email is required for Let's Encrypt registration",
                    suggestion="Set HOSTKIT_ADMIN_EMAIL environment variable or pass --email",
                )

        # Run certbot
        try:
            cmd = [
                self.certbot_bin,
                "certonly",
                "--nginx",
                "-d",
                domain,
                "--non-interactive",
                "--agree-tos",
                "--email",
                email,
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                # Record the failed attempt
                self.db.record_ssl_attempt(
                    project, domain, success=False, error_message=error_msg[:500]
                )

                # Check for common errors
                if "DNS problem" in error_msg or "NXDOMAIN" in error_msg:
                    raise SSLError(
                        code="DNS_ERROR",
                        message=f"DNS is not configured for '{domain}'",
                        suggestion=(
                            "Ensure the domain points to this server's IP before provisioning SSL"
                        ),
                    )
                elif "rate limit" in error_msg.lower():
                    raise SSLError(
                        code="RATE_LIMITED",
                        message="Let's Encrypt rate limit reached",
                        suggestion="Wait before trying again, or use staging for testing",
                    )
                elif "connection refused" in error_msg.lower():
                    raise SSLError(
                        code="CONNECTION_REFUSED",
                        message="Certbot could not connect to verify domain",
                        suggestion="Ensure Nginx is running and port 80 is accessible",
                    )
                else:
                    raise SSLError(
                        code="PROVISION_FAILED",
                        message=f"Certificate provisioning failed: {error_msg}",
                        suggestion="Check domain DNS and firewall settings",
                    )

            # Record the successful attempt
            self.db.record_ssl_attempt(project, domain, success=True)

            # Update domain SSL status in database
            self.db.update_domain_ssl(domain, True)

            # Update Nginx config to use SSL
            from hostkit.services.nginx_service import NginxService

            nginx = NginxService()
            nginx.enable_ssl_for_project(domain_record["project"])

            return {
                "domain": domain,
                "provisioned": True,
                "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
                "key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
                "message": f"SSL certificate provisioned for {domain}",
            }

        except subprocess.TimeoutExpired:
            # Record the failed attempt
            self.db.record_ssl_attempt(project, domain, success=False, error_message="Timeout")
            raise SSLError(
                code="PROVISION_TIMEOUT",
                message="Certificate provisioning timed out",
                suggestion="Check network connectivity and try again",
            )
        except FileNotFoundError:
            raise SSLError(
                code="CERTBOT_NOT_FOUND",
                message="Certbot is not installed",
                suggestion="Install certbot with 'apt install certbot python3-certbot-nginx'",
            )

    def renew(self, domain: str | None = None, force: bool = False) -> dict[str, Any]:
        """Renew SSL certificates."""
        try:
            cmd = [self.certbot_bin, "renew"]

            if domain:
                cmd.extend(["--cert-name", domain])

            if force:
                cmd.append("--force-renewal")

            # Add hooks to reload nginx after renewal
            cmd.extend(
                [
                    "--deploy-hook",
                    "systemctl reload nginx",
                ]
            )

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes for renewals
            )

            if result.returncode != 0:
                raise SSLError(
                    code="RENEW_FAILED",
                    message=f"Certificate renewal failed: {result.stderr or result.stdout}",
                )

            # Parse output to count renewals
            output = result.stdout
            renewed_count = output.count("Congratulations")
            skipped_count = output.count("not yet due")

            return {
                "renewed": renewed_count,
                "skipped": skipped_count,
                "output": output,
                "message": (
                    f"Renewed {renewed_count} certificate(s), {skipped_count} not due for renewal"
                ),
            }

        except subprocess.TimeoutExpired:
            raise SSLError(
                code="RENEW_TIMEOUT",
                message="Certificate renewal timed out",
            )

    def check_auto_renewal(self) -> dict[str, Any]:
        """Check if auto-renewal timer is active."""
        try:
            # Check certbot timer status
            result = subprocess.run(
                ["systemctl", "is-active", "certbot.timer"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            timer_active = result.stdout.strip() == "active"

            # Check certbot timer enabled
            result = subprocess.run(
                ["systemctl", "is-enabled", "certbot.timer"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            timer_enabled = result.stdout.strip() == "enabled"

            # Get next run time
            next_run = None
            try:
                result = subprocess.run(
                    ["systemctl", "show", "certbot.timer", "--property=NextElapseUSecRealtime"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    line = result.stdout.strip()
                    if "=" in line:
                        next_run = line.split("=", 1)[1]
            except subprocess.SubprocessError:
                pass

            return {
                "timer_active": timer_active,
                "timer_enabled": timer_enabled,
                "next_run": next_run,
                "status": "active" if timer_active and timer_enabled else "inactive",
            }

        except FileNotFoundError:
            return {
                "timer_active": False,
                "timer_enabled": False,
                "next_run": None,
                "status": "not_installed",
            }

    def enable_auto_renewal(self) -> dict[str, Any]:
        """Enable the certbot auto-renewal timer."""
        try:
            subprocess.run(
                ["systemctl", "enable", "certbot.timer"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "start", "certbot.timer"],
                check=True,
                capture_output=True,
            )

            return {
                "enabled": True,
                "message": "Auto-renewal timer enabled",
            }

        except subprocess.CalledProcessError as e:
            raise SSLError(
                code="ENABLE_FAILED",
                message=(
                    "Failed to enable auto-renewal: "
                    + (e.stderr.decode() if e.stderr else "unknown error")
                ),
            )

    def test_renewal(self, domain: str | None = None) -> dict[str, Any]:
        """Test certificate renewal without actually renewing (dry run)."""
        try:
            cmd = [self.certbot_bin, "renew", "--dry-run"]

            if domain:
                cmd.extend(["--cert-name", domain])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            success = result.returncode == 0

            return {
                "success": success,
                "output": result.stdout,
                "errors": result.stderr if not success else None,
            }

        except subprocess.TimeoutExpired:
            raise SSLError(
                code="TEST_TIMEOUT",
                message="Renewal test timed out",
            )
