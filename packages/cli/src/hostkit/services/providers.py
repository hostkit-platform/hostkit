"""Provider database for HostKit secrets validation.

This module contains a database of common API providers with:
- Key validation patterns (regex)
- Console/dashboard URLs
- Navigation paths to find API keys
- Tips for users
- Test vs production key detection
"""

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KeyValidation:
    """Validation rules for a specific key type."""

    pattern: str | None = None  # Regex pattern
    example: str | None = None  # Example value
    min_length: int | None = None
    max_length: int | None = None
    test_pattern: str | None = None  # Pattern indicating test/dev key
    live_pattern: str | None = None  # Pattern indicating live/prod key

    def validate(self, value: str) -> dict[str, Any]:
        """Validate a secret value against this key's rules.

        Args:
            value: The secret value to validate

        Returns:
            Dict with 'valid', 'format_valid', 'warnings', 'key_type' (test/live/unknown)
        """
        result: dict[str, Any] = {
            "valid": True,
            "format_valid": None,  # None means no pattern to check
            "warnings": [],
            "key_type": "unknown",
        }

        # Check length constraints
        if self.min_length and len(value) < self.min_length:
            result["valid"] = False
            result["warnings"].append(
                f"Value too short (min {self.min_length} chars)"
            )

        if self.max_length and len(value) > self.max_length:
            result["valid"] = False
            result["warnings"].append(
                f"Value too long (max {self.max_length} chars)"
            )

        # Check pattern
        if self.pattern:
            if re.match(self.pattern, value):
                result["format_valid"] = True
            else:
                result["format_valid"] = False
                result["warnings"].append("Invalid format")
                if self.example:
                    result["warnings"].append(f"Expected format like: {self.example}")

        # Detect test vs live keys
        if self.test_pattern and re.match(self.test_pattern, value):
            result["key_type"] = "test"
            result["warnings"].append("Using test/development key")
        elif self.live_pattern and re.match(self.live_pattern, value):
            result["key_type"] = "live"

        return result


@dataclass
class Provider:
    """Provider information and validation rules."""

    id: str
    name: str
    keys: list[str]
    url: str
    path: str
    docs: str | None = None
    tips: list[str] = field(default_factory=list)
    validation: dict[str, KeyValidation] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "keys": self.keys,
            "url": self.url,
            "path": self.path,
            "docs": self.docs,
            "tips": self.tips,
        }

    def get_key_validation(self, key: str) -> KeyValidation | None:
        """Get validation rules for a specific key."""
        return self.validation.get(key)


# =============================================================================
# Provider Database
# =============================================================================

