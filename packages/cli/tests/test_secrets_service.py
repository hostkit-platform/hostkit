"""Tests for the secrets service."""

import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hostkit.services.crypto_service import CryptoService
from hostkit.services.secrets_service import (
    MagicLinkToken,
    ProjectSecrets,
    SecretMetadata,
    SecretsService,
    SecretsServiceError,
)


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as secrets_dir:
        with tempfile.TemporaryDirectory() as key_dir:
            yield Path(secrets_dir), Path(key_dir)


@pytest.fixture
def mock_db():
    """Create a mock database."""
    db = MagicMock()
    db.get_project.return_value = {"name": "testproject", "status": "running"}
    return db


@pytest.fixture
def secrets_service(temp_dirs, mock_db):
    """Create a SecretsService with temporary directories."""
    secrets_dir, key_dir = temp_dirs
    key_path = key_dir / "master.key"

    crypto = CryptoService(master_key_path=key_path)
    crypto.generate_master_key()

    service = SecretsService(secrets_dir=secrets_dir, crypto=crypto)

    with patch.object(service, "db", mock_db):
        yield service


class TestSecretMetadata:
    """Tests for SecretMetadata dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        meta = SecretMetadata(
            key="API_KEY",
            required=True,
            provider="stripe",
            description="Stripe API key",
            set_at="2024-01-01T00:00:00Z",
            length=32,
        )

        result = meta.to_dict()

        assert result["key"] == "API_KEY"
        assert result["required"] is True
        assert result["provider"] == "stripe"
        assert result["description"] == "Stripe API key"
        assert result["set_at"] == "2024-01-01T00:00:00Z"
        assert result["length"] == 32

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "key": "SECRET",
            "required": False,
            "provider": "aws",
            "description": "AWS secret",
            "set_at": "2024-01-01T00:00:00Z",
            "length": 40,
        }

        meta = SecretMetadata.from_dict(data)

        assert meta.key == "SECRET"
        assert meta.required is False
        assert meta.provider == "aws"
        assert meta.description == "AWS secret"
        assert meta.set_at == "2024-01-01T00:00:00Z"
        assert meta.length == 40

    def test_from_dict_minimal(self):
        """Test creation from minimal dictionary."""
        data = {"key": "SIMPLE"}

        meta = SecretMetadata.from_dict(data)

        assert meta.key == "SIMPLE"
        assert meta.required is True  # default
        assert meta.provider is None
        assert meta.description is None


class TestProjectSecrets:
    """Tests for ProjectSecrets dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        ps = ProjectSecrets(
            project="myapp",
            secrets={"KEY1": "value1", "KEY2": "value2"},
            metadata={
                "KEY1": SecretMetadata(key="KEY1", required=True),
                "KEY2": SecretMetadata(key="KEY2", required=False),
            },
        )

        result = ps.to_dict()

        assert result["project"] == "myapp"
        assert result["secrets"] == {"KEY1": "value1", "KEY2": "value2"}
        assert "KEY1" in result["metadata"]
        assert "KEY2" in result["metadata"]

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "project": "testapp",
            "secrets": {"API_KEY": "secret123"},
            "metadata": {
                "API_KEY": {"key": "API_KEY", "required": True, "provider": "openai"},
            },
        }

        ps = ProjectSecrets.from_dict(data)

        assert ps.project == "testapp"
        assert ps.secrets["API_KEY"] == "secret123"
        assert ps.metadata["API_KEY"].provider == "openai"


