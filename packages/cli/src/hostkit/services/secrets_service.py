"""Secrets management service for HostKit.

This module provides secure storage and retrieval of project secrets,
encrypted at rest using AES-256-GCM, and magic link generation for
the secrets portal.
"""

import json
import os
import re
import secrets as py_secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt

from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta
from hostkit.services.crypto_service import CryptoService, CryptoServiceError, get_crypto
from hostkit.services.providers import (
    detect_provider_from_env_key,
    get_provider,
    get_provider_for_key,
    validate_secret,
)

# Register with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="secrets",
        description="Encrypted secrets portal for third-party API keys",
        provision_flag="--with-secrets",
        enable_command="hostkit secrets define {project} --from .env.example",
        env_vars_provided=[],
        related_commands=["secrets define", "secrets portal", "secrets verify"],
    )
)


# Constants
SECRETS_DIR = Path("/var/lib/hostkit/secrets")
PORTAL_BASE_URL = "https://secrets.hostkit.dev"
DEFAULT_TOKEN_EXPIRY_HOURS = 24
MAX_SECRETS_PER_PROJECT = 50


@dataclass
class SecretMetadata:
    """Metadata for a secret key."""

    key: str
    required: bool = True
    provider: str | None = None
    description: str | None = None
    set_at: str | None = None
    length: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "required": self.required,
            "provider": self.provider,
            "description": self.description,
            "set_at": self.set_at,
            "length": self.length,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SecretMetadata":
        return cls(
            key=data["key"],
            required=data.get("required", True),
            provider=data.get("provider"),
            description=data.get("description"),
            set_at=data.get("set_at"),
            length=data.get("length"),
        )