PROVIDERS: dict[str, Provider] = {
    # -------------------------------------------------------------------------
    # OAuth Providers
    # -------------------------------------------------------------------------
    "google_oauth": Provider(
        id="google_oauth",
        name="Google Cloud Console",
        keys=["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        url="https://console.cloud.google.com/apis/credentials",
        path="APIs & Services → Credentials → OAuth 2.0 Client IDs",
        docs="https://developers.google.com/identity/protocols/oauth2",
        tips=[
            "Create an OAuth 2.0 Client ID (Web application type)",
            "Add your domain to Authorized redirect URIs",
        ],
        validation={
            "GOOGLE_CLIENT_ID": KeyValidation(
                pattern=r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$",
                example="123456789-abc123.apps.googleusercontent.com",
            ),
            "GOOGLE_CLIENT_SECRET": KeyValidation(
                pattern=r"^GOCSPX-[A-Za-z0-9_-]+$",
                example="GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxx",
            ),
        },
    ),
    "apple_oauth": Provider(
        id="apple_oauth",
        name="Apple Developer Portal",
        keys=["APPLE_CLIENT_ID", "APPLE_CLIENT_SECRET", "APPLE_TEAM_ID", "APPLE_KEY_ID"],
        url="https://developer.apple.com/account/resources/identifiers",
        path="Certificates, IDs & Profiles → Identifiers → Service IDs",
        docs="https://developer.apple.com/sign-in-with-apple/",
        tips=[
            "Create a Service ID for Sign in with Apple",
            "Configure domains and return URLs in the Service ID",
            "Create a private key for Sign in with Apple",
        ],
        validation={
            "APPLE_CLIENT_ID": KeyValidation(
                pattern=r"^[a-zA-Z0-9.-]+$",
                example="com.example.app",
            ),
            "APPLE_TEAM_ID": KeyValidation(
                pattern=r"^[A-Z0-9]{10}$",
                example="ABC123DEF4",
            ),
            "APPLE_KEY_ID": KeyValidation(
                pattern=r"^[A-Z0-9]{10}$",
                example="KEY123ABC4",
            ),
        },
    ),
    "github": Provider(
        id="github",
        name="GitHub",
        keys=["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_TOKEN"],
        url="https://github.com/settings/developers",
        path="Settings → Developer settings → OAuth Apps",
        docs="https://docs.github.com/en/apps/oauth-apps",
        tips=[
            "For OAuth: Create an OAuth App under Developer settings",
            "For API access: Create a personal access token or GitHub App",
        ],
        validation={
            "GITHUB_CLIENT_ID": KeyValidation(
                pattern=r"^(Iv1\.[a-f0-9]{16}|[a-f0-9]{20})$",
                example="Iv1.1234567890abcdef",
            ),
            "GITHUB_CLIENT_SECRET": KeyValidation(
                pattern=r"^[a-f0-9]{40}$",
                example="40-character hex string",
                min_length=40,
                max_length=40,
            ),
            "GITHUB_TOKEN": KeyValidation(
                pattern=r"^(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82})$",
                example="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            ),
        },
    ),
    "discord": Provider(
        id="discord",
        name="Discord Developer Portal",
        keys=["DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "DISCORD_BOT_TOKEN"],
        url="https://discord.com/developers/applications",
        path="Applications → [Your App] → OAuth2",
        docs="https://discord.com/developers/docs",
        tips=[
            "Create an application in the Developer Portal",
            "For bots: Enable bot in the Bot section and copy token",
            "For OAuth: Copy Client ID and Secret from OAuth2 section",
        ],
        validation={
            "DISCORD_CLIENT_ID": KeyValidation(
                pattern=r"^\d{17,20}$",
                example="123456789012345678",
            ),
            "DISCORD_CLIENT_SECRET": KeyValidation(
                pattern=r"^[A-Za-z0-9_-]{32}$",
                example="32-character string",
            ),
            "DISCORD_BOT_TOKEN": KeyValidation(
                pattern=r"^[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}$",
                example="Bot token (three dot-separated parts)",
            ),
        },
    ),
    "slack": Provider(
        id="slack",
        name="Slack API",
        keys=["SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET", "SLACK_SIGNING_SECRET", "SLACK_BOT_TOKEN"],
        url="https://api.slack.com/apps",
        path="Your Apps → [App] → Basic Information",
        docs="https://api.slack.com/authentication",
        tips=[
            "Create a Slack App from the API portal",
            "Enable OAuth scopes your app needs",
            "Install the app to your workspace to get tokens",
        ],
        validation={
            "SLACK_CLIENT_ID": KeyValidation(
                pattern=r"^\d+\.\d+$",
                example="123456789.987654321",
            ),
            "SLACK_CLIENT_SECRET": KeyValidation(
                pattern=r"^[a-f0-9]{32}$",
                example="32-character hex string",
            ),
            "SLACK_SIGNING_SECRET": KeyValidation(
                pattern=r"^[a-f0-9]{32}$",
                example="32-character hex string",
            ),
            "SLACK_BOT_TOKEN": KeyValidation(
                pattern=r"^xoxb-[0-9]+-[0-9]+-[A-Za-z0-9]+$",
                example="xoxb-123-456-abc123",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # Payment Providers
    # -------------------------------------------------------------------------
    "stripe": Provider(
        id="stripe",
        name="Stripe Dashboard",
        keys=["STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PUBLISHABLE_KEY"],
        url="https://dashboard.stripe.com/apikeys",
        path="Developers → API Keys",
        docs="https://stripe.com/docs/keys",
        tips=[
            "Use sk_test_... for development",
            "Use sk_live_... for production (requires account activation)",
            "Publishable keys (pk_) are safe to include in client-side code",
        ],
        validation={
            "STRIPE_API_KEY": KeyValidation(
                pattern=r"^sk_(test|live)_[A-Za-z0-9]{24,}$",
                example="sk_live_xxxxxxxxxxxxxxxxxxxxxxxx",
                test_pattern=r"^sk_test_",
                live_pattern=r"^sk_live_",
            ),
            "STRIPE_WEBHOOK_SECRET": KeyValidation(
                pattern=r"^whsec_[A-Za-z0-9]+$",
                example="whsec_xxxxxxxxxxxxxxxxxxxxxxxx",
            ),
            "STRIPE_PUBLISHABLE_KEY": KeyValidation(
                pattern=r"^pk_(test|live)_[A-Za-z0-9]{24,}$",
                example="pk_live_xxxxxxxxxxxxxxxxxxxxxxxx",
                test_pattern=r"^pk_test_",
                live_pattern=r"^pk_live_",
            ),
        },
    ),
    "plaid": Provider(
        id="plaid",
        name="Plaid Dashboard",
        keys=["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV"],
        url="https://dashboard.plaid.com/team/keys",
        path="Team Settings → Keys",
        docs="https://plaid.com/docs/api/",
        tips=[
            "Use Sandbox keys for development",
            "Production requires application approval",
            "Set PLAID_ENV to 'sandbox', 'development', or 'production'",
        ],
        validation={
            "PLAID_CLIENT_ID": KeyValidation(
                pattern=r"^[a-f0-9]{24}$",
                example="24-character hex string",
            ),
            "PLAID_SECRET": KeyValidation(
                pattern=r"^[a-f0-9]{30}$",
                example="30-character hex string",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # Cloud Providers
    # -------------------------------------------------------------------------
    "aws": Provider(
        id="aws",
        name="AWS Console",
        keys=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
        url="https://console.aws.amazon.com/iam/home#/security_credentials",
        path="IAM → Users → Security credentials → Create access key",
        docs="https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html",
        tips=[
            "Create an IAM user with minimal required permissions",
            "Never use root account credentials",
            "Consider using IAM roles instead of access keys when possible",
        ],
        validation={
            "AWS_ACCESS_KEY_ID": KeyValidation(
                pattern=r"^AKIA[A-Z0-9]{16}$",
                example="AKIAIOSFODNN7EXAMPLE",
            ),
            "AWS_SECRET_ACCESS_KEY": KeyValidation(
                pattern=r"^[A-Za-z0-9/+=]{40}$",
                example="40-character string",
                min_length=40,
                max_length=40,
            ),
        },
    ),
    "cloudflare": Provider(
        id="cloudflare",
        name="Cloudflare",
        keys=["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ZONE_ID", "CLOUDFLARE_API_KEY"],
        url="https://dash.cloudflare.com/profile/api-tokens",
        path="Profile → API Tokens → Create Token",
        docs="https://developers.cloudflare.com/api/tokens/",
        tips=[
            "Use API Tokens (scoped) instead of Global API Key when possible",
            "Zone ID is found in the Overview tab of your domain",
        ],
        validation={
            "CLOUDFLARE_API_TOKEN": KeyValidation(
                pattern=r"^[A-Za-z0-9_-]{40}$",
                example="40-character token",
            ),
            "CLOUDFLARE_ZONE_ID": KeyValidation(
                pattern=r"^[a-f0-9]{32}$",
                example="32-character hex string",
            ),
            "CLOUDFLARE_API_KEY": KeyValidation(
                pattern=r"^[a-f0-9]{37}$",
                example="37-character hex string (Global API Key)",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # AI/ML Providers
    # -------------------------------------------------------------------------
    "openai": Provider(
        id="openai",
        name="OpenAI Platform",
        keys=["OPENAI_API_KEY", "OPENAI_ORG_ID"],
        url="https://platform.openai.com/api-keys",
        path="API Keys → Create new secret key",
        docs="https://platform.openai.com/docs/api-reference/authentication",
        tips=[
            "API keys start with sk-",
            "Set usage limits to prevent unexpected charges",
            "Organization ID is optional but useful for billing separation",
        ],
        validation={
            "OPENAI_API_KEY": KeyValidation(
                pattern=r"^sk-[A-Za-z0-9_-]{32,}$",
                example="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            ),
            "OPENAI_ORG_ID": KeyValidation(
                pattern=r"^org-[A-Za-z0-9]+$",
                example="org-xxxxxxxxxxxxxxxx",
            ),
        },
    ),
    "anthropic": Provider(
        id="anthropic",
        name="Anthropic Console",
        keys=["ANTHROPIC_API_KEY"],
        url="https://console.anthropic.com/settings/keys",
        path="Settings → API Keys → Create Key",
        docs="https://docs.anthropic.com/en/docs/getting-started",
        tips=[
            "API keys start with sk-ant-",
            "Set monthly spending limits in the console",
        ],
        validation={
            "ANTHROPIC_API_KEY": KeyValidation(
                pattern=r"^sk-ant-[A-Za-z0-9-]{32,}$",
                example="sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # Email Providers
    # -------------------------------------------------------------------------
    "sendgrid": Provider(
        id="sendgrid",
        name="SendGrid",
        keys=["SENDGRID_API_KEY"],
        url="https://app.sendgrid.com/settings/api_keys",
        path="Settings → API Keys → Create API Key",
        docs="https://docs.sendgrid.com/api-reference/api-keys/create-api-keys",
        tips=[
            "Create API keys with minimal required permissions",
            "Use separate keys for different environments",
        ],
        validation={
            "SENDGRID_API_KEY": KeyValidation(
                pattern=r"^SG\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
                example="SG.xxxxxxx.yyyyyyy",
            ),
        },
    ),
    "resend": Provider(
        id="resend",
        name="Resend",
        keys=["RESEND_API_KEY"],
        url="https://resend.com/api-keys",
        path="API Keys → Create API Key",
        docs="https://resend.com/docs/api-reference/api-keys",
        tips=[
            "API keys start with re_",
            "Use domain verification for production sending",
        ],
        validation={
            "RESEND_API_KEY": KeyValidation(
                pattern=r"^re_[A-Za-z0-9_]+$",
                example="re_xxxxxxxxxxxxxxxx",
            ),
        },
    ),
    "postmark": Provider(
        id="postmark",
        name="Postmark",
        keys=["POSTMARK_API_KEY", "POSTMARK_SERVER_TOKEN"],
        url="https://account.postmarkapp.com/servers",
        path="Servers → [Your Server] → API Tokens",
        docs="https://postmarkapp.com/developer",
        tips=[
            "Server tokens are for sending from a specific server",
            "Account tokens are for account-level operations",
        ],
        validation={
            "POSTMARK_API_KEY": KeyValidation(
                pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
                example="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            ),
            "POSTMARK_SERVER_TOKEN": KeyValidation(
                pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
                example="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            ),
        },
    ),
    "mailgun": Provider(
        id="mailgun",
        name="Mailgun",
        keys=["MAILGUN_API_KEY", "MAILGUN_DOMAIN"],
        url="https://app.mailgun.com/app/account/security/api_keys",
        path="Settings → API Keys",
        docs="https://documentation.mailgun.com/en/latest/api-intro.html",
        tips=[
            "Use domain-specific sending keys when possible",
            "Verify your sending domain for better deliverability",
        ],
        validation={
            "MAILGUN_API_KEY": KeyValidation(
                pattern=r"^[a-f0-9]{32}-[a-f0-9]{8}-[a-f0-9]{8}$",
                example="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx-xxxxxxxx-xxxxxxxx",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # SMS/Communication Providers
    # -------------------------------------------------------------------------
    "twilio": Provider(
        id="twilio",
        name="Twilio Console",
        keys=["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"],
        url="https://console.twilio.com/",
        path="Account → API Keys & Tokens",
        docs="https://www.twilio.com/docs/iam/api-keys",
        tips=[
            "Account SID and Auth Token are on the Console dashboard",
            "Consider using API Keys instead of Auth Token for better security",
        ],
        validation={
            "TWILIO_ACCOUNT_SID": KeyValidation(
                pattern=r"^AC[a-f0-9]{32}$",
                example="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            ),
            "TWILIO_AUTH_TOKEN": KeyValidation(
                pattern=r"^[a-f0-9]{32}$",
                example="32-character hex string",
            ),
            "TWILIO_PHONE_NUMBER": KeyValidation(
                pattern=r"^\+[0-9]{10,15}$",
                example="+15551234567",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # Database/Backend Services
    # -------------------------------------------------------------------------
    "supabase": Provider(
        id="supabase",
        name="Supabase",
        keys=["SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"],
        url="https://supabase.com/dashboard/project/_/settings/api",
        path="Project Settings → API",
        docs="https://supabase.com/docs/guides/api",
        tips=[
            "anon key is safe for client-side use (with RLS enabled)",
            "service_role key bypasses RLS - keep it server-side only",
            "URL format: https://[project-ref].supabase.co",
        ],
        validation={
            "SUPABASE_URL": KeyValidation(
                pattern=r"^https://[a-z0-9]+\.supabase\.co$",
                example="https://abcdefghij.supabase.co",
            ),
            "SUPABASE_ANON_KEY": KeyValidation(
                pattern=r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
                example="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            ),
            "SUPABASE_SERVICE_ROLE_KEY": KeyValidation(
                pattern=r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
                example="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            ),
        },
    ),
    "firebase": Provider(
        id="firebase",
        name="Firebase Console",
        keys=["FIREBASE_API_KEY", "FIREBASE_PROJECT_ID", "FIREBASE_AUTH_DOMAIN"],
        url="https://console.firebase.google.com/",
        path="Project Settings → General → Your apps",
        docs="https://firebase.google.com/docs/web/setup",
        tips=[
            "API key is safe to expose (security comes from Firebase rules)",
            "Use service account for server-side operations",
        ],
        validation={
            "FIREBASE_API_KEY": KeyValidation(
                pattern=r"^AIza[A-Za-z0-9_-]{35}$",
                example="AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            ),
            "FIREBASE_PROJECT_ID": KeyValidation(
                pattern=r"^[a-z0-9-]+$",
                example="my-project-12345",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # Search/Analytics
    # -------------------------------------------------------------------------
    "algolia": Provider(
        id="algolia",
        name="Algolia",
        keys=["ALGOLIA_APP_ID", "ALGOLIA_API_KEY", "ALGOLIA_SEARCH_KEY"],
        url="https://dashboard.algolia.com/account/api-keys",
        path="Settings → API Keys",
        docs="https://www.algolia.com/doc/guides/security/api-keys/",
        tips=[
            "Use Search-only API key for client-side code",
            "Admin API key should only be used server-side",
        ],
        validation={
            "ALGOLIA_APP_ID": KeyValidation(
                pattern=r"^[A-Z0-9]{10}$",
                example="ABCDEF1234",
            ),
            "ALGOLIA_API_KEY": KeyValidation(
                pattern=r"^[a-f0-9]{32}$",
                example="32-character hex string",
            ),
            "ALGOLIA_SEARCH_KEY": KeyValidation(
                pattern=r"^[a-f0-9]{32}$",
                example="32-character hex string",
            ),
        },
    ),
    "sentry": Provider(
        id="sentry",
        name="Sentry",
        keys=["SENTRY_DSN", "SENTRY_AUTH_TOKEN"],
        url="https://sentry.io/settings/",
        path="Projects → [Project] → Client Keys (DSN)",
        docs="https://docs.sentry.io/product/sentry-basics/dsn-explainer/",
        tips=[
            "DSN is safe to include in client-side code",
            "Auth tokens are for API access (release management, etc.)",
        ],
        validation={
            "SENTRY_DSN": KeyValidation(
                pattern=r"^https://[a-f0-9]+@[a-z0-9.]+\.ingest\.sentry\.io/[0-9]+$",
                example="https://xxxxx@xxxxx.ingest.sentry.io/12345",
            ),
            "SENTRY_AUTH_TOKEN": KeyValidation(
                pattern=r"^sntrys_[A-Za-z0-9]+$",
                example="sntrys_xxxxxxxxxxxxxxxx",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # Maps/Location
    # -------------------------------------------------------------------------
    "mapbox": Provider(
        id="mapbox",
        name="Mapbox",
        keys=["MAPBOX_ACCESS_TOKEN"],
        url="https://account.mapbox.com/access-tokens/",
        path="Account → Access tokens",
        docs="https://docs.mapbox.com/help/getting-started/access-tokens/",
        tips=[
            "Use URL restrictions to limit token usage",
            "Create separate tokens for different environments",
        ],
        validation={
            "MAPBOX_ACCESS_TOKEN": KeyValidation(
                pattern=r"^pk\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+$",
                example="pk.xxxxxxx.yyyyyyy",
            ),
        },
    ),
    "google_maps": Provider(
        id="google_maps",
        name="Google Maps Platform",
        keys=["GOOGLE_MAPS_API_KEY"],
        url="https://console.cloud.google.com/google/maps-apis/credentials",
        path="Google Maps Platform → Credentials",
        docs="https://developers.google.com/maps/documentation/javascript/get-api-key",
        tips=[
            "Restrict API key to specific APIs and domains",
            "Set up billing alerts to prevent unexpected charges",
        ],
        validation={
            "GOOGLE_MAPS_API_KEY": KeyValidation(
                pattern=r"^AIza[A-Za-z0-9_-]{35}$",
                example="AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            ),
        },
    ),
    # -------------------------------------------------------------------------
    # Storage
    # -------------------------------------------------------------------------
    "uploadthing": Provider(
        id="uploadthing",
        name="UploadThing",
        keys=["UPLOADTHING_SECRET", "UPLOADTHING_APP_ID"],
        url="https://uploadthing.com/dashboard",
        path="Dashboard → API Keys",
        docs="https://docs.uploadthing.com/",
        tips=[
            "Secret key should never be exposed client-side",
        ],
        validation={
            "UPLOADTHING_SECRET": KeyValidation(
                pattern=r"^sk_[a-z]+_[A-Za-z0-9]+$",
                example="sk_live_xxxxxxxxxxxxxxxx",
                test_pattern=r"^sk_test_",
                live_pattern=r"^sk_live_",
            ),
        },
    ),
    "cloudinary": Provider(
        id="cloudinary",
        name="Cloudinary",
        keys=["CLOUDINARY_URL", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "CLOUDINARY_CLOUD_NAME"],
        url="https://console.cloudinary.com/settings/api-keys",
        path="Settings → API Keys",
        docs="https://cloudinary.com/documentation/admin_api",
        tips=[
            "CLOUDINARY_URL contains all credentials in one string",
            "Cloud name is public and safe to expose",
        ],
        validation={
            "CLOUDINARY_URL": KeyValidation(
                pattern=r"^cloudinary://[0-9]+:[A-Za-z0-9_-]+@[a-z0-9-]+$",
                example="cloudinary://123456789012345:abcdefghijklmnop@cloudname",
            ),
            "CLOUDINARY_API_KEY": KeyValidation(
                pattern=r"^[0-9]{15}$",
                example="123456789012345",
            ),
        },
    ),
}


# =============================================================================
# Helper Functions
# =============================================================================

def get_provider(provider_id: str) -> Provider | None:
    """Get a provider by ID."""
    return PROVIDERS.get(provider_id)


def get_provider_for_key(key: str) -> Provider | None:
    """Find the provider that owns a specific key."""
    for provider in PROVIDERS.values():
        if key in provider.keys:
            return provider
    return None


def get_all_providers() -> list[Provider]:
    """Get all providers."""
    return list(PROVIDERS.values())


def get_provider_ids() -> list[str]:
    """Get all provider IDs."""
    return list(PROVIDERS.keys())


def validate_secret(key: str, value: str) -> dict[str, Any]:
    """Validate a secret value against known patterns.

    Args:
        key: The secret key name
        value: The secret value

    Returns:
        Dict with validation results:
        - valid: bool - Overall validity
        - format_valid: bool | None - Pattern match (None if no pattern)
        - key_type: str - "test", "live", or "unknown"
        - warnings: list[str] - Warning messages
        - provider: str | None - Provider ID if found
    """
    provider = get_provider_for_key(key)

    result: dict[str, Any] = {
        "valid": True,
        "format_valid": None,
        "key_type": "unknown",
        "warnings": [],
        "provider": provider.id if provider else None,
    }

    if not provider:
        # No known validation for this key
        return result

    key_validation = provider.get_key_validation(key)
    if not key_validation:
        return result

    # Run validation
    validation_result = key_validation.validate(value)
    result.update(validation_result)

    return result


def detect_provider_from_env_key(key: str) -> str | None:
    """Try to detect provider from an environment variable key name.

    Uses heuristics to guess the provider from common key naming patterns.

    Args:
        key: Environment variable key name

    Returns:
        Provider ID or None if not detected
    """
    key_upper = key.upper()

    # Direct mappings
    key_to_provider = {
        "GOOGLE_CLIENT_ID": "google_oauth",
        "GOOGLE_CLIENT_SECRET": "google_oauth",
        "APPLE_CLIENT_ID": "apple_oauth",
        "APPLE_CLIENT_SECRET": "apple_oauth",
        "APPLE_TEAM_ID": "apple_oauth",
        "STRIPE_API_KEY": "stripe",
        "STRIPE_SECRET_KEY": "stripe",
        "STRIPE_PUBLISHABLE_KEY": "stripe",
        "STRIPE_WEBHOOK_SECRET": "stripe",
        "SENDGRID_API_KEY": "sendgrid",
        "RESEND_API_KEY": "resend",
        "POSTMARK_API_KEY": "postmark",
        "POSTMARK_SERVER_TOKEN": "postmark",
        "TWILIO_ACCOUNT_SID": "twilio",
        "TWILIO_AUTH_TOKEN": "twilio",
        "AWS_ACCESS_KEY_ID": "aws",
        "AWS_SECRET_ACCESS_KEY": "aws",
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "GITHUB_CLIENT_ID": "github",
        "GITHUB_CLIENT_SECRET": "github",
        "GITHUB_TOKEN": "github",
        "DISCORD_CLIENT_ID": "discord",
        "DISCORD_CLIENT_SECRET": "discord",
        "DISCORD_BOT_TOKEN": "discord",
        "SLACK_CLIENT_ID": "slack",
        "SLACK_CLIENT_SECRET": "slack",
        "SLACK_SIGNING_SECRET": "slack",
        "SLACK_BOT_TOKEN": "slack",
        "SUPABASE_URL": "supabase",
        "SUPABASE_ANON_KEY": "supabase",
        "SUPABASE_SERVICE_ROLE_KEY": "supabase",
        "FIREBASE_API_KEY": "firebase",
        "FIREBASE_PROJECT_ID": "firebase",
        "PLAID_CLIENT_ID": "plaid",
        "PLAID_SECRET": "plaid",
        "MAPBOX_ACCESS_TOKEN": "mapbox",
        "ALGOLIA_APP_ID": "algolia",
        "ALGOLIA_API_KEY": "algolia",
        "SENTRY_DSN": "sentry",
        "CLOUDFLARE_API_TOKEN": "cloudflare",
        "CLOUDFLARE_ZONE_ID": "cloudflare",
        "CLOUDINARY_URL": "cloudinary",
        "GOOGLE_MAPS_API_KEY": "google_maps",
        "MAILGUN_API_KEY": "mailgun",
        "UPLOADTHING_SECRET": "uploadthing",
    }

    if key_upper in key_to_provider:
        return key_to_provider[key_upper]

    # Prefix-based detection
    prefix_to_provider = {
        "STRIPE_": "stripe",
        "SENDGRID_": "sendgrid",
        "TWILIO_": "twilio",
        "AWS_": "aws",
        "OPENAI_": "openai",
        "ANTHROPIC_": "anthropic",
        "GITHUB_": "github",
        "DISCORD_": "discord",
        "SLACK_": "slack",
        "SUPABASE_": "supabase",
        "FIREBASE_": "firebase",
        "PLAID_": "plaid",
        "MAPBOX_": "mapbox",
        "ALGOLIA_": "algolia",
        "SENTRY_": "sentry",
        "CLOUDFLARE_": "cloudflare",
        "CLOUDINARY_": "cloudinary",
        "RESEND_": "resend",
        "POSTMARK_": "postmark",
        "MAILGUN_": "mailgun",
    }

    for prefix, provider_id in prefix_to_provider.items():
        if key_upper.startswith(prefix):
            return provider_id

    return None
