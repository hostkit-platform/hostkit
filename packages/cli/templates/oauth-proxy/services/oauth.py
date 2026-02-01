"""OAuth provider services for Google and Apple.

Handles OAuth flow logic, token exchange, and ID token validation.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from jose import jwt, JWTError

from config import get_settings, Settings

logger = logging.getLogger(__name__)


class OAuthError(Exception):
    """OAuth-related error."""

    def __init__(self, message: str, provider: str) -> None:
        super().__init__(message)
        self.provider = provider


@dataclass
class OAuthUserInfo:
    """Normalized user info from OAuth provider."""

    provider: str
    provider_user_id: str
    email: str | None
    email_verified: bool
    name: str | None = None
    picture: str | None = None


class GoogleOAuthService:
    """Google OAuth 2.0 service."""

    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
    GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

    SCOPES = ["openid", "email", "profile"]

    def __init__(self, settings: Settings) -> None:
        """Initialize the Google OAuth service.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self._google_keys: dict[str, Any] | None = None
        self._keys_fetched_at: float = 0

    def build_authorization_url(
        self,
        state: str,
        redirect_uri: str | None = None,
    ) -> str:
        """Build Google OAuth authorization URL.

        Args:
            state: Encrypted state containing project, return_url, etc.
            redirect_uri: Optional override for redirect URI

        Returns:
            Full authorization URL

        Raises:
            OAuthError: If Google OAuth is not configured
        """
        if not self.settings.google_client_id:
            raise OAuthError("Google OAuth is not configured", provider="google")

        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": redirect_uri or self.settings.google_callback_url,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.GOOGLE_AUTH_URL}?{query}"

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback
            redirect_uri: Must match the one used in authorization

        Returns:
            Token response with access_token, id_token, etc.

        Raises:
            OAuthError: If token exchange fails
        """
        if not self.settings.google_client_id:
            raise OAuthError("Google OAuth is not configured", provider="google")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri or self.settings.google_callback_url,
                },
            )

            if response.status_code != 200:
                logger.error(f"Google token exchange failed: {response.text}")
                raise OAuthError(
                    f"Google token exchange failed: {response.text}",
                    provider="google",
                )

            return response.json()

    async def _fetch_google_keys(self) -> dict[str, Any]:
        """Fetch Google's public keys for JWT verification.

        Keys are cached for 1 hour.

        Returns:
            Google's JWKS response
        """
        now = time.time()
        if self._google_keys and (now - self._keys_fetched_at) < 3600:
            return self._google_keys

        async with httpx.AsyncClient() as client:
            response = await client.get(self.GOOGLE_CERTS_URL)
            if response.status_code != 200:
                raise OAuthError(
                    "Failed to fetch Google public keys",
                    provider="google",
                )
            self._google_keys = response.json()
            self._keys_fetched_at = now
            return self._google_keys

    async def validate_id_token(
        self,
        id_token: str,
        access_token: str | None = None,
    ) -> OAuthUserInfo:
        """Validate Google ID token and extract user info.

        Args:
            id_token: The ID token from Google
            access_token: Optional access token for at_hash verification

        Returns:
            Normalized user info

        Raises:
            OAuthError: If token is invalid
        """
        keys = await self._fetch_google_keys()

        try:
            # Decode header to get key ID
            unverified_header = jwt.get_unverified_header(id_token)
            kid = unverified_header.get("kid")

            # Find the matching key
            key = None
            for jwk in keys.get("keys", []):
                if jwk.get("kid") == kid:
                    key = jwk
                    break

            if not key:
                raise OAuthError(
                    "No matching key found for Google ID token",
                    provider="google",
                )

            # Verify and decode
            payload = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.settings.google_client_id,
                issuer=["https://accounts.google.com", "accounts.google.com"],
                access_token=access_token,
            )

            logger.debug(
                f"Validated Google token for sub={payload['sub']}, "
                f"email={payload.get('email')}"
            )

            return OAuthUserInfo(
                provider="google",
                provider_user_id=payload["sub"],
                email=payload.get("email"),
                email_verified=payload.get("email_verified", False),
                name=payload.get("name"),
                picture=payload.get("picture"),
            )

        except JWTError as e:
            logger.error(f"Google ID token validation failed: {e}")
            raise OAuthError(
                f"Invalid Google ID token: {e}",
                provider="google",
            )


class AppleOAuthService:
    """Apple Sign-In service."""

    APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
    APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
    APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"

    SCOPES = ["name", "email"]

    def __init__(self, settings: Settings) -> None:
        """Initialize the Apple OAuth service.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self._apple_keys: dict[str, Any] | None = None
        self._keys_fetched_at: float = 0

    def build_authorization_url(
        self,
        state: str,
        redirect_uri: str | None = None,
    ) -> str:
        """Build Apple Sign-In authorization URL.

        Args:
            state: Encrypted state containing project, return_url, etc.
            redirect_uri: Optional override for callback URL

        Returns:
            Full authorization URL

        Raises:
            OAuthError: If Apple Sign-In is not configured
        """
        if not self.settings.apple_client_id:
            raise OAuthError("Apple Sign-In is not configured", provider="apple")

        callback_uri = redirect_uri or self.settings.apple_callback_url

        params = {
            "client_id": self.settings.apple_client_id,
            "redirect_uri": callback_uri,
            "response_type": "code id_token",
            "response_mode": "form_post",  # Apple uses POST for callback
            "scope": " ".join(self.SCOPES),
            "state": state,
        }

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.APPLE_AUTH_URL}?{query}"

    def _generate_client_secret(self) -> str:
        """Generate Apple client secret JWT.

        Apple requires a JWT signed with your private key as the client secret.

        Returns:
            Signed JWT client secret

        Raises:
            OAuthError: If Apple private key is not configured
        """
        now = datetime.now(timezone.utc)
        expire = now + timedelta(days=180)  # Max 6 months

        headers = {
            "alg": "ES256",
            "kid": self.settings.apple_key_id,
        }

        payload = {
            "iss": self.settings.apple_team_id,
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
            "aud": "https://appleid.apple.com",
            "sub": self.settings.apple_client_id,
        }

        private_key = self.settings.apple_private_key_content
        if not private_key:
            raise OAuthError(
                "Apple private key not configured",
                provider="apple",
            )

        return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback
            redirect_uri: Must match the one used in authorization

        Returns:
            Token response with access_token, id_token, etc.

        Raises:
            OAuthError: If token exchange fails
        """
        callback_uri = redirect_uri or self.settings.apple_callback_url
        client_secret = self._generate_client_secret()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.APPLE_TOKEN_URL,
                data={
                    "client_id": self.settings.apple_client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": callback_uri,
                },
            )

            if response.status_code != 200:
                logger.error(f"Apple token exchange failed: {response.text}")
                raise OAuthError(
                    f"Apple token exchange failed: {response.text}",
                    provider="apple",
                )

            return response.json()

    async def _fetch_apple_keys(self) -> dict[str, Any]:
        """Fetch Apple's public keys for JWT verification.

        Keys are cached for 1 hour.

        Returns:
            Apple's JWKS response
        """
        now = time.time()
        if self._apple_keys and (now - self._keys_fetched_at) < 3600:
            return self._apple_keys

        async with httpx.AsyncClient() as client:
            response = await client.get(self.APPLE_KEYS_URL)
            if response.status_code != 200:
                raise OAuthError(
                    "Failed to fetch Apple public keys",
                    provider="apple",
                )
            self._apple_keys = response.json()
            self._keys_fetched_at = now
            return self._apple_keys

    async def validate_id_token(
        self,
        id_token: str,
        user_data: str | None = None,
    ) -> OAuthUserInfo:
        """Validate Apple ID token and extract user info.

        Note: Apple only sends user info (name, email) on FIRST sign-in.
        After that, you must rely on the token claims.

        Args:
            id_token: The ID token from Apple
            user_data: JSON string with user info (only on first sign-in)

        Returns:
            Normalized user info

        Raises:
            OAuthError: If token is invalid
        """
        import json

        keys = await self._fetch_apple_keys()

        try:
            # Decode header to get key ID
            unverified_header = jwt.get_unverified_header(id_token)
            kid = unverified_header.get("kid")
            logger.debug(f"Apple token key ID: {kid}")

            # Find the matching key
            key = None
            for jwk in keys.get("keys", []):
                if jwk.get("kid") == kid:
                    key = jwk
                    break

            if not key:
                logger.warning(f"No matching Apple key found for kid: {kid}")
                raise OAuthError(
                    "No matching key found for Apple ID token",
                    provider="apple",
                )

            # Verify and decode
            payload = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.settings.apple_client_id,
                issuer="https://appleid.apple.com",
            )

            logger.debug(
                f"Validated Apple token for sub={payload['sub']}, "
                f"email={payload.get('email')}"
            )

            # Parse user data if provided (first sign-in only)
            name = None
            if user_data:
                try:
                    user_info = json.loads(user_data)
                    name_data = user_info.get("name", {})
                    first_name = name_data.get("firstName", "")
                    last_name = name_data.get("lastName", "")
                    if first_name or last_name:
                        name = f"{first_name} {last_name}".strip()
                    logger.debug(f"Parsed user name from user_data: {name}")
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug(f"Could not parse user_data: {e}")

            return OAuthUserInfo(
                provider="apple",
                provider_user_id=payload["sub"],
                email=payload.get("email"),
                email_verified=payload.get("email_verified", False),
                name=name,
            )

        except JWTError as e:
            logger.error(f"Apple ID token validation failed: {e}")
            raise OAuthError(
                f"Invalid Apple ID token: {e}",
                provider="apple",
            )


# Service factory functions
def get_google_service() -> GoogleOAuthService:
    """Get a Google OAuth service instance."""
    return GoogleOAuthService(get_settings())


def get_apple_service() -> AppleOAuthService:
    """Get an Apple OAuth service instance."""
    return AppleOAuthService(get_settings())