class TestSecretsService:
    """Tests for SecretsService."""

    def test_set_and_get_secret(self, secrets_service):
        """Test setting and getting a secret."""
        result = secrets_service.set_secret(
            project="testproject",
            key="API_KEY",
            value="sk_live_12345",
            provider="stripe",
        )

        assert result["action"] == "created"
        assert result["key"] == "API_KEY"
        assert result["length"] == len("sk_live_12345")

        # Get the secret back
        value = secrets_service.get_secret("testproject", "API_KEY")
        assert value == "sk_live_12345"

    def test_update_secret(self, secrets_service):
        """Test updating an existing secret."""
        secrets_service.set_secret("testproject", "KEY", "value1")
        result = secrets_service.set_secret("testproject", "KEY", "value2")

        assert result["action"] == "updated"

        value = secrets_service.get_secret("testproject", "KEY")
        assert value == "value2"

    def test_delete_secret(self, secrets_service):
        """Test deleting a secret."""
        secrets_service.set_secret("testproject", "DELETE_ME", "value")

        result = secrets_service.delete_secret("testproject", "DELETE_ME")

        assert result["action"] == "deleted"

        value = secrets_service.get_secret("testproject", "DELETE_ME")
        assert value is None

    def test_delete_nonexistent_secret(self, secrets_service):
        """Test deleting a secret that doesn't exist."""
        with pytest.raises(SecretsServiceError) as exc_info:
            secrets_service.delete_secret("testproject", "NONEXISTENT")

        assert exc_info.value.code == "SECRET_NOT_FOUND"

    def test_list_secrets(self, secrets_service):
        """Test listing secrets."""
        secrets_service.set_secret(
            "testproject", "KEY1", "value1", required=True, provider="stripe"
        )
        secrets_service.set_secret(
            "testproject", "KEY2", "val2", required=False
        )

        result = secrets_service.list_secrets("testproject")

        assert len(result) == 2

        key1 = next(s for s in result if s["key"] == "KEY1")
        assert key1["set"] is True
        assert key1["length"] == len("value1")
        assert key1["required"] is True
        assert key1["provider"] == "stripe"

        key2 = next(s for s in result if s["key"] == "KEY2")
        assert key2["set"] is True
        assert key2["required"] is False

    def test_list_empty_secrets(self, secrets_service):
        """Test listing secrets for project with no secrets."""
        result = secrets_service.list_secrets("testproject")
        assert result == []

    def test_import_secrets(self, secrets_service):
        """Test importing multiple secrets."""
        secrets_dict = {
            "KEY1": "value1",
            "KEY2": "value2",
            "KEY3": "value3",
        }

        result = secrets_service.import_secrets("testproject", secrets_dict)

        assert result["total_imported"] == 3
        assert set(result["created"]) == {"KEY1", "KEY2", "KEY3"}

        # Verify all were stored
        for key, expected_value in secrets_dict.items():
            value = secrets_service.get_secret("testproject", key)
            assert value == expected_value

    def test_import_secrets_no_overwrite(self, secrets_service):
        """Test importing secrets without overwriting existing."""
        secrets_service.set_secret("testproject", "EXISTING", "original")

        result = secrets_service.import_secrets(
            "testproject",
            {"EXISTING": "new", "NEW_KEY": "value"},
            overwrite=False,
        )

        assert result["skipped"] == ["EXISTING"]
        assert result["created"] == ["NEW_KEY"]

        # Original value should be preserved
        assert secrets_service.get_secret("testproject", "EXISTING") == "original"

    def test_import_secrets_with_overwrite(self, secrets_service):
        """Test importing secrets with overwrite enabled."""
        secrets_service.set_secret("testproject", "EXISTING", "original")

        result = secrets_service.import_secrets(
            "testproject",
            {"EXISTING": "new"},
            overwrite=True,
        )

        assert result["updated"] == ["EXISTING"]

        assert secrets_service.get_secret("testproject", "EXISTING") == "new"

    def test_get_all_secrets(self, secrets_service):
        """Test getting all secrets for a project."""
        secrets_service.set_secret("testproject", "KEY1", "value1")
        secrets_service.set_secret("testproject", "KEY2", "value2")

        all_secrets = secrets_service.get_all_secrets("testproject")

        assert all_secrets == {"KEY1": "value1", "KEY2": "value2"}

    def test_delete_all_secrets(self, secrets_service, temp_dirs):
        """Test deleting all secrets for a project."""
        secrets_service.set_secret("testproject", "KEY1", "value1")
        secrets_service.set_secret("testproject", "KEY2", "value2")

        result = secrets_service.delete_all_secrets("testproject")

        assert result["action"] == "deleted_all"

        # All secrets should be gone
        assert secrets_service.get_all_secrets("testproject") == {}

    def test_invalid_key_name(self, secrets_service):
        """Test error for invalid key names."""
        with pytest.raises(SecretsServiceError) as exc_info:
            secrets_service.set_secret("testproject", "invalid-key", "value")

        assert exc_info.value.code == "INVALID_KEY_NAME"

        with pytest.raises(SecretsServiceError):
            secrets_service.set_secret("testproject", "123starts_with_number", "value")

    def test_valid_key_names(self, secrets_service):
        """Test valid key name formats."""
        valid_keys = [
            "SIMPLE",
            "with_underscore",
            "_starts_with_underscore",
            "MixedCase123",
            "A",
        ]

        for key in valid_keys:
            result = secrets_service.set_secret("testproject", key, "value")
            assert result["key"] == key

    def test_project_not_found(self, secrets_service, mock_db):
        """Test error when project doesn't exist."""
        mock_db.get_project.return_value = None

        with pytest.raises(SecretsServiceError) as exc_info:
            secrets_service.set_secret("nonexistent", "KEY", "value")

        assert exc_info.value.code == "PROJECT_NOT_FOUND"

    def test_audit_log(self, secrets_service, temp_dirs):
        """Test audit logging."""
        secrets_dir, _ = temp_dirs

        secrets_service.set_secret("testproject", "KEY1", "value1")
        secrets_service.set_secret("testproject", "KEY2", "value2")
        secrets_service.delete_secret("testproject", "KEY1")

        audit_log = secrets_service.get_audit_log("testproject")

        assert len(audit_log) == 3

        # Most recent first
        assert audit_log[0]["action"] == "secret.deleted"
        assert audit_log[1]["action"] == "secret.created"
        assert audit_log[2]["action"] == "secret.created"

    def test_secrets_persistence(self, temp_dirs, mock_db):
        """Test that secrets persist across service instances."""
        secrets_dir, key_dir = temp_dirs
        key_path = key_dir / "master.key"

        crypto = CryptoService(master_key_path=key_path)
        crypto.generate_master_key()

        # Create service and set secret
        service1 = SecretsService(secrets_dir=secrets_dir, crypto=crypto)
        with patch.object(service1, "db", mock_db):
            service1.set_secret("testproject", "PERSISTENT", "myvalue")

        # Create new service instance
        service2 = SecretsService(secrets_dir=secrets_dir, crypto=crypto)
        with patch.object(service2, "db", mock_db):
            value = service2.get_secret("testproject", "PERSISTENT")

        assert value == "myvalue"


