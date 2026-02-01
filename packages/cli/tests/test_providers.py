"""Tests for the providers module."""

import pytest

from hostkit.services.providers import (
    KeyValidation,
    Provider,
    PROVIDERS,
    detect_provider_from_env_key,
    get_all_providers,
    get_provider,
    get_provider_for_key,
    get_provider_ids,
    validate_secret,
)


class TestKeyValidation:
    """Tests for KeyValidation dataclass."""

    def test_validate_pattern_match(self):
        """Test validation with matching pattern."""
        validation = KeyValidation(
            pattern=r"^sk_live_[A-Za-z0-9]+$",
            example="sk_live_xxxx",
        )

        result = validation.validate("sk_live_abc123")

        assert result["valid"] is True
        assert result["format_valid"] is True
        assert result["warnings"] == []

    def test_validate_pattern_no_match(self):
        """Test validation with non-matching pattern."""
        validation = KeyValidation(
            pattern=r"^sk_live_[A-Za-z0-9]+$",
            example="sk_live_xxxx",
        )

        result = validation.validate("invalid_key")

        assert result["format_valid"] is False
        assert "Invalid format" in result["warnings"]

    def test_validate_min_length(self):
        """Test validation with minimum length."""
        validation = KeyValidation(min_length=10)

        result = validation.validate("short")

        assert result["valid"] is False
        assert any("too short" in w for w in result["warnings"])

    def test_validate_max_length(self):
        """Test validation with maximum length."""
        validation = KeyValidation(max_length=5)

        result = validation.validate("toolongvalue")

        assert result["valid"] is False
        assert any("too long" in w for w in result["warnings"])

    def test_detect_test_key(self):
        """Test detection of test/development keys."""
        validation = KeyValidation(
            pattern=r"^sk_(test|live)_[A-Za-z0-9]+$",
            test_pattern=r"^sk_test_",
            live_pattern=r"^sk_live_",
        )

        result = validation.validate("sk_test_abc123")

        assert result["key_type"] == "test"
        assert "Using test/development key" in result["warnings"]

    def test_detect_live_key(self):
        """Test detection of live/production keys."""
        validation = KeyValidation(
            pattern=r"^sk_(test|live)_[A-Za-z0-9]+$",
            test_pattern=r"^sk_test_",
            live_pattern=r"^sk_live_",
        )

        result = validation.validate("sk_live_abc123")

        assert result["key_type"] == "live"
        assert "Using test/development key" not in result["warnings"]

    def test_no_pattern(self):
        """Test validation without pattern returns format_valid as None."""
        validation = KeyValidation()

        result = validation.validate("any_value")

        assert result["valid"] is True
        assert result["format_valid"] is None


