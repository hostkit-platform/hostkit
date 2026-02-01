"""Nginx reverse proxy management for HostKit."""

import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Template

from hostkit.config import get_config
from hostkit.database import get_db

# Dev domain patterns that don't require DNS validation
DEV_DOMAIN_SUFFIXES = (".nip.io", ".sslip.io", ".localhost", ".local")



@dataclass
class NginxSite:
    """Information about an Nginx site configuration."""

    project: str
    domains: list[str]
    enabled: bool
    ssl_enabled: bool
    port: int
    config_path: str


class NginxError(Exception):
    """Base exception for Nginx errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Auth location block template (inserted into server blocks when auth is enabled)
# Uses regex to match only specific HostKit Auth endpoints, allowing apps to define
# their own /auth/* routes (e.g., /auth/callback for OAuth redirects)
#
# Matched endpoints:
# - /auth/signup, /auth/signin, /auth/signout
# - /auth/verify-email, /auth/resend-verification
# - /auth/forgot-password, /auth/reset-password
# - /auth/health, /auth/docs, /auth/redoc, /auth/openapi.json
# - /auth/oauth/* (Google, Apple OAuth)
# - /auth/identity/* (OAuth proxy identity verification)
# - /auth/user (profile management)
# - /auth/token/* (refresh, revoke)
# - /auth/magic-link/* (send, verify)
# - /auth/anonymous/* (signup, convert)
AUTH_LOCATION_TEMPLATE = """
    # HostKit Auth Service (specific endpoints only)
    # Other /auth/* routes pass through to the app
    location ~ ^/auth/(signup|signin|signout|verify-email|resend-verification|forgot-password|reset-password|health|docs|redoc|openapi\\.json|oauth|identity|user|token|magic-link|anonymous)(/.*)?$ {
        proxy_pass http://127.0.0.1:{{ auth_port }};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60;
    }
"""

# Payment location block template (inserted into server blocks when payments are enabled)
# Routes all /payments/* requests to the payment service
PAYMENT_LOCATION_TEMPLATE = """
    # HostKit Payment Service
    location /payments/ {
        proxy_pass http://127.0.0.1:{{ payment_port }}/payments/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60;
    }
"""

# SMS location block template (inserted into server blocks when SMS is enabled)
# Routes all /api/sms/* requests to the SMS service
SMS_LOCATION_TEMPLATE = """
    # HostKit SMS Service
    location /api/sms/ {
        proxy_pass http://127.0.0.1:{{ sms_port }}/api/sms/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60;
    }
"""

# Booking location block template (inserted into server blocks when booking is enabled)
# Routes /api/booking/* and /api/admin/* requests to the booking service
BOOKING_LOCATION_TEMPLATE = """
    # HostKit Booking Service API
    location /api/booking/ {
        proxy_pass http://127.0.0.1:{{ booking_port }}/api/booking/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60;
    }

    location /api/admin/ {
        proxy_pass http://127.0.0.1:{{ booking_port }}/api/admin/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60;
    }
"""

# Chatbot location block template (inserted into server blocks when chatbot is enabled)
# Routes /chatbot/* requests to the chatbot service with SSE support
CHATBOT_LOCATION_TEMPLATE = """
    # HostKit Chatbot Service
    location /chatbot/ {
        proxy_pass http://127.0.0.1:{{ chatbot_port }}/chatbot/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE support - disable buffering for streaming responses
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300;
        # Required for SSE
        proxy_set_header Connection '';
        chunked_transfer_encoding off;
    }
"""

# Nginx site template (HTTP only - SSL added separately after certificate provisioning)
NGINX_SITE_TEMPLATE = """# Managed by HostKit - Do not edit manually
# Project: {{ project_name }}
# Generated: {{ timestamp }}