class TestSecretsDirectory:
    """Tests for secrets directory management."""

    def test_directory_created_with_permissions(self, temp_dirs, mock_db):
        """Test that secrets directory is created with correct permissions."""
        secrets_dir, key_dir = temp_dirs
        key_path = key_dir / "master.key"

        # Use a non-existent subdirectory
        new_secrets_dir = secrets_dir / "new_subdir"

        crypto = CryptoService(master_key_path=key_path)
        crypto.generate_master_key()

        service = SecretsService(secrets_dir=new_secrets_dir, crypto=crypto)
        with patch.object(service, "db", mock_db):
            service.set_secret("testproject", "KEY", "value")

        # Directory should exist with 0700 permissions
        assert new_secrets_dir.exists()
        mode = new_secrets_dir.stat().st_mode & 0o777
        assert mode == 0o700

    def test_file_permissions(self, secrets_service, temp_dirs):
        """Test that secret files have correct permissions."""
        secrets_dir, _ = temp_dirs

        secrets_service.set_secret("testproject", "KEY", "value")

        enc_file = secrets_dir / "testproject.enc"
        meta_file = secrets_dir / "testproject.meta"

        assert enc_file.exists()
        assert meta_file.exists()

        assert (enc_file.stat().st_mode & 0o777) == 0o600
        assert (meta_file.stat().st_mode & 0o777) == 0o600