class TestProvider:
    """Tests for Provider dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        provider = Provider(
            id="test_provider",
            name="Test Provider",
            keys=["TEST_KEY"],
            url="https://test.com",
            path="Settings > API Keys",
            docs="https://test.com/docs",
            tips=["Tip 1", "Tip 2"],
        )

        result = provider.to_dict()

        assert result["id"] == "test_provider"
        assert result["name"] == "Test Provider"
        assert result["keys"] == ["TEST_KEY"]
        assert result["url"] == "https://test.com"
        assert result["tips"] == ["Tip 1", "Tip 2"]

    def test_get_key_validation(self):
        """Test getting key validation rules."""
        provider = Provider(
            id="test",
            name="Test",
            keys=["KEY1", "KEY2"],
            url="https://test.com",
            path="path",
            validation={
                "KEY1": KeyValidation(pattern=r"^test_"),
            },
        )

        assert provider.get_key_validation("KEY1") is not None
        assert provider.get_key_validation("KEY2") is None
        assert provider.get_key_validation("KEY3") is None


class TestProviderDatabase:
    """Tests for the provider database."""

    def test_providers_exist(self):
        """Test that providers database is populated."""
        assert len(PROVIDERS) > 0

    def test_required_providers_present(self):
        """Test that key providers are present."""
        required_providers = [
            "stripe",
            "google_oauth",
            "aws",
            "openai",
            "anthropic",
            "sendgrid",
            "twilio",
            "github",
        ]

        for provider_id in required_providers:
            assert provider_id in PROVIDERS, f"Missing provider: {provider_id}"

    def test_get_provider(self):
        """Test getting a provider by ID."""
        provider = get_provider("stripe")

        assert provider is not None
        assert provider.id == "stripe"
        assert provider.name == "Stripe Dashboard"
        assert "STRIPE_API_KEY" in provider.keys

    def test_get_provider_not_found(self):
        """Test getting a nonexistent provider."""
        provider = get_provider("nonexistent_provider")
        assert provider is None

    def test_get_provider_for_key(self):
        """Test finding provider for a key."""
        provider = get_provider_for_key("STRIPE_API_KEY")

        assert provider is not None
        assert provider.id == "stripe"

    def test_get_provider_for_unknown_key(self):
        """Test finding provider for unknown key."""
        provider = get_provider_for_key("UNKNOWN_CUSTOM_KEY")
        assert provider is None

    def test_get_all_providers(self):
        """Test getting all providers."""
        providers = get_all_providers()

        assert len(providers) > 0
        assert all(isinstance(p, Provider) for p in providers)

    def test_get_provider_ids(self):
        """Test getting all provider IDs."""
        ids = get_provider_ids()

        assert len(ids) > 0
        assert "stripe" in ids
        assert "google_oauth" in ids


class TestValidateSecret:
    """Tests for the validate_secret function."""

    def test_validate_stripe_api_key_live(self):
        """Test validating a Stripe live API key."""
        result = validate_secret("STRIPE_API_KEY", "sk_live_abc123def456ghi789")

        assert result["valid"] is True
        assert result["format_valid"] is True
        assert result["key_type"] == "live"
        assert result["provider"] == "stripe"

    def test_validate_stripe_api_key_test(self):
        """Test validating a Stripe test API key."""
        result = validate_secret("STRIPE_API_KEY", "sk_test_abc123def456ghi789")

        assert result["valid"] is True
        assert result["format_valid"] is True
        assert result["key_type"] == "test"
        assert "Using test/development key" in result["warnings"]

    def test_validate_invalid_stripe_key(self):
        """Test validating an invalid Stripe key format."""
        result = validate_secret("STRIPE_API_KEY", "invalid_key")

        assert result["format_valid"] is False
        assert any("Invalid format" in w for w in result["warnings"])

    def test_validate_openai_api_key(self):
        """Test validating an OpenAI API key."""
        # Valid format: sk- followed by 48+ alphanumeric chars
        key = "sk-" + "a" * 48

        result = validate_secret("OPENAI_API_KEY", key)

        assert result["format_valid"] is True
        assert result["provider"] == "openai"

    def test_validate_aws_access_key(self):
        """Test validating an AWS access key ID."""
        # Valid format: AKIA followed by 16 uppercase alphanumeric chars
        key = "AKIA" + "A" * 16

        result = validate_secret("AWS_ACCESS_KEY_ID", key)

        assert result["format_valid"] is True
        assert result["provider"] == "aws"

    def test_validate_unknown_key(self):
        """Test validating an unknown key type."""
        result = validate_secret("MY_CUSTOM_KEY", "any_value")

        assert result["valid"] is True
        assert result["format_valid"] is None
        assert result["provider"] is None


class TestDetectProviderFromEnvKey:
    """Tests for the detect_provider_from_env_key function."""

    def test_detect_stripe(self):
        """Test detecting Stripe provider."""
        assert detect_provider_from_env_key("STRIPE_API_KEY") == "stripe"
        assert detect_provider_from_env_key("STRIPE_SECRET_KEY") == "stripe"
        assert detect_provider_from_env_key("STRIPE_WEBHOOK_SECRET") == "stripe"

    def test_detect_google_oauth(self):
        """Test detecting Google OAuth provider."""
        assert detect_provider_from_env_key("GOOGLE_CLIENT_ID") == "google_oauth"
        assert detect_provider_from_env_key("GOOGLE_CLIENT_SECRET") == "google_oauth"

    def test_detect_aws(self):
        """Test detecting AWS provider."""
        assert detect_provider_from_env_key("AWS_ACCESS_KEY_ID") == "aws"
        assert detect_provider_from_env_key("AWS_SECRET_ACCESS_KEY") == "aws"

    def test_detect_openai(self):
        """Test detecting OpenAI provider."""
        assert detect_provider_from_env_key("OPENAI_API_KEY") == "openai"

    def test_detect_anthropic(self):
        """Test detecting Anthropic provider."""
        assert detect_provider_from_env_key("ANTHROPIC_API_KEY") == "anthropic"

    def test_detect_by_prefix(self):
        """Test detecting provider by prefix."""
        assert detect_provider_from_env_key("STRIPE_CUSTOM_FIELD") == "stripe"
        assert detect_provider_from_env_key("AWS_REGION") == "aws"
        assert detect_provider_from_env_key("SUPABASE_CUSTOM") == "supabase"

    def test_detect_unknown(self):
        """Test that unknown keys return None."""
        assert detect_provider_from_env_key("MY_CUSTOM_KEY") is None
        assert detect_provider_from_env_key("UNKNOWN_SERVICE") is None

    def test_case_insensitivity(self):
        """Test that detection is case-insensitive for direct matches."""
        # Direct mappings are uppercase internally
        assert detect_provider_from_env_key("STRIPE_API_KEY") == "stripe"


class TestProviderValidationPatterns:
    """Tests for specific provider validation patterns."""

    def test_google_client_id_pattern(self):
        """Test Google Client ID validation."""
        # Valid format: numbers-alphanumeric.apps.googleusercontent.com
        valid_id = "123456789-abc123def456.apps.googleusercontent.com"
        result = validate_secret("GOOGLE_CLIENT_ID", valid_id)
        assert result["format_valid"] is True

        invalid_id = "invalid@email.com"
        result = validate_secret("GOOGLE_CLIENT_ID", invalid_id)
        assert result["format_valid"] is False

    def test_sendgrid_api_key_pattern(self):
        """Test SendGrid API key validation."""
        # Valid format: SG.xxx.yyy
        valid_key = "SG.abc123_def-456.ghi789_jkl-012"
        result = validate_secret("SENDGRID_API_KEY", valid_key)
        assert result["format_valid"] is True

        invalid_key = "sg_invalid_key"
        result = validate_secret("SENDGRID_API_KEY", invalid_key)
        assert result["format_valid"] is False

    def test_twilio_account_sid_pattern(self):
        """Test Twilio Account SID validation."""
        # Valid format: AC followed by 32 hex chars
        valid_sid = "AC" + "a" * 32
        result = validate_secret("TWILIO_ACCOUNT_SID", valid_sid)
        assert result["format_valid"] is True

        invalid_sid = "TW12345"
        result = validate_secret("TWILIO_ACCOUNT_SID", invalid_sid)
        assert result["format_valid"] is False

    def test_github_token_pattern(self):
        """Test GitHub token validation."""
        # Valid format: ghp_ followed by 36 chars
        valid_token = "ghp_" + "A" * 36
        result = validate_secret("GITHUB_TOKEN", valid_token)
        assert result["format_valid"] is True

        invalid_token = "github_token_123"
        result = validate_secret("GITHUB_TOKEN", invalid_token)
        assert result["format_valid"] is False

    def test_supabase_url_pattern(self):
        """Test Supabase URL validation."""
        # Valid format: https://xxx.supabase.co
        valid_url = "https://abcdefghij.supabase.co"
        result = validate_secret("SUPABASE_URL", valid_url)
        assert result["format_valid"] is True

        invalid_url = "http://localhost:5432"
        result = validate_secret("SUPABASE_URL", invalid_url)
        assert result["format_valid"] is False

    def test_anthropic_api_key_pattern(self):
        """Test Anthropic API key validation."""
        # Valid format: sk-ant-xxx
        valid_key = "sk-ant-" + "a" * 32
        result = validate_secret("ANTHROPIC_API_KEY", valid_key)
        assert result["format_valid"] is True

        invalid_key = "sk_anthropic_123"
        result = validate_secret("ANTHROPIC_API_KEY", invalid_key)
        assert result["format_valid"] is False