server {
    listen 80;
    server_name {{ domains | join(' ') }};
{{ auth_location }}{{ payment_location }}{{ sms_location }}{{ booking_location }}{{ chatbot_location }}
    location / {
        proxy_pass http://127.0.0.1:{{ port }};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 86400;
    }
}
"""

# Nginx site template with SSL (after certificate is provisioned)
NGINX_SSL_SITE_TEMPLATE = """# Managed by HostKit - Do not edit manually
# Project: {{ project_name }}
# Generated: {{ timestamp }}
# SSL: enabled

# HTTPS server
server {
    listen 443 ssl http2;
    server_name {{ domains | join(' ') }};

    ssl_certificate /etc/letsencrypt/live/{{ primary_domain }}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{{ primary_domain }}/privkey.pem;

    # SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;

    # HSTS (uncomment if you're sure)
    # add_header Strict-Transport-Security "max-age=63072000" always;
{{ auth_location }}{{ payment_location }}{{ sms_location }}{{ booking_location }}{{ chatbot_location }}
    location / {
        proxy_pass http://127.0.0.1:{{ port }};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 86400;
    }
}

# HTTP to HTTPS redirect
server {
    listen 80;
    server_name {{ domains | join(' ') }};
    return 301 https://$server_name$request_uri;
}
"""


class NginxService:
    """Service for managing Nginx reverse proxy configurations."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.sites_available = Path("/etc/nginx/sites-available")
        self.sites_enabled = Path("/etc/nginx/sites-enabled")

    def _is_dev_domain(self, domain: str) -> bool:
        """Check if domain is a development domain that doesn't require DNS validation."""
        return any(domain.endswith(suffix) for suffix in DEV_DOMAIN_SUFFIXES)

    def _resolve_domain(self, domain: str) -> str | None:
        """Resolve a domain to its IP address.

        Returns the IP address or None if resolution fails.
        """
        try:
            result = socket.getaddrinfo(domain, None, socket.AF_INET)
            if result:
                return result[0][4][0]  # First IPv4 address
        except socket.gaierror:
            pass
        return None

    def _validate_domain_dns(self, domain: str) -> None:
        """Validate that a domain resolves to this VPS.

        Args:
            domain: The domain to validate

        Raises:
            NginxError: If DNS validation fails
        """
        # Skip validation for dev domains
        if self._is_dev_domain(domain):
            return

        # Resolve the domain
        resolved_ip = self._resolve_domain(domain)

        if resolved_ip is None:
            raise NginxError(
                code="DNS_RESOLUTION_FAILED",
                message=f"Domain '{domain}' could not be resolved",
                suggestion=f"Ensure the domain has an A record pointing to {self.config.vps_ip}",
            )

        if resolved_ip != self.config.vps_ip:
            raise NginxError(
                code="DNS_MISMATCH",
                message=f"Domain '{domain}' resolves to {resolved_ip}, expected {self.config.vps_ip}",
                suggestion=f"Update the domain's A record to point to {self.config.vps_ip}",
            )

    def _site_config_name(self, project: str) -> str:
        """Generate Nginx site config filename."""
        return f"hostkit-{project}"

    def _get_site_config_path(self, project: str) -> Path:
        """Get the path to a site's config file."""
        return self.sites_available / self._site_config_name(project)

    def _get_site_enabled_path(self, project: str) -> Path:
        """Get the path to a site's enabled symlink."""
        return self.sites_enabled / self._site_config_name(project)

    def _is_site_enabled(self, project: str) -> bool:
        """Check if a site is enabled (symlinked in sites-enabled)."""
        return self._get_site_enabled_path(project).exists()

    def _check_ssl_configured(self, project: str) -> bool:
        """Check if SSL is configured for a site."""
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            return False
        content = config_path.read_text()
        return "listen 443 ssl" in content

    def list_sites(self) -> list[NginxSite]:
        """List all Nginx sites managed by HostKit."""
        sites = []

        # Get all projects from database
        projects = self.db.list_projects()

        for proj in projects:
            project_name = proj["name"]
            config_path = self._get_site_config_path(project_name)

            if config_path.exists():
                # Get domains for this project
                domains = self.db.list_domains(project_name)
                domain_list = [d["domain"] for d in domains]

                sites.append(
                    NginxSite(
                        project=project_name,
                        domains=domain_list,
                        enabled=self._is_site_enabled(project_name),
                        ssl_enabled=self._check_ssl_configured(project_name),
                        port=proj["port"],
                        config_path=str(config_path),
                    )
                )

        return sites

    def get_site(self, project: str) -> NginxSite:
        """Get information about a specific site."""
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            raise NginxError(
                code="SITE_NOT_FOUND",
                message=f"No Nginx site configured for '{project}'",
                suggestion=f"Run 'hostkit nginx add {project} <domain>' to add a domain",
            )

        domains = self.db.list_domains(project)
        domain_list = [d["domain"] for d in domains]

        return NginxSite(
            project=project,
            domains=domain_list,
            enabled=self._is_site_enabled(project),
            ssl_enabled=self._check_ssl_configured(project),
            port=proj["port"],
            config_path=str(config_path),
        )

    def add_domain(self, project: str, domain: str, skip_dns: bool = False) -> dict[str, Any]:
        """Add a domain to a project's Nginx configuration."""
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if domain is already configured
        existing = self.db.get_domain(domain)
        if existing:
            if existing["project"] == project:
                raise NginxError(
                    code="DOMAIN_EXISTS",
                    message=f"Domain '{domain}' is already configured for '{project}'",
                )
            raise NginxError(
                code="DOMAIN_IN_USE",
                message=f"Domain '{domain}' is already used by project '{existing['project']}'",
                suggestion=f"Remove it first with 'hostkit nginx remove {existing['project']} {domain}'",
            )

        # Validate DNS points to this VPS (skip for dev domains or if --skip-dns)
        if not skip_dns:
            self._validate_domain_dns(domain)

        # Add domain to database
        self.db.add_domain(domain, project)

        # Regenerate Nginx config
        self._generate_site_config(project)

        # Enable site if not already enabled
        if not self._is_site_enabled(project):
            self._enable_site(project)

        # Test and reload
        self.test_config()
        self.reload()

        return {
            "project": project,
            "domain": domain,
            "enabled": True,
            "suggestion": f"Run 'hostkit ssl provision {domain}' to enable HTTPS",
        }

    def remove_domain(self, project: str, domain: str) -> dict[str, Any]:
        """Remove a domain from a project's Nginx configuration."""
        # Verify domain exists and belongs to project
        existing = self.db.get_domain(domain)
        if not existing:
            raise NginxError(
                code="DOMAIN_NOT_FOUND",
                message=f"Domain '{domain}' is not configured",
            )

        if existing["project"] != project:
            raise NginxError(
                code="DOMAIN_MISMATCH",
                message=f"Domain '{domain}' belongs to project '{existing['project']}', not '{project}'",
            )

        # Remove from database
        self.db.delete_domain(domain)

        # Check if project has remaining domains
        remaining_domains = self.db.list_domains(project)

        if remaining_domains:
            # Regenerate config with remaining domains
            self._generate_site_config(project)
        else:
            # No domains left - disable and remove site config
            self._disable_site(project)
            self._remove_site_config(project)

        # Test and reload
        self.test_config()
        self.reload()

        return {
            "project": project,
            "domain": domain,
            "removed": True,
            "remaining_domains": len(remaining_domains),
        }

    def _get_auth_port(self, project: str) -> int | None:
        """Get the auth service port if auth is enabled for project."""
        auth_service = self.db.get_auth_service(project)
        if auth_service and auth_service.get("enabled"):
            return auth_service.get("auth_port")
        return None

    def _get_payment_port(self, project: str) -> int | None:
        """Get the payment service port if payments are enabled for project."""
        # Check if payment service is enabled by checking if payment DB exists
        from hostkit.services.payment_service import PaymentService
        payment_service = PaymentService()
        if payment_service.payment_is_enabled(project):
            return payment_service._payment_port(project)
        return None

    def _get_sms_port(self, project: str) -> int | None:
        """Get SMS service port if enabled."""
        from hostkit.services.sms_service import SMSService
        sms_service = SMSService()
        if sms_service.sms_is_enabled(project):
            return sms_service._sms_port(project)
        return None

    def _get_booking_port(self, project: str) -> int | None:
        """Get booking service port if booking is enabled for project."""
        from hostkit.services.booking_service import BookingService
        booking_service = BookingService()
        if booking_service.booking_is_enabled(project):
            return booking_service._booking_port(project)
        return None

    def _get_chatbot_port(self, project: str) -> int | None:
        """Get chatbot service port if chatbot is enabled for project."""
        from hostkit.services.chatbot_service import ChatbotService
        chatbot_service = ChatbotService()
        if chatbot_service.chatbot_is_enabled(project):
            return chatbot_service._chatbot_port(project)
        return None

    def _generate_site_config(self, project: str, auth_port: int | None = None, payment_port: int | None = None, sms_port: int | None = None, booking_port: int | None = None, chatbot_port: int | None = None) -> None:
        """Generate or update Nginx site configuration for a project.

        Args:
            project: Project name
            auth_port: Optional auth service port. If None, checks database.
            payment_port: Optional payment service port. If None, checks database.
            sms_port: Optional SMS service port. If None, checks database.
            booking_port: Optional booking service port. If None, checks database.
            chatbot_port: Optional chatbot service port. If None, checks database.
        """
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
            )

        domains = self.db.list_domains(project)
        if not domains:
            return  # No domains, no config needed

        domain_list = [d["domain"] for d in domains]

        # Check if any domain has SSL provisioned
        has_ssl = any(d["ssl_provisioned"] for d in domains)

        # Check if auth is enabled (use provided port or check database)
        if auth_port is None:
            auth_port = self._get_auth_port(project)

        # Check if payments are enabled (use provided port or check database)
        if payment_port is None:
            payment_port = self._get_payment_port(project)

        # Check if SMS is enabled (use provided port or check database)
        if sms_port is None:
            sms_port = self._get_sms_port(project)

        # Check if Booking is enabled (use provided port or check database)
        if booking_port is None:
            booking_port = self._get_booking_port(project)

        # Check if Chatbot is enabled (use provided port or check database)
        if chatbot_port is None:
            chatbot_port = self._get_chatbot_port(project)

        # Generate auth location block if auth is enabled
        auth_location = ""
        if auth_port:
            auth_template = Template(AUTH_LOCATION_TEMPLATE)
            auth_location = auth_template.render(auth_port=auth_port)

        # Generate payment location block if payments are enabled
        payment_location = ""
        if payment_port:
            payment_template = Template(PAYMENT_LOCATION_TEMPLATE)
            payment_location = payment_template.render(payment_port=payment_port)

        # Generate SMS location block if SMS is enabled
        sms_location = ""
        if sms_port:
            sms_template = Template(SMS_LOCATION_TEMPLATE)
            sms_location = sms_template.render(sms_port=sms_port)

        # Generate booking location block if booking is enabled
        booking_location = ""
        if booking_port:
            booking_template = Template(BOOKING_LOCATION_TEMPLATE)
            booking_location = booking_template.render(booking_port=booking_port)

        # Generate chatbot location block if chatbot is enabled
        chatbot_location = ""
        if chatbot_port:
            chatbot_template = Template(CHATBOT_LOCATION_TEMPLATE)
            chatbot_location = chatbot_template.render(chatbot_port=chatbot_port)

        # Choose template based on SSL status
        if has_ssl:
            template = Template(NGINX_SSL_SITE_TEMPLATE)
            content = template.render(
                project_name=project,
                port=proj["port"],
                domains=domain_list,
                primary_domain=domain_list[0],  # First domain for certificate path
                timestamp=self._timestamp(),
                auth_location=auth_location,
                payment_location=payment_location,
                sms_location=sms_location,
                booking_location=booking_location,
                chatbot_location=chatbot_location,
            )
        else:
            template = Template(NGINX_SITE_TEMPLATE)
            content = template.render(
                project_name=project,
                port=proj["port"],
                domains=domain_list,
                timestamp=self._timestamp(),
                auth_location=auth_location,
                payment_location=payment_location,
                sms_location=sms_location,
                booking_location=booking_location,
                chatbot_location=chatbot_location,
            )

        # Write config
        config_path = self._get_site_config_path(project)
        config_path.write_text(content)

    def _enable_site(self, project: str) -> None:
        """Enable a site by creating symlink in sites-enabled."""
        config_path = self._get_site_config_path(project)
        enabled_path = self._get_site_enabled_path(project)

        if not config_path.exists():
            raise NginxError(
                code="CONFIG_NOT_FOUND",
                message=f"No config exists for '{project}'",
            )

        if enabled_path.exists():
            return  # Already enabled

        enabled_path.symlink_to(config_path)

    def _disable_site(self, project: str) -> None:
        """Disable a site by removing symlink from sites-enabled."""
        enabled_path = self._get_site_enabled_path(project)
        if enabled_path.exists() or enabled_path.is_symlink():
            enabled_path.unlink()

    def _remove_site_config(self, project: str) -> None:
        """Remove a site's configuration file."""
        config_path = self._get_site_config_path(project)
        if config_path.exists():
            config_path.unlink()

    def test_config(self) -> dict[str, Any]:
        """Test Nginx configuration syntax."""
        try:
            result = subprocess.run(
                ["nginx", "-t"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                return {
                    "valid": True,
                    "message": "Nginx configuration is valid",
                }
            else:
                # Parse error message
                error_msg = result.stderr or result.stdout
                raise NginxError(
                    code="CONFIG_INVALID",
                    message=f"Nginx configuration test failed: {error_msg}",
                    suggestion="Check your site configurations for syntax errors",
                )

        except subprocess.TimeoutExpired:
            raise NginxError(
                code="TEST_TIMEOUT",
                message="Nginx config test timed out",
            )

    def reload(self) -> dict[str, Any]:
        """Reload Nginx configuration (graceful)."""
        # Test first
        self.test_config()

        try:
            result = subprocess.run(
                ["systemctl", "reload", "nginx"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                return {
                    "reloaded": True,
                    "message": "Nginx configuration reloaded",
                }
            else:
                raise NginxError(
                    code="RELOAD_FAILED",
                    message=f"Failed to reload Nginx: {result.stderr or result.stdout}",
                    suggestion="Check 'systemctl status nginx' for details",
                )

        except subprocess.TimeoutExpired:
            raise NginxError(
                code="RELOAD_TIMEOUT",
                message="Nginx reload timed out",
            )

    def enable_ssl_for_project(self, project: str) -> None:
        """Regenerate site config with SSL enabled (called after certificate provisioning)."""
        # Mark all domains for this project as SSL provisioned
        domains = self.db.list_domains(project)
        for d in domains:
            self.db.update_domain_ssl(d["domain"], True)

        # Regenerate config with SSL
        self._generate_site_config(project)

        # Reload Nginx
        self.test_config()
        self.reload()

    def _timestamp(self) -> str:
        """Get current timestamp string."""
        from datetime import datetime

        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    def add_auth_location(self, project: str, auth_port: int) -> dict[str, Any]:
        """Add auth service location block to a project's Nginx configuration.

        Args:
            project: Project name
            auth_port: Port the auth service is running on

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if site config exists (project needs at least one domain)
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            # No nginx config yet - that's ok, auth will be added when domains are configured
            return {
                "project": project,
                "auth_port": auth_port,
                "configured": False,
                "message": "No Nginx site configured yet. Auth location will be added when a domain is configured.",
            }

        # Regenerate site config with auth location
        self._generate_site_config(project, auth_port=auth_port)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "auth_port": auth_port,
            "configured": True,
            "message": "Auth location added to Nginx configuration",
        }

    def remove_auth_location(self, project: str) -> dict[str, Any]:
        """Remove auth service location block from a project's Nginx configuration.

        Args:
            project: Project name

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
            )

        # Check if site config exists
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            return {
                "project": project,
                "removed": False,
                "message": "No Nginx site configured",
            }

        # Regenerate site config without auth location (auth_port=None)
        # The database record should already be removed at this point
        self._generate_site_config(project, auth_port=None)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "removed": True,
            "message": "Auth location removed from Nginx configuration",
        }

    def add_payment_location(self, project: str, payment_port: int) -> dict[str, Any]:
        """Add payment service location block to a project's Nginx configuration.

        Args:
            project: Project name
            payment_port: Port the payment service is running on

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if site config exists (project needs at least one domain)
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            # No nginx config yet - that's ok, payment will be added when domains are configured
            return {
                "project": project,
                "payment_port": payment_port,
                "configured": False,
                "message": "No Nginx site configured yet. Payment location will be added when a domain is configured.",
            }

        # Regenerate site config with payment location
        self._generate_site_config(project, payment_port=payment_port)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "payment_port": payment_port,
            "configured": True,
            "message": "Payment location added to Nginx configuration",
        }

    def remove_payment_location(self, project: str) -> dict[str, Any]:
        """Remove payment service location block from a project's Nginx configuration.

        Args:
            project: Project name

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
            )

        # Check if site config exists
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            return {
                "project": project,
                "removed": False,
                "message": "No Nginx site configured",
            }

        # Regenerate site config without payment location (payment_port=None)
        self._generate_site_config(project, payment_port=None)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "removed": True,
            "message": "Payment location removed from Nginx configuration",
        }

    def add_sms_location(self, project: str, sms_port: int) -> dict[str, Any]:
        """Add SMS service location block to a project's Nginx configuration.

        Args:
            project: Project name
            sms_port: Port the SMS service is running on

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if site config exists (project needs at least one domain)
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            # No nginx config yet - that's ok, SMS will be added when domains are configured
            return {
                "project": project,
                "sms_port": sms_port,
                "configured": False,
                "message": "No Nginx site configured yet. SMS location will be added when a domain is configured.",
            }

        # Regenerate site config with SMS location
        self._generate_site_config(project, sms_port=sms_port)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "sms_port": sms_port,
            "configured": True,
            "message": "SMS location added to Nginx configuration",
        }

    def remove_sms_location(self, project: str) -> dict[str, Any]:
        """Remove SMS service location block from a project's Nginx configuration.

        Args:
            project: Project name

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
            )

        # Check if site config exists
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            return {
                "project": project,
                "removed": False,
                "message": "No Nginx site configured",
            }

        # Regenerate site config without SMS location (sms_port=None)
        self._generate_site_config(project, sms_port=None)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "removed": True,
            "message": "SMS location removed from Nginx configuration",
        }

    def add_booking_location(self, project: str, booking_port: int) -> dict[str, Any]:
        """Add booking service location block to a project's Nginx configuration.

        Args:
            project: Project name
            booking_port: Port the booking service is running on

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if site config exists (project needs at least one domain)
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            # No nginx config yet - that's ok, booking will be added when domains are configured
            return {
                "project": project,
                "booking_port": booking_port,
                "configured": False,
                "message": "No Nginx site configured yet. Booking location will be added when a domain is configured.",
            }

        # Regenerate site config with booking location
        self._generate_site_config(project, booking_port=booking_port)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "booking_port": booking_port,
            "configured": True,
            "message": "Booking location added to Nginx configuration",
        }

    def remove_booking_location(self, project: str) -> dict[str, Any]:
        """Remove booking service location block from a project's Nginx configuration.

        Args:
            project: Project name

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
            )

        # Check if site config exists
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            return {
                "project": project,
                "removed": False,
                "message": "No Nginx site configured",
            }

        # Regenerate site config without booking location (booking_port=None)
        self._generate_site_config(project, booking_port=None)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "removed": True,
            "message": "Booking location removed from Nginx configuration",
        }

    def add_chatbot_location(self, project: str, chatbot_port: int) -> dict[str, Any]:
        """Add chatbot service location block to a project's Nginx configuration.

        Args:
            project: Project name
            chatbot_port: Port the chatbot service is running on

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if site config exists (project needs at least one domain)
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            # No nginx config yet - that's ok, chatbot will be added when domains are configured
            return {
                "project": project,
                "chatbot_port": chatbot_port,
                "configured": False,
                "message": "No Nginx site configured yet. Chatbot location will be added when a domain is configured.",
            }

        # Regenerate site config with chatbot location
        self._generate_site_config(project, chatbot_port=chatbot_port)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "chatbot_port": chatbot_port,
            "configured": True,
            "message": "Chatbot location added to Nginx configuration",
        }

    def remove_chatbot_location(self, project: str) -> dict[str, Any]:
        """Remove chatbot service location block from a project's Nginx configuration.

        Args:
            project: Project name

        Returns:
            Dictionary with operation result
        """
        # Verify project exists
        proj = self.db.get_project(project)
        if not proj:
            raise NginxError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
            )

        # Check if site config exists
        config_path = self._get_site_config_path(project)
        if not config_path.exists():
            return {
                "project": project,
                "removed": False,
                "message": "No Nginx site configured",
            }

        # Regenerate site config without chatbot location (chatbot_port=None)
        self._generate_site_config(project, chatbot_port=None)

        # Test and reload Nginx
        self.test_config()
        self.reload()

        return {
            "project": project,
            "removed": True,
            "message": "Chatbot location removed from Nginx configuration",
        }

    def update_wildcard_auth_routes(self) -> dict[str, Any]:
        """Update the auth location regex in the wildcard config to match AUTH_LOCATION_TEMPLATE.

        This ensures the wildcard config (hostkit-wildcard) has the same auth route
        patterns as per-project configs generated from AUTH_LOCATION_TEMPLATE.

        Returns:
            Dictionary with operation result including old and new patterns
        """
        import re

        wildcard_config = self.sites_enabled / "hostkit-wildcard"

        if not wildcard_config.exists():
            raise NginxError(
                code="WILDCARD_NOT_FOUND",
                message="Wildcard config not found at /etc/nginx/sites-enabled/hostkit-wildcard",
                suggestion="The wildcard config may not be set up on this VPS",
            )

        # Read current config
        content = wildcard_config.read_text()

        # Extract the current auth location regex pattern
        old_pattern_match = re.search(
            r'location ~ \^/auth/\(([^)]+)\)\(/\.\*\)\?\$',
            content
        )

        if not old_pattern_match:
            raise NginxError(
                code="PATTERN_NOT_FOUND",
                message="Could not find auth location pattern in wildcard config",
                suggestion="The wildcard config format may have changed",
            )

        old_routes = old_pattern_match.group(1)

        # Extract the new pattern from AUTH_LOCATION_TEMPLATE
        new_pattern_match = re.search(
            r'location ~ \^/auth/\(([^)]+)\)\(/\.\*\)\?\$',
            AUTH_LOCATION_TEMPLATE
        )

        if not new_pattern_match:
            raise NginxError(
                code="TEMPLATE_ERROR",
                message="Could not extract pattern from AUTH_LOCATION_TEMPLATE",
            )

        new_routes = new_pattern_match.group(1)

        if old_routes == new_routes:
            return {
                "updated": False,
                "message": "Wildcard config already has the latest auth routes",
                "routes": new_routes.split("|"),
            }

        # Replace the old pattern with the new one
        new_content = content.replace(
            f"location ~ ^/auth/({old_routes})(/.*)?$",
            f"location ~ ^/auth/({new_routes})(/.*)?$"
        )

        # Write the updated config
        wildcard_config.write_text(new_content)

        # Test and reload
        self.test_config()
        self.reload()

        return {
            "updated": True,
            "old_routes": old_routes.split("|"),
            "new_routes": new_routes.split("|"),
            "added": [r for r in new_routes.split("|") if r not in old_routes.split("|")],
            "message": "Wildcard config updated with latest auth routes",
        }