class TestMagicLinkToken:
    """Tests for MagicLinkToken dataclass."""

    def test_is_expired_false(self):
        """Test that a future expiration is not expired."""
        token = MagicLinkToken(
            project="testproject",
            jti="test-jti",
            scope="secrets:write",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        assert token.is_expired is False

    def test_is_expired_true(self):
        """Test that a past expiration is expired."""
        token = MagicLinkToken(
            project="testproject",
            jti="test-jti",
            scope="secrets:write",
            issued_at=datetime.now(timezone.utc) - timedelta(hours=48),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=24),
        )
        assert token.is_expired is True

    def test_to_dict(self):
        """Test conversion to dictionary."""
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=24)
        token = MagicLinkToken(
            project="myapp",
            jti="unique-id",
            scope="secrets:write",
            issued_at=now,
            expires_at=expires,
        )

        result = token.to_dict()

        assert result["project"] == "myapp"
        assert result["jti"] == "unique-id"
        assert result["scope"] == "secrets:write"
        assert "issued_at" in result
        assert "expires_at" in result
        assert "is_expired" in result


class TestMagicLinks:
    """Tests for magic link generation and validation."""

    def test_generate_magic_link(self, secrets_service):
        """Test generating a magic link."""
        result = secrets_service.generate_magic_link("testproject", expires_hours=24)

        assert result["project"] == "testproject"
        assert "magic_link" in result
        assert "token" in result
        assert "jti" in result
        assert result["expires_in_hours"] == 24
        assert "portal_url" in result
        assert "secrets.hostkit.dev" in result["portal_url"]
        assert "token=" in result["magic_link"]

    def test_generate_magic_link_custom_expiry(self, secrets_service):
        """Test generating magic link with custom expiration."""
        result = secrets_service.generate_magic_link("testproject", expires_hours=1)

        assert result["expires_in_hours"] == 1

    def test_validate_magic_link(self, secrets_service):
        """Test validating a generated magic link."""
        gen_result = secrets_service.generate_magic_link("testproject")
        token = gen_result["token"]

        validated = secrets_service.validate_magic_link(token)

        assert isinstance(validated, MagicLinkToken)
        assert validated.project == "testproject"
        assert validated.jti == gen_result["jti"]
        assert validated.scope == "secrets:write"
        assert validated.is_expired is False

    def test_validate_invalid_token(self, secrets_service):
        """Test validation fails for invalid token."""
        with pytest.raises(SecretsServiceError) as exc_info:
            secrets_service.validate_magic_link("invalid-token")

        assert exc_info.value.code == "TOKEN_INVALID"

    def test_validate_tampered_token(self, secrets_service):
        """Test validation fails for tampered token."""
        gen_result = secrets_service.generate_magic_link("testproject")
        token = gen_result["token"]

        # Tamper with the token
        tampered = token[:-10] + "tampered00"

        with pytest.raises(SecretsServiceError) as exc_info:
            secrets_service.validate_magic_link(tampered)

        assert exc_info.value.code == "TOKEN_INVALID"

    def test_revoke_magic_links(self, secrets_service):
        """Test revoking all magic links for a project."""
        # Generate a token first
        gen_result = secrets_service.generate_magic_link("testproject")
        token = gen_result["token"]

        # Should validate before revocation
        secrets_service.validate_magic_link(token)

        # Revoke all links
        revoke_result = secrets_service.revoke_magic_links("testproject")
        assert revoke_result["action"] == "revoked_all"
        assert "revoked_at" in revoke_result

        # Token should now be rejected
        with pytest.raises(SecretsServiceError) as exc_info:
            secrets_service.validate_magic_link(token)

        assert exc_info.value.code == "TOKEN_REVOKED"

    def test_new_token_after_revocation(self, secrets_service):
        """Test that new tokens work after revocation."""
        # Generate and revoke
        old_result = secrets_service.generate_magic_link("testproject")
        secrets_service.revoke_magic_links("testproject")

        # Generate new token
        new_result = secrets_service.generate_magic_link("testproject")

        # Old token should fail
        with pytest.raises(SecretsServiceError):
            secrets_service.validate_magic_link(old_result["token"])

        # New token should work
        validated = secrets_service.validate_magic_link(new_result["token"])
        assert validated.project == "testproject"

    def test_magic_link_audit_log(self, secrets_service):
        """Test that magic link operations are logged."""
        secrets_service.generate_magic_link("testproject")
        secrets_service.revoke_magic_links("testproject")

        audit_log = secrets_service.get_audit_log("testproject")

        actions = [entry["action"] for entry in audit_log]
        assert "magic_link.generated" in actions
        assert "magic_links.revoked_all" in actions

    def test_project_not_found_for_magic_link(self, secrets_service, mock_db):
        """Test error when generating magic link for nonexistent project."""
        mock_db.get_project.return_value = None

        with pytest.raises(SecretsServiceError) as exc_info:
            secrets_service.generate_magic_link("nonexistent")

        assert exc_info.value.code == "PROJECT_NOT_FOUND"

    def test_magic_link_contains_project_in_url(self, secrets_service):
        """Test that magic link URL contains project name."""
        result = secrets_service.generate_magic_link("testproject")

        assert "/p/testproject" in result["magic_link"]
        assert "/p/testproject" in result["portal_url"]


