"""DNS management for HostKit using Cloudflare API and nip.io."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml

from hostkit.config import get_config


@dataclass
class DNSRecord:
    """Information about a DNS record."""

    id: str
    name: str
    type: str
    content: str
    ttl: int
    proxied: bool
    zone_id: str
    zone_name: str


class DNSError(Exception):
    """Base exception for DNS errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class DNSService:
    """Service for managing DNS records via Cloudflare API and nip.io."""

    CONFIG_PATH = Path("/etc/hostkit/dns.yaml")
    IP_CACHE_PATH = Path("/var/lib/hostkit/vps_ip.cache")
    CLOUDFLARE_API_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self) -> None:
        self.config = get_config()
        self._cf_config: dict[str, Any] | None = None
        self._vps_ip: str | None = None

    # ─────────────────────────────────────────────────────────────────────────
    # Configuration Management
    # ─────────────────────────────────────────────────────────────────────────

    def _load_cf_config(self) -> dict[str, Any]:
        """Load Cloudflare configuration from disk."""
        if self._cf_config is not None:
            return self._cf_config

        if not self.CONFIG_PATH.exists():
            raise DNSError(
                code="NOT_CONFIGURED",
                message="DNS is not configured",
                suggestion="Run 'hostkit dns config' to configure Cloudflare credentials",
            )

        try:
            with open(self.CONFIG_PATH) as f:
                self._cf_config = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise DNSError(
                code="CONFIG_INVALID",
                message=f"Invalid DNS configuration: {e}",
            )

        return self._cf_config

    def configure(
        self,
        api_token: str,
        zone_name: str | None = None,
    ) -> dict[str, Any]:
        """Configure Cloudflare API credentials."""
        # Validate the token by making a test API call
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        try:
            # Verify token
            resp = requests.get(
                f"{self.CLOUDFLARE_API_URL}/user/tokens/verify",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            if not result.get("success"):
                raise DNSError(
                    code="INVALID_TOKEN",
                    message="Cloudflare API token is invalid",
                    suggestion="Check your token in the Cloudflare dashboard",
                )

            # If zone_name provided, look up zone_id
            zone_id = None
            if zone_name:
                zone_id = self._lookup_zone_id(api_token, zone_name)

            # Save configuration
            config_data = {
                "api_token": api_token,
                "zone_name": zone_name,
                "zone_id": zone_id,
            }

            self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CONFIG_PATH, "w") as f:
                yaml.safe_dump(config_data, f, default_flow_style=False)

            # Secure the file (readable only by root)
            self.CONFIG_PATH.chmod(0o600)

            # Clear cached config
            self._cf_config = None

            return {
                "configured": True,
                "zone_name": zone_name,
                "zone_id": zone_id,
                "message": "DNS configuration saved",
            }

        except requests.RequestException as e:
            raise DNSError(
                code="API_ERROR",
                message=f"Failed to verify Cloudflare token: {e}",
                suggestion="Check your network connection and token",
            )

    def _lookup_zone_id(self, api_token: str, zone_name: str) -> str:
        """Look up zone ID by zone name."""
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        resp = requests.get(
            f"{self.CLOUDFLARE_API_URL}/zones",
            headers=headers,
            params={"name": zone_name},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        if not result.get("success"):
            errors = result.get("errors", [])
            error_msg = errors[0].get("message") if errors else "Unknown error"
            raise DNSError(
                code="ZONE_LOOKUP_FAILED",
                message=f"Failed to look up zone: {error_msg}",
            )

        zones = result.get("result", [])
        if not zones:
            raise DNSError(
                code="ZONE_NOT_FOUND",
                message=f"Zone '{zone_name}' not found in your Cloudflare account",
                suggestion="Check the domain is added to your Cloudflare account",
            )

        return zones[0]["id"]

    def get_config_info(self) -> dict[str, Any]:
        """Get current DNS configuration (without exposing token)."""
        if not self.CONFIG_PATH.exists():
            return {
                "configured": False,
                "zone_name": None,
                "zone_id": None,
            }

        cf_config = self._load_cf_config()
        return {
            "configured": True,
            "zone_name": cf_config.get("zone_name"),
            "zone_id": cf_config.get("zone_id"),
            "config_path": str(self.CONFIG_PATH),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # VPS IP Detection
    # ─────────────────────────────────────────────────────────────────────────

    def get_vps_ip(self, force_refresh: bool = False) -> str:
        """Get the VPS public IP address, with caching."""
        if self._vps_ip and not force_refresh:
            return self._vps_ip

        # Check cache file (valid for 1 hour)
        if not force_refresh and self.IP_CACHE_PATH.exists():
            try:
                cache_data = json.loads(self.IP_CACHE_PATH.read_text())
                import time
                if time.time() - cache_data.get("timestamp", 0) < 3600:
                    self._vps_ip = cache_data["ip"]
                    return self._vps_ip
            except (json.JSONDecodeError, KeyError):
                pass

        # Fetch IP from external service
        ip = self._fetch_public_ip()
        self._vps_ip = ip

        # Cache the result
        self.IP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        import time
        self.IP_CACHE_PATH.write_text(json.dumps({
            "ip": ip,
            "timestamp": time.time(),
        }))

        return ip

    def _fetch_public_ip(self) -> str:
        """Fetch public IP from external services."""
        # Try multiple services for reliability
        services = [
            "https://api.ipify.org?format=json",
            "https://ifconfig.me/ip",
            "https://icanhazip.com",
        ]

        for service in services:
            try:
                resp = requests.get(service, timeout=10)
                resp.raise_for_status()

                if "ipify" in service:
                    return resp.json()["ip"]
                else:
                    return resp.text.strip()

            except requests.RequestException:
                continue

        raise DNSError(
            code="IP_DETECTION_FAILED",
            message="Could not detect VPS public IP",
            suggestion="Check network connectivity or specify IP manually",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Cloudflare DNS Record Operations
    # ─────────────────────────────────────────────────────────────────────────

    def _get_headers(self) -> dict[str, str]:
        """Get Cloudflare API headers."""
        cf_config = self._load_cf_config()
        return {
            "Authorization": f"Bearer {cf_config['api_token']}",
            "Content-Type": "application/json",
        }

    def _get_zone_id(self) -> str:
        """Get configured zone ID."""
        cf_config = self._load_cf_config()
        zone_id = cf_config.get("zone_id")
        if not zone_id:
            raise DNSError(
                code="ZONE_NOT_CONFIGURED",
                message="No zone configured",
                suggestion="Run 'hostkit dns config --zone <domain>' to set your domain",
            )
        return zone_id

    def _get_zone_name(self) -> str:
        """Get configured zone name."""
        cf_config = self._load_cf_config()
        zone_name = cf_config.get("zone_name")
        if not zone_name:
            raise DNSError(
                code="ZONE_NOT_CONFIGURED",
                message="No zone configured",
                suggestion="Run 'hostkit dns config --zone <domain>' to set your domain",
            )
        return zone_name

    def list_records(self, record_type: str | None = None) -> list[DNSRecord]:
        """List DNS records in the configured zone."""
        zone_id = self._get_zone_id()
        zone_name = self._get_zone_name()
        headers = self._get_headers()

        params: dict[str, Any] = {"per_page": 100}
        if record_type:
            params["type"] = record_type.upper()

        try:
            resp = requests.get(
                f"{self.CLOUDFLARE_API_URL}/zones/{zone_id}/dns_records",
                headers=headers,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            if not result.get("success"):
                errors = result.get("errors", [])
                error_msg = errors[0].get("message") if errors else "Unknown error"
                raise DNSError(
                    code="LIST_FAILED",
                    message=f"Failed to list DNS records: {error_msg}",
                )

            records = []
            for rec in result.get("result", []):
                records.append(DNSRecord(
                    id=rec["id"],
                    name=rec["name"],
                    type=rec["type"],
                    content=rec["content"],
                    ttl=rec["ttl"],
                    proxied=rec.get("proxied", False),
                    zone_id=zone_id,
                    zone_name=zone_name,
                ))

            return records

        except requests.RequestException as e:
            raise DNSError(
                code="API_ERROR",
                message=f"Failed to list DNS records: {e}",
            )

    def get_record(self, name: str, record_type: str = "A") -> DNSRecord | None:
        """Get a specific DNS record by name and type."""
        zone_name = self._get_zone_name()

        # Normalize name to FQDN
        if not name.endswith(zone_name):
            name = f"{name}.{zone_name}"

        records = self.list_records(record_type=record_type)
        for rec in records:
            if rec.name == name:
                return rec
        return None

    def add_record(
        self,
        name: str,
        content: str | None = None,
        record_type: str = "A",
        ttl: int = 300,
        proxied: bool = False,
    ) -> dict[str, Any]:
        """Add a DNS record."""
        zone_id = self._get_zone_id()
        zone_name = self._get_zone_name()
        headers = self._get_headers()

        # Default content to VPS IP for A records
        if content is None:
            if record_type.upper() == "A":
                content = self.get_vps_ip()
            else:
                raise DNSError(
                    code="CONTENT_REQUIRED",
                    message=f"Content is required for {record_type} records",
                )

        # Normalize name
        if name == "@" or name == zone_name:
            full_name = zone_name
        elif name.endswith(zone_name):
            full_name = name
        else:
            full_name = f"{name}.{zone_name}"

        # Check if record already exists
        existing = self.get_record(full_name, record_type)
        if existing:
            raise DNSError(
                code="RECORD_EXISTS",
                message=f"{record_type} record '{full_name}' already exists (points to {existing.content})",
                suggestion=f"Use 'hostkit dns remove {full_name}' first or update the existing record",
            )

        # Create the record
        try:
            resp = requests.post(
                f"{self.CLOUDFLARE_API_URL}/zones/{zone_id}/dns_records",
                headers=headers,
                json={
                    "type": record_type.upper(),
                    "name": full_name,
                    "content": content,
                    "ttl": ttl,
                    "proxied": proxied,
                },
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            if not result.get("success"):
                errors = result.get("errors", [])
                error_msg = errors[0].get("message") if errors else "Unknown error"
                raise DNSError(
                    code="CREATE_FAILED",
                    message=f"Failed to create DNS record: {error_msg}",
                )

            record_data = result.get("result", {})
            return {
                "id": record_data.get("id"),
                "name": record_data.get("name"),
                "type": record_data.get("type"),
                "content": record_data.get("content"),
                "ttl": record_data.get("ttl"),
                "proxied": record_data.get("proxied"),
                "message": f"DNS record created: {full_name} -> {content}",
            }

        except requests.RequestException as e:
            raise DNSError(
                code="API_ERROR",
                message=f"Failed to create DNS record: {e}",
            )

    def remove_record(self, name: str, record_type: str = "A") -> dict[str, Any]:
        """Remove a DNS record."""
        zone_id = self._get_zone_id()
        zone_name = self._get_zone_name()
        headers = self._get_headers()

        # Normalize name
        if not name.endswith(zone_name):
            name = f"{name}.{zone_name}"

        # Find the record
        record = self.get_record(name, record_type)
        if not record:
            raise DNSError(
                code="RECORD_NOT_FOUND",
                message=f"No {record_type} record found for '{name}'",
            )

        # Delete the record
        try:
            resp = requests.delete(
                f"{self.CLOUDFLARE_API_URL}/zones/{zone_id}/dns_records/{record.id}",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            if not result.get("success"):
                errors = result.get("errors", [])
                error_msg = errors[0].get("message") if errors else "Unknown error"
                raise DNSError(
                    code="DELETE_FAILED",
                    message=f"Failed to delete DNS record: {error_msg}",
                )

            return {
                "removed": True,
                "name": name,
                "type": record_type,
                "content": record.content,
                "message": f"DNS record removed: {name}",
            }

        except requests.RequestException as e:
            raise DNSError(
                code="API_ERROR",
                message=f"Failed to delete DNS record: {e}",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # nip.io Development Domains
    # ─────────────────────────────────────────────────────────────────────────

    def get_dev_domain(self, project_name: str) -> str:
        """Generate a nip.io development domain for a project."""
        vps_ip = self.get_vps_ip()
        return f"{project_name}.{vps_ip}.nip.io"

    def configure_dev_domain(self, project_name: str) -> dict[str, Any]:
        """Configure a nip.io domain for a project (Nginx integration)."""
        dev_domain = self.get_dev_domain(project_name)

        # Import nginx service to add the domain
        from hostkit.services.nginx_service import NginxService, NginxError
        from hostkit.database import get_db

        db = get_db()

        # Check project exists
        proj = db.get_project(project_name)
        if not proj:
            raise DNSError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Add domain to nginx
        nginx = NginxService()
        try:
            nginx.add_domain(project_name, dev_domain)
        except NginxError as e:
            if e.code == "DOMAIN_EXISTS":
                # Domain already configured, that's fine
                return {
                    "project": project_name,
                    "domain": dev_domain,
                    "configured": True,
                    "new": False,
                    "url": f"http://{dev_domain}",
                    "message": f"Development domain already configured: {dev_domain}",
                }
            raise DNSError(
                code=e.code,
                message=e.message,
                suggestion=e.suggestion,
            )

        return {
            "project": project_name,
            "domain": dev_domain,
            "configured": True,
            "new": True,
            "url": f"http://{dev_domain}",
            "message": f"Development domain configured: http://{dev_domain}",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Integration Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def add_subdomain_for_project(
        self,
        project_name: str,
        subdomain: str | None = None,
    ) -> dict[str, Any]:
        """Add a production subdomain for a project (DNS + Nginx)."""
        zone_name = self._get_zone_name()

        # Default subdomain to project name
        if subdomain is None:
            subdomain = project_name

        full_domain = f"{subdomain}.{zone_name}"

        # Add DNS record
        dns_result = self.add_record(subdomain)

        # Add to Nginx
        from hostkit.services.nginx_service import NginxService
        nginx = NginxService()
        nginx.add_domain(project_name, full_domain)

        return {
            "project": project_name,
            "domain": full_domain,
            "dns_record": dns_result,
            "nginx_configured": True,
            "message": f"Production domain configured: {full_domain}",
            "next_step": f"Run 'hostkit ssl provision {full_domain}' to enable HTTPS",
        }