@dataclass
class ProjectSecrets:
    """Container for a project's secrets and metadata."""

    project: str
    secrets: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, SecretMetadata] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "secrets": self.secrets,
            "metadata": {k: v.to_dict() for k, v in self.metadata.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectSecrets":
        return cls(
            project=data["project"],
            secrets=data.get("secrets", {}),
            metadata={k: SecretMetadata.from_dict(v) for k, v in data.get("metadata", {}).items()},
        )


class SecretsServiceError(Exception):
    """Exception for secrets service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


@dataclass
class MagicLinkToken:
    """Decoded magic link token data."""

    project: str
    jti: str
    scope: str
    issued_at: datetime
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "jti": self.jti,
            "scope": self.scope,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "is_expired": self.is_expired,
        }


class SecretsService:
    """Service for managing encrypted project secrets."""

    def __init__(
        self,
        secrets_dir: Path | None = None,
        crypto: CryptoService | None = None,
    ) -> None:
        """Initialize the secrets service.

        Args:
            secrets_dir: Directory for storing secrets (default: /var/lib/hostkit/secrets)
            crypto: CryptoService instance (default: global instance)
        """
        self.secrets_dir = secrets_dir or SECRETS_DIR
        self.crypto = crypto or get_crypto()
        self.db = get_db()

    def _ensure_secrets_dir(self) -> None:
        """Ensure secrets directory exists with proper permissions."""
        if not self.secrets_dir.exists():
            self.secrets_dir.mkdir(parents=True, mode=0o700)
        else:
            # Ensure permissions are secure
            mode = self.secrets_dir.stat().st_mode & 0o777
            if mode != 0o700:
                os.chmod(self.secrets_dir, 0o700)

    def _secrets_file(self, project: str) -> Path:
        """Get path to project's encrypted secrets file."""
        return self.secrets_dir / f"{project}.enc"

    def _meta_file(self, project: str) -> Path:
        """Get path to project's metadata file."""
        return self.secrets_dir / f"{project}.meta"

    def _audit_file(self, project: str) -> Path:
        """Get path to project's audit log file."""
        return self.secrets_dir / f"{project}.audit"

    def _validate_project(self, project: str) -> None:
        """Validate that the project exists."""
        if not self.db.get_project(project):
            raise SecretsServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

    def _validate_key_name(self, key: str) -> None:
        """Validate secret key name format."""
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise SecretsServiceError(
                code="INVALID_KEY_NAME",
                message=f"Invalid secret key name: {key}",
                suggestion="Key names must start with a letter or underscore, "
                "and contain only letters, numbers, and underscores",
            )

    def _load_secrets(self, project: str) -> ProjectSecrets:
        """Load encrypted secrets for a project.

        Args:
            project: Project name

        Returns:
            ProjectSecrets containing decrypted secrets and metadata
        """
        secrets_file = self._secrets_file(project)
        meta_file = self._meta_file(project)

        secrets_data: dict[str, str] = {}
        metadata: dict[str, SecretMetadata] = {}

        # Load encrypted secrets
        if secrets_file.exists():
            try:
                encrypted_data = secrets_file.read_bytes()
                decrypted = self.crypto.decrypt_string(encrypted_data, context=project)
                secrets_data = json.loads(decrypted)
            except CryptoServiceError:
                raise
            except Exception as e:
                raise SecretsServiceError(
                    code="SECRETS_LOAD_ERROR",
                    message=f"Failed to load secrets for '{project}': {e}",
                ) from e

        # Load metadata
        if meta_file.exists():
            try:
                meta_content = meta_file.read_text()
                meta_dict = json.loads(meta_content)
                metadata = {k: SecretMetadata.from_dict(v) for k, v in meta_dict.items()}
            except Exception as e:
                raise SecretsServiceError(
                    code="METADATA_LOAD_ERROR",
                    message=f"Failed to load metadata for '{project}': {e}",
                ) from e

        return ProjectSecrets(project=project, secrets=secrets_data, metadata=metadata)

    def _save_secrets(self, project_secrets: ProjectSecrets) -> None:
        """Save encrypted secrets for a project.

        Args:
            project_secrets: ProjectSecrets to save
        """
        self._ensure_secrets_dir()

        project = project_secrets.project
        secrets_file = self._secrets_file(project)
        meta_file = self._meta_file(project)

        # Encrypt and save secrets
        secrets_json = json.dumps(project_secrets.secrets)
        encrypted = self.crypto.encrypt_string(secrets_json, context=project)
        secrets_file.write_bytes(encrypted)
        os.chmod(secrets_file, 0o600)

        # Save metadata (unencrypted - only contains key names, not values)
        meta_dict = {k: v.to_dict() for k, v in project_secrets.metadata.items()}
        meta_file.write_text(json.dumps(meta_dict, indent=2))
        os.chmod(meta_file, 0o600)

    def _audit_log(
        self,
        project: str,
        action: str,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Write an audit log entry.

        Args:
            project: Project name
            action: Action performed (e.g., "secret.set", "secret.deleted")
            details: Optional additional details
            ip_address: Optional IP address (for portal access)
            user_agent: Optional user agent (for portal access)
        """
        self._ensure_secrets_dir()
        audit_file = self._audit_file(project)

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": action,
            "details": details or {},
        }
        if ip_address:
            entry["ip_address"] = ip_address
        if user_agent:
            entry["user_agent"] = user_agent

        # Append to audit log
        with open(audit_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        os.chmod(audit_file, 0o600)

    def init_master_key(self, force: bool = False) -> dict[str, Any]:
        """Initialize the master encryption key.

        Args:
            force: If True, regenerate existing key (WARNING: breaks existing secrets)

        Returns:
            Dict with status information
        """
        try:
            path = self.crypto.generate_master_key(force=force)
            return {
                "path": str(path),
                "action": "regenerated" if force else "created",
            }
        except CryptoServiceError:
            raise

    def master_key_exists(self) -> bool:
        """Check if master key exists."""
        return self.crypto.master_key_exists()

    def list_secrets(self, project: str) -> list[dict[str, Any]]:
        """List secrets for a project (keys and metadata only, no values).

        Args:
            project: Project name

        Returns:
            List of dicts with key name, status, and metadata
        """
        self._validate_project(project)

        try:
            project_secrets = self._load_secrets(project)
        except CryptoServiceError:
            raise
        except SecretsServiceError:
            # No secrets yet
            return []

        result = []
        # Get all keys from both secrets and metadata
        all_keys = set(project_secrets.secrets.keys()) | set(project_secrets.metadata.keys())

        for key in sorted(all_keys):
            is_set = key in project_secrets.secrets
            value_length = len(project_secrets.secrets[key]) if is_set else None
            meta = project_secrets.metadata.get(key, SecretMetadata(key=key))

            result.append(
                {
                    "key": key,
                    "set": is_set,
                    "length": value_length,
                    "required": meta.required,
                    "provider": meta.provider,
                    "description": meta.description,
                    "set_at": meta.set_at,
                }
            )

        return result

    def get_secret(self, project: str, key: str) -> str | None:
        """Get a secret value.

        Args:
            project: Project name
            key: Secret key

        Returns:
            Secret value, or None if not set
        """
        self._validate_project(project)

        try:
            project_secrets = self._load_secrets(project)
            return project_secrets.secrets.get(key)
        except SecretsServiceError:
            return None

    def set_secret(
        self,
        project: str,
        key: str,
        value: str,
        required: bool = True,
        provider: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Set a secret value.

        Args:
            project: Project name
            key: Secret key
            value: Secret value
            required: Whether this secret is required
            provider: Optional provider name (e.g., "stripe", "google_oauth")
            description: Optional description

        Returns:
            Dict with operation result
        """
        self._validate_project(project)
        self._validate_key_name(key)

        # Load existing secrets
        try:
            project_secrets = self._load_secrets(project)
        except SecretsServiceError:
            # No secrets yet, create new
            project_secrets = ProjectSecrets(project=project)

        # Check if this is a new secret and if we've hit the limit
        was_set = key in project_secrets.secrets
        if not was_set and len(project_secrets.secrets) >= MAX_SECRETS_PER_PROJECT:
            raise SecretsServiceError(
                code="SECRETS_LIMIT_EXCEEDED",
                message=(
                    f"Maximum secrets ({MAX_SECRETS_PER_PROJECT}) reached for project '{project}'"
                ),
                suggestion=(
                    "Remove unused secrets before adding new ones with 'hostkit secrets delete'"
                ),
            )

        # Update secret
        project_secrets.secrets[key] = value

        # Update metadata
        now = datetime.utcnow().isoformat() + "Z"
        if key in project_secrets.metadata:
            project_secrets.metadata[key].set_at = now
            project_secrets.metadata[key].length = len(value)
            if provider is not None:
                project_secrets.metadata[key].provider = provider
            if description is not None:
                project_secrets.metadata[key].description = description
        else:
            project_secrets.metadata[key] = SecretMetadata(
                key=key,
                required=required,
                provider=provider,
                description=description,
                set_at=now,
                length=len(value),
            )

        # Save
        self._save_secrets(project_secrets)

        # Audit log
        self._audit_log(
            project=project,
            action="secret.updated" if was_set else "secret.created",
            details={"key": key, "length": len(value)},
        )

        return {
            "project": project,
            "key": key,
            "action": "updated" if was_set else "created",
            "length": len(value),
        }

    def delete_secret(self, project: str, key: str) -> dict[str, Any]:
        """Delete a secret.

        Args:
            project: Project name
            key: Secret key

        Returns:
            Dict with operation result
        """
        self._validate_project(project)

        try:
            project_secrets = self._load_secrets(project)
        except SecretsServiceError:
            raise SecretsServiceError(
                code="SECRET_NOT_FOUND",
                message=f"No secrets found for project '{project}'",
            )

        if key not in project_secrets.secrets:
            raise SecretsServiceError(
                code="SECRET_NOT_FOUND",
                message=f"Secret '{key}' not found in project '{project}'",
                suggestion=f"Run 'hostkit secrets list {project}' to see available secrets",
            )

        # Remove secret value but keep metadata (marks as "defined but not set")
        del project_secrets.secrets[key]
        if key in project_secrets.metadata:
            project_secrets.metadata[key].set_at = None
            project_secrets.metadata[key].length = None

        # Save
        self._save_secrets(project_secrets)

        # Audit log
        self._audit_log(
            project=project,
            action="secret.deleted",
            details={"key": key},
        )

        return {
            "project": project,
            "key": key,
            "action": "deleted",
        }

    def import_secrets(
        self,
        project: str,
        secrets: dict[str, str],
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """Import multiple secrets from a dictionary.

        Args:
            project: Project name
            secrets: Dict of key-value pairs
            overwrite: If True, overwrite existing secrets; if False, skip existing

        Returns:
            Dict with import results
        """
        self._validate_project(project)

        # Validate all keys first
        for key in secrets:
            self._validate_key_name(key)

        # Load existing secrets
        try:
            project_secrets = self._load_secrets(project)
        except SecretsServiceError:
            project_secrets = ProjectSecrets(project=project)

        created = []
        updated = []
        skipped = []
        now = datetime.utcnow().isoformat() + "Z"

        for key, value in secrets.items():
            if key in project_secrets.secrets:
                if overwrite:
                    project_secrets.secrets[key] = value
                    if key in project_secrets.metadata:
                        project_secrets.metadata[key].set_at = now
                        project_secrets.metadata[key].length = len(value)
                    else:
                        project_secrets.metadata[key] = SecretMetadata(
                            key=key, set_at=now, length=len(value)
                        )
                    updated.append(key)
                else:
                    skipped.append(key)
            else:
                project_secrets.secrets[key] = value
                project_secrets.metadata[key] = SecretMetadata(
                    key=key, set_at=now, length=len(value)
                )
                created.append(key)

        # Save
        self._save_secrets(project_secrets)

        # Audit log
        self._audit_log(
            project=project,
            action="secrets.imported",
            details={
                "created": created,
                "updated": updated,
                "skipped": skipped,
            },
        )

        return {
            "project": project,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "total_imported": len(created) + len(updated),
        }

    def get_all_secrets(self, project: str) -> dict[str, str]:
        """Get all secrets for a project (for injection into .env).

        Args:
            project: Project name

        Returns:
            Dict of all secret key-value pairs
        """
        self._validate_project(project)

        try:
            project_secrets = self._load_secrets(project)
            return project_secrets.secrets.copy()
        except SecretsServiceError:
            return {}

    def delete_all_secrets(self, project: str) -> dict[str, Any]:
        """Delete all secrets for a project.

        Args:
            project: Project name

        Returns:
            Dict with operation result
        """
        self._validate_project(project)

        secrets_file = self._secrets_file(project)
        meta_file = self._meta_file(project)

        deleted_files = []
        if secrets_file.exists():
            secrets_file.unlink()
            deleted_files.append(str(secrets_file))
        if meta_file.exists():
            meta_file.unlink()
            deleted_files.append(str(meta_file))

        # Audit log
        self._audit_log(
            project=project,
            action="secrets.deleted_all",
        )

        return {
            "project": project,
            "action": "deleted_all",
            "files_removed": deleted_files,
        }

    def clear_definitions(
        self,
        project: str,
        keep_values: bool = True,
    ) -> dict[str, Any]:
        """Clear all secret definitions (metadata) for a project.

        This removes all defined secrets from the portal, allowing you to
        re-define them from a new .env.example file.

        Args:
            project: Project name
            keep_values: If True, preserve existing secret values (default).
                        If False, also delete all secret values.

        Returns:
            Dict with operation result including:
            - cleared_definitions: list of cleared definition keys
            - preserved_values: list of keys with preserved values (if keep_values=True)
            - deleted_values: list of keys with deleted values (if keep_values=False)
        """
        self._validate_project(project)

        meta_file = self._meta_file(project)
        secrets_file = self._secrets_file(project)

        cleared_definitions: list[str] = []
        preserved_values: list[str] = []
        deleted_values: list[str] = []

        # Load existing data
        try:
            project_secrets = self._load_secrets(project)
            cleared_definitions = list(project_secrets.metadata.keys())

            if keep_values:
                # Preserve secret values, clear only metadata
                preserved_values = list(project_secrets.secrets.keys())

                # Clear metadata
                project_secrets.metadata = {}

                # Re-create minimal metadata for existing secrets
                for key in project_secrets.secrets:
                    project_secrets.metadata[key] = SecretMetadata(
                        key=key,
                        required=True,
                        set_at=datetime.utcnow().isoformat() + "Z",
                        length=len(project_secrets.secrets[key]),
                    )

                # Save updated data
                self._save_secrets(project_secrets)
            else:
                # Delete everything
                deleted_values = list(project_secrets.secrets.keys())
                if meta_file.exists():
                    meta_file.unlink()
                if secrets_file.exists():
                    secrets_file.unlink()

        except SecretsServiceError:
            # No secrets exist yet, just ensure files are removed
            if meta_file.exists():
                meta_file.unlink()
            if secrets_file.exists():
                secrets_file.unlink()

        # Audit log
        self._audit_log(
            project=project,
            action="secrets.definitions_cleared",
            details={
                "cleared_definitions": cleared_definitions,
                "preserved_values": preserved_values,
                "deleted_values": deleted_values,
                "keep_values": keep_values,
            },
        )

        return {
            "project": project,
            "action": "definitions_cleared",
            "cleared_definitions": cleared_definitions,
            "preserved_values": preserved_values,
            "deleted_values": deleted_values,
            "keep_values": keep_values,
        }

    def get_audit_log(
        self,
        project: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get audit log entries for a project.

        Args:
            project: Project name
            limit: Maximum number of entries to return

        Returns:
            List of audit log entries (most recent first)
        """
        self._validate_project(project)

        audit_file = self._audit_file(project)
        if not audit_file.exists():
            return []

        entries = []
        with open(audit_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Return most recent first
        entries.reverse()
        return entries[:limit]

    # -------------------------------------------------------------------------
    # Define & Verify Methods
    # -------------------------------------------------------------------------

    def undefine_secret(
        self,
        project: str,
        key: str,
        delete_value: bool = False,
    ) -> dict[str, Any]:
        """Remove a secret definition from the portal.

        This removes the secret from showing in the portal. By default,
        if the secret has a value set, it will be preserved (just hidden
        from the portal). Use delete_value=True to also delete the value.

        Args:
            project: Project name
            key: Secret key to undefine
            delete_value: If True, also delete the secret value

        Returns:
            Dict with operation result
        """
        self._validate_project(project)

        try:
            project_secrets = self._load_secrets(project)
        except SecretsServiceError:
            raise SecretsServiceError(
                code="SECRET_NOT_FOUND",
                message=f"No secrets found for project '{project}'",
            )

        # Check if defined
        if key not in project_secrets.metadata:
            raise SecretsServiceError(
                code="SECRET_NOT_DEFINED",
                message=f"Secret '{key}' is not defined for project '{project}'",
                suggestion=f"Run 'hostkit secrets list {project}' to see defined secrets",
            )

        had_value = key in project_secrets.secrets
        value_deleted = False

        # Remove from metadata (definition)
        del project_secrets.metadata[key]

        # Optionally remove value
        if delete_value and key in project_secrets.secrets:
            del project_secrets.secrets[key]
            value_deleted = True

        # Save
        self._save_secrets(project_secrets)

        # Audit log
        self._audit_log(
            project=project,
            action="secret.undefined",
            details={
                "key": key,
                "had_value": had_value,
                "value_deleted": value_deleted,
            },
        )

        return {
            "project": project,
            "key": key,
            "action": "undefined",
            "had_value": had_value,
            "value_deleted": value_deleted,
            "value_preserved": had_value and not value_deleted,
        }

    def define_secret(
        self,
        project: str,
        key: str,
        required: bool = True,
        provider: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Define a secret requirement (without setting a value).

        This defines what secrets a project needs, allowing verification
        that all required secrets are set before deployment.

        Args:
            project: Project name
            key: Secret key name
            required: Whether this secret is required
            provider: Optional provider ID (e.g., "stripe", "google_oauth")
            description: Optional description

        Returns:
            Dict with operation result
        """
        self._validate_project(project)
        self._validate_key_name(key)

        # Auto-detect provider if not specified
        if provider is None:
            provider = detect_provider_from_env_key(key)

        # Load existing secrets
        try:
            project_secrets = self._load_secrets(project)
        except SecretsServiceError:
            project_secrets = ProjectSecrets(project=project)

        # Check if already defined
        was_defined = key in project_secrets.metadata

        # Update or create metadata
        if key in project_secrets.metadata:
            project_secrets.metadata[key].required = required
            if provider is not None:
                project_secrets.metadata[key].provider = provider
            if description is not None:
                project_secrets.metadata[key].description = description
        else:
            project_secrets.metadata[key] = SecretMetadata(
                key=key,
                required=required,
                provider=provider,
                description=description,
            )

        # Save
        self._save_secrets(project_secrets)

        # Audit log
        self._audit_log(
            project=project,
            action="secret.defined",
            details={
                "key": key,
                "required": required,
                "provider": provider,
            },
        )

        return {
            "project": project,
            "key": key,
            "action": "updated" if was_defined else "defined",
            "required": required,
            "provider": provider,
            "description": description,
        }

    def define_secrets_from_env(
        self,
        project: str,
        env_content: str,
    ) -> dict[str, Any]:
        """Define secrets from a .env.example file.

        Parses comments to determine required/optional status and descriptions.

        Format supported:
        - `KEY=` or `KEY=value` - Defines a secret
        - `# Required - ...` comment before key marks as required
        - `# Optional - ...` comment before key marks as optional
        - `# description` comment before key sets description
        - Lines starting with `# Auto-generated` marks all following keys
          as auto-generated (skipped) until an empty line or new section

        Args:
            project: Project name
            env_content: Content of .env.example file

        Returns:
            Dict with operation results
        """
        self._validate_project(project)

        defined = []
        skipped = []
        auto_generated = []

        lines = env_content.splitlines()
        pending_comment: str | None = None
        pending_required: bool | None = None
        in_auto_block = False  # Track if we're in an auto-generated block

        for line in lines:
            line_stripped = line.strip()

            # Empty lines end the current block
            if not line_stripped:
                pending_comment = None
                pending_required = None
                in_auto_block = False  # Exit auto-generated block on empty line
                continue

            # Handle comments
            if line_stripped.startswith("#"):
                comment = line_stripped[1:].strip()

                # Check for auto-generated marker - starts a block
                if comment.lower().startswith("auto-generated"):
                    in_auto_block = True
                    pending_comment = None
                    pending_required = None
                    continue

                # Check for required/optional markers - ends auto block
                if comment.lower().startswith("required"):
                    in_auto_block = False
                    pending_required = True
                    # Extract description after "Required - " or "Required:"
                    desc_match = re.match(r"required\s*[-:]\s*(.+)", comment, re.IGNORECASE)
                    if desc_match:
                        pending_comment = desc_match.group(1).strip()
                    else:
                        pending_comment = None
                    continue
                elif comment.lower().startswith("optional"):
                    in_auto_block = False
                    pending_required = False
                    desc_match = re.match(r"optional\s*[-:]\s*(.+)", comment, re.IGNORECASE)
                    if desc_match:
                        pending_comment = desc_match.group(1).strip()
                    else:
                        pending_comment = None
                    continue

                # Regular comment becomes description (if not in auto block)
                if not in_auto_block and pending_comment is None:
                    pending_comment = comment

                continue

            # Handle KEY=VALUE lines
            if "=" in line_stripped:
                key = line_stripped.split("=", 1)[0].strip()

                # Skip export prefix
                if key.startswith("export "):
                    key = key[7:].strip()

                # Validate key name
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                    continue

                # Skip auto-generated keys (in auto block)
                if in_auto_block:
                    auto_generated.append(key)
                    continue

                # Determine required status (default: required unless marked optional)
                required = pending_required if pending_required is not None else True

                # Auto-detect provider
                provider = detect_provider_from_env_key(key)

                # Define the secret
                try:
                    self.define_secret(
                        project=project,
                        key=key,
                        required=required,
                        provider=provider,
                        description=pending_comment,
                    )
                    defined.append(
                        {
                            "key": key,
                            "required": required,
                            "provider": provider,
                            "description": pending_comment,
                        }
                    )
                except SecretsServiceError:
                    skipped.append(key)

                # Reset description for next key (but not required status)
                pending_comment = None

        return {
            "project": project,
            "defined": defined,
            "skipped": skipped,
            "auto_generated": auto_generated,
            "total_defined": len(defined),
        }

    def verify_secrets(self, project: str) -> dict[str, Any]:
        """Verify all required secrets are set and validate formats.

        Args:
            project: Project name

        Returns:
            Dict with verification results including:
            - ready: bool - True if all required secrets are set
            - required_count: int - Number of required secrets
            - required_set: int - Number of required secrets that are set
            - optional_count: int - Number of optional secrets
            - optional_set: int - Number of optional secrets that are set
            - secrets: list - Details for each secret
        """
        self._validate_project(project)

        try:
            project_secrets = self._load_secrets(project)
        except SecretsServiceError:
            return {
                "project": project,
                "ready": True,  # No secrets defined = nothing required
                "required_count": 0,
                "required_set": 0,
                "optional_count": 0,
                "optional_set": 0,
                "secrets": [],
            }

        secrets_list = []
        required_count = 0
        required_set = 0
        optional_count = 0
        optional_set = 0
        has_warnings = False

        # Get all keys from metadata (defined secrets)
        all_keys = set(project_secrets.metadata.keys())

        for key in sorted(all_keys):
            meta = project_secrets.metadata.get(key, SecretMetadata(key=key))
            is_set = key in project_secrets.secrets
            value = project_secrets.secrets.get(key)

            # Track counts
            if meta.required:
                required_count += 1
                if is_set:
                    required_set += 1
            else:
                optional_count += 1
                if is_set:
                    optional_set += 1

            # Build secret info
            secret_info: dict[str, Any] = {
                "key": key,
                "required": meta.required,
                "set": is_set,
                "length": len(value) if value else None,
                "provider": meta.provider,
                "description": meta.description,
            }

            # Validate format if value is set
            if is_set and value:
                validation = validate_secret(key, value)
                secret_info["format_valid"] = validation["format_valid"]
                secret_info["key_type"] = validation["key_type"]
                secret_info["warnings"] = validation["warnings"]

                if validation["warnings"]:
                    has_warnings = True

                # Add provider info if available
                provider = get_provider_for_key(key)
                if provider:
                    secret_info["provider_name"] = provider.name
                    secret_info["provider_url"] = provider.url
            else:
                secret_info["format_valid"] = None
                secret_info["key_type"] = None
                secret_info["warnings"] = []

            secrets_list.append(secret_info)

        # Determine if ready for deployment
        ready = required_set == required_count

        return {
            "project": project,
            "ready": ready,
            "required_count": required_count,
            "required_set": required_set,
            "optional_count": optional_count,
            "optional_set": optional_set,
            "has_warnings": has_warnings,
            "secrets": secrets_list,
        }

    def get_provider_info(self, provider_id: str) -> dict[str, Any] | None:
        """Get provider information.

        Args:
            provider_id: Provider ID (e.g., "stripe", "google_oauth")

        Returns:
            Provider info dict or None if not found
        """
        provider = get_provider(provider_id)
        if not provider:
            return None
        return provider.to_dict()

    def get_provider_for_key(self, key: str) -> dict[str, Any] | None:
        """Get provider information for a secret key.

        Args:
            key: Secret key name

        Returns:
            Provider info dict or None if not found
        """
        provider = get_provider_for_key(key)
        if not provider:
            return None
        return provider.to_dict()

    def inject_secrets(self, project: str) -> dict[str, Any]:
        """Inject secrets into a project's .env file.

        This merges encrypted secrets into the project's .env file,
        preserving any existing auto-generated values (DATABASE_URL, etc.).

        Args:
            project: Project name

        Returns:
            Dict with injection results:
            - project: str
            - injected: list of keys injected
            - skipped: list of keys already in .env
            - total_injected: int
        """
        from hostkit.services.env_service import EnvService

        self._validate_project(project)

        # Get all encrypted secrets
        secrets = self.get_all_secrets(project)
        if not secrets:
            return {
                "project": project,
                "injected": [],
                "skipped": [],
                "total_injected": 0,
            }

        # Read existing .env file
        env_service = EnvService()
        existing_vars = env_service._read_env_file(project)

        injected = []
        skipped = []

        # Inject secrets (overwrite existing values from secrets vault)
        for key, value in secrets.items():
            if key in existing_vars:
                # Update existing value if it differs
                if existing_vars[key] != value:
                    existing_vars[key] = value
                    injected.append(key)
                else:
                    skipped.append(key)
            else:
                existing_vars[key] = value
                injected.append(key)

        # Write back to .env file
        if injected:
            env_service._write_env_file(project, existing_vars)

        # Audit log
        self._audit_log(
            project=project,
            action="secrets.injected",
            details={
                "injected": injected,
                "skipped": skipped,
            },
        )

        return {
            "project": project,
            "injected": injected,
            "skipped": skipped,
            "total_injected": len(injected),
        }

    # -------------------------------------------------------------------------
    # Magic Link Methods
    # -------------------------------------------------------------------------

    def _revoked_tokens_file(self, project: str) -> Path:
        """Get path to project's revoked tokens file."""
        return self.secrets_dir / f"{project}.revoked"

    def _is_token_revoked(self, project: str, jti: str) -> bool:
        """Check if a token JTI has been revoked.

        Args:
            project: Project name
            jti: Token ID to check

        Returns:
            True if token is revoked
        """
        revoked_file = self._revoked_tokens_file(project)
        if not revoked_file.exists():
            return False

        try:
            revoked_data = json.loads(revoked_file.read_text())
            revoked_jtis = revoked_data.get("revoked_jtis", [])
            return jti in revoked_jtis
        except (json.JSONDecodeError, OSError):
            return False

    def _add_revoked_token(self, project: str, jti: str) -> None:
        """Add a token JTI to the revocation list.

        Args:
            project: Project name
            jti: Token ID to revoke
        """
        self._ensure_secrets_dir()
        revoked_file = self._revoked_tokens_file(project)

        revoked_data: dict[str, Any] = {"revoked_jtis": [], "revoked_at": []}
        if revoked_file.exists():
            try:
                revoked_data = json.loads(revoked_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        if jti not in revoked_data.get("revoked_jtis", []):
            revoked_data.setdefault("revoked_jtis", []).append(jti)
            revoked_data.setdefault("revoked_at", []).append(datetime.now(UTC).isoformat())

        revoked_file.write_text(json.dumps(revoked_data, indent=2))
        os.chmod(revoked_file, 0o600)

    def _clear_revoked_tokens(self, project: str) -> int:
        """Clear all revoked tokens for a project (used when revoking all).

        Args:
            project: Project name

        Returns:
            Number of tokens that were in the revocation list
        """
        revoked_file = self._revoked_tokens_file(project)
        count = 0
        if revoked_file.exists():
            try:
                revoked_data = json.loads(revoked_file.read_text())
                count = len(revoked_data.get("revoked_jtis", []))
            except (json.JSONDecodeError, OSError):
                pass
            revoked_file.unlink()
        return count

    def generate_magic_link(
        self,
        project: str,
        expires_hours: int = DEFAULT_TOKEN_EXPIRY_HOURS,
        scope: str = "secrets:write",
    ) -> dict[str, Any]:
        """Generate a magic link for portal access.

        Args:
            project: Project name
            expires_hours: Hours until token expires (default: 24)
            scope: Token scope (default: "secrets:write")

        Returns:
            Dict with portal_url, magic_link, token, and expiration info
        """
        self._validate_project(project)

        # Get master key for signing
        master_key = self.crypto._ensure_master_key()

        # Generate unique token ID
        jti = py_secrets.token_urlsafe(16)

        # Calculate timestamps
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=expires_hours)

        # Create JWT payload
        payload = {
            "project": project,
            "jti": jti,
            "scope": scope,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }

        # Sign with master key using HS256
        token = jwt.encode(payload, master_key, algorithm="HS256")

        # Build URLs
        portal_url = f"{PORTAL_BASE_URL}/p/{project}"
        magic_link = f"{portal_url}?token={token}"

        # Audit log
        self._audit_log(
            project=project,
            action="magic_link.generated",
            details={
                "jti": jti,
                "expires_at": expires_at.isoformat(),
                "expires_hours": expires_hours,
            },
        )

        return {
            "project": project,
            "portal_url": portal_url,
            "magic_link": magic_link,
            "token": token,
            "jti": jti,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "expires_in_hours": expires_hours,
        }

    def validate_magic_link(self, token: str) -> MagicLinkToken:
        """Validate a magic link token.

        Args:
            token: JWT token string

        Returns:
            MagicLinkToken with decoded data

        Raises:
            SecretsServiceError: If token is invalid, expired, or revoked
        """
        # Get master key for verification
        try:
            master_key = self.crypto._ensure_master_key()
        except CryptoServiceError as e:
            raise SecretsServiceError(
                code="TOKEN_VALIDATION_ERROR",
                message="Cannot validate token: master key not available",
                suggestion=str(e.suggestion) if e.suggestion else None,
            ) from e

        # Decode and verify JWT
        try:
            payload = jwt.decode(token, master_key, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise SecretsServiceError(
                code="TOKEN_EXPIRED",
                message="Magic link has expired",
                suggestion="Generate a new magic link with 'hostkit secrets portal <project>'",
            )
        except jwt.InvalidTokenError as e:
            raise SecretsServiceError(
                code="TOKEN_INVALID",
                message=f"Invalid magic link token: {e}",
                suggestion="Ensure you're using the complete magic link URL",
            )

        # Extract fields
        project = payload.get("project")
        jti = payload.get("jti")
        scope = payload.get("scope", "secrets:write")
        iat = payload.get("iat")
        exp = payload.get("exp")

        if not project or not jti:
            raise SecretsServiceError(
                code="TOKEN_INVALID",
                message="Magic link token is missing required fields",
            )

        # Check if token has been revoked (by JTI)
        if self._is_token_revoked(project, jti):
            raise SecretsServiceError(
                code="TOKEN_REVOKED",
                message="This magic link has been revoked",
                suggestion="Generate a new magic link with 'hostkit secrets portal <project>'",
            )

        # Check if token was issued before a "revoke all" operation
        issued_at = datetime.fromtimestamp(iat, tz=UTC) if iat else None
        if issued_at and self._is_token_revoked_by_timestamp(project, issued_at):
            raise SecretsServiceError(
                code="TOKEN_REVOKED",
                message="This magic link has been revoked",
                suggestion="Generate a new magic link with 'hostkit secrets portal <project>'",
            )

        # Build MagicLinkToken
        return MagicLinkToken(
            project=project,
            jti=jti,
            scope=scope,
            issued_at=datetime.fromtimestamp(iat, tz=UTC) if iat else datetime.now(UTC),
            expires_at=datetime.fromtimestamp(exp, tz=UTC) if exp else datetime.now(UTC),
        )

    def revoke_magic_links(self, project: str) -> dict[str, Any]:
        """Revoke all magic links for a project.

        This works by recording a revocation timestamp. Any tokens issued
        before this timestamp will be rejected on validation.

        Args:
            project: Project name

        Returns:
            Dict with revocation result
        """
        self._validate_project(project)
        self._ensure_secrets_dir()

        revoked_file = self._revoked_tokens_file(project)

        # Store revocation timestamp - all tokens issued before this are invalid
        revocation_data = {
            "revoked_all_at": datetime.now(UTC).isoformat(),
            "revoked_jtis": [],  # Clear individual revocations
        }

        revoked_file.write_text(json.dumps(revocation_data, indent=2))
        os.chmod(revoked_file, 0o600)

        # Audit log
        self._audit_log(
            project=project,
            action="magic_links.revoked_all",
        )

        return {
            "project": project,
            "action": "revoked_all",
            "revoked_at": revocation_data["revoked_all_at"],
        }

    def _is_token_revoked_by_timestamp(self, project: str, issued_at: datetime) -> bool:
        """Check if a token was issued before the revocation timestamp.

        Args:
            project: Project name
            issued_at: When the token was issued

        Returns:
            True if token was issued before revocation
        """
        revoked_file = self._revoked_tokens_file(project)
        if not revoked_file.exists():
            return False

        try:
            revoked_data = json.loads(revoked_file.read_text())
            revoked_all_at = revoked_data.get("revoked_all_at")
            if revoked_all_at:
                revocation_time = datetime.fromisoformat(revoked_all_at.replace("Z", "+00:00"))
                return issued_at < revocation_time
        except (json.JSONDecodeError, OSError, ValueError):
            pass

        return False


# Global secrets service instance (loaded lazily)
_secrets: SecretsService | None = None


def get_secrets_service() -> SecretsService:
    """Get the global secrets service instance."""
    global _secrets
    if _secrets is None:
        _secrets = SecretsService()
    return _secrets