class TestDefineSecrets:
    """Tests for secret definition functionality."""

    def test_define_secret(self, secrets_service):
        """Test defining a secret requirement."""
        result = secrets_service.define_secret(
            project="testproject",
            key="API_KEY",
            required=True,
            provider="stripe",
            description="Stripe API key",
        )

        assert result["action"] == "defined"
        assert result["key"] == "API_KEY"
        assert result["required"] is True
        assert result["provider"] == "stripe"

    def test_define_secret_auto_detect_provider(self, secrets_service):
        """Test that provider is auto-detected from key name."""
        result = secrets_service.define_secret(
            project="testproject",
            key="STRIPE_API_KEY",
            required=True,
        )

        assert result["provider"] == "stripe"

    def test_define_secret_update_existing(self, secrets_service):
        """Test updating an existing secret definition."""
        secrets_service.define_secret("testproject", "KEY1", required=True)
        result = secrets_service.define_secret(
            "testproject", "KEY1", required=False, description="Updated"
        )

        assert result["action"] == "updated"
        assert result["required"] is False

    def test_define_secrets_from_env(self, secrets_service):
        """Test defining secrets from .env.example content."""
        env_content = """# Auto-generated by HostKit
DATABASE_URL=
SECRET_KEY=

# Required - OAuth login
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Required - Payment processing
STRIPE_API_KEY=

# Optional - Email service
SENDGRID_API_KEY=
"""
        result = secrets_service.define_secrets_from_env("testproject", env_content)

        # Auto-generated block catches both DATABASE_URL and SECRET_KEY
        assert len(result["auto_generated"]) == 2
        assert "DATABASE_URL" in result["auto_generated"]
        assert "SECRET_KEY" in result["auto_generated"]

        # Should have 4 defined secrets
        assert result["total_defined"] == 4

        # Check defined keys
        defined_keys = [d["key"] for d in result["defined"]]
        assert "GOOGLE_CLIENT_ID" in defined_keys
        assert "GOOGLE_CLIENT_SECRET" in defined_keys
        assert "STRIPE_API_KEY" in defined_keys
        assert "SENDGRID_API_KEY" in defined_keys

        # Check providers were detected
        google_def = next(d for d in result["defined"] if d["key"] == "GOOGLE_CLIENT_ID")
        assert google_def["provider"] == "google_oauth"

    def test_define_secrets_from_env_required_optional(self, secrets_service):
        """Test that required/optional markers are parsed correctly."""
        env_content = """
# Required - Must have this
REQUIRED_KEY=

# Optional - Nice to have
OPTIONAL_KEY=

# No marker - defaults to required
DEFAULT_KEY=
"""
        result = secrets_service.define_secrets_from_env("testproject", env_content)

        defined = {d["key"]: d for d in result["defined"]}

        assert defined["REQUIRED_KEY"]["required"] is True
        assert defined["OPTIONAL_KEY"]["required"] is False
        assert defined["DEFAULT_KEY"]["required"] is True


class TestVerifySecrets:
    """Tests for secret verification functionality."""

    def test_verify_no_secrets_defined(self, secrets_service):
        """Test verification when no secrets are defined."""
        result = secrets_service.verify_secrets("testproject")

        assert result["ready"] is True
        assert result["required_count"] == 0
        assert result["secrets"] == []

    def test_verify_all_required_set(self, secrets_service):
        """Test verification when all required secrets are set."""
        secrets_service.define_secret("testproject", "KEY1", required=True)
        secrets_service.define_secret("testproject", "KEY2", required=True)
        secrets_service.set_secret("testproject", "KEY1", "value1")
        secrets_service.set_secret("testproject", "KEY2", "value2")

        result = secrets_service.verify_secrets("testproject")

        assert result["ready"] is True
        assert result["required_count"] == 2
        assert result["required_set"] == 2

    def test_verify_missing_required(self, secrets_service):
        """Test verification when required secrets are missing."""
        secrets_service.define_secret("testproject", "KEY1", required=True)
        secrets_service.define_secret("testproject", "KEY2", required=True)
        secrets_service.set_secret("testproject", "KEY1", "value1")
        # KEY2 not set

        result = secrets_service.verify_secrets("testproject")

        assert result["ready"] is False
        assert result["required_count"] == 2
        assert result["required_set"] == 1

    def test_verify_optional_not_required(self, secrets_service):
        """Test that optional secrets don't affect readiness."""
        secrets_service.define_secret("testproject", "REQUIRED", required=True)
        secrets_service.define_secret("testproject", "OPTIONAL", required=False)
        secrets_service.set_secret("testproject", "REQUIRED", "value")
        # OPTIONAL not set

        result = secrets_service.verify_secrets("testproject")

        assert result["ready"] is True
        assert result["optional_count"] == 1
        assert result["optional_set"] == 0

    def test_verify_with_format_validation(self, secrets_service):
        """Test verification includes format validation."""
        secrets_service.define_secret(
            "testproject", "STRIPE_API_KEY", required=True, provider="stripe"
        )
        secrets_service.set_secret(
            "testproject", "STRIPE_API_KEY", "sk_live_validkey1234567890"
        )

        result = secrets_service.verify_secrets("testproject")

        secret = result["secrets"][0]
        assert secret["set"] is True
        assert secret["format_valid"] is True
        assert secret["key_type"] == "live"

    def test_verify_detects_test_keys(self, secrets_service):
        """Test that verification detects test keys."""
        secrets_service.define_secret("testproject", "STRIPE_API_KEY", required=True)
        secrets_service.set_secret(
            "testproject", "STRIPE_API_KEY", "sk_test_testkey1234567890"
        )

        result = secrets_service.verify_secrets("testproject")

        secret = result["secrets"][0]
        assert secret["key_type"] == "test"
        assert any("test" in w.lower() for w in secret["warnings"])
        assert result["has_warnings"] is True

    def test_verify_includes_provider_info(self, secrets_service):
        """Test that verification includes provider information."""
        secrets_service.define_secret(
            "testproject", "STRIPE_API_KEY", required=True, provider="stripe"
        )
        secrets_service.set_secret(
            "testproject", "STRIPE_API_KEY", "sk_live_validkey1234567890"
        )

        result = secrets_service.verify_secrets("testproject")

        secret = result["secrets"][0]
        assert secret["provider_name"] == "Stripe Dashboard"
        assert "stripe.com" in secret["provider_url"]

    def test_verify_invalid_format(self, secrets_service):
        """Test verification detects invalid key formats."""
        secrets_service.define_secret(
            "testproject", "STRIPE_API_KEY", required=True, provider="stripe"
        )
        secrets_service.set_secret(
            "testproject", "STRIPE_API_KEY", "invalid_key_format"
        )

        result = secrets_service.verify_secrets("testproject")

        secret = result["secrets"][0]
        assert secret["format_valid"] is False
        assert any("Invalid format" in w for w in secret["warnings"])
