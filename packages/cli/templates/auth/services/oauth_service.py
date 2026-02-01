"""OAuth service for Google and Apple Sign-In.

Handles OAuth flow, token validation, and user creation/linking.
"""

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx
from jose import JWTError, jwt

logger = logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings, Settings
from models.oauth import OAuthAccount
from models.session import Session
from models.user import User


from services.jwt_service import get_jwt_service


# OAuth provider constants
class OAuthProvider:
    """OAuth provider identifiers."""
    GOOGLE = "google"
    APPLE = "apple"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class OAuthUserInfo:
    """Normalized user info from OAuth provider."""

    provider: str
    provider_user_id: str
    email: str | None
    email_verified: bool
    name: str | None = None
    picture: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None


@dataclass
class OAuthResult:
    """Result of OAuth authentication."""

    user: User
    oauth_account: OAuthAccount
    session: Session
    access_token: str
    refresh_token: str
    is_new_user: bool


# =============================================================================
# OAuth State Management (CSRF Protection)
# =============================================================================


class OAuthStateStore:
    """In-memory store for OAuth state tokens.

    In production, consider using Redis for distributed state storage.
    States expire after 10 minutes.
    """

    STATE_TTL_SECONDS = 600  # 10 minutes

    def __init__(self) -> None:
        self._states: dict[str, tuple[float, dict[str, Any]]] = {}

    def generate_state(self, data: dict[str, Any] | None = None) -> str:
        """Generate a secure state token.

        Args:
            data: Optional data to associate with the state

        Returns:
            URL-safe state token
        """
        self._cleanup_expired()
        state = secrets.token_urlsafe(32)
        self._states[state] = (time.time(), data or {})
        return state

    def validate_and_consume(self, state: str) -> dict[str, Any] | None:
        """Validate and consume a state token.

        Args:
            state: The state token to validate

        Returns:
            Associated data if valid, None if invalid or expired
        """
        self._cleanup_expired()

        if state not in self._states:
            return None

        created_at, data = self._states.pop(state)
        if time.time() - created_at > self.STATE_TTL_SECONDS:
            return None

        return data

    def _cleanup_expired(self) -> None:
        """Remove expired states."""
        now = time.time()
        expired = [
            state
            for state, (created_at, _) in self._states.items()
            if now - created_at > self.STATE_TTL_SECONDS
        ]
        for state in expired:
            self._states.pop(state, None)


# Global state store
_state_store = OAuthStateStore()


def get_state_store() -> OAuthStateStore:
    """Get the OAuth state store singleton."""
    return _state_store


# =============================================================================
# Google OAuth
# =============================================================================


class GoogleOAuthService:
    """Google OAuth 2.0 service."""

    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
    GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

    SCOPES = ["openid", "email", "profile"]

    def __init__(self, settings: Settings) -> None:
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
            state: CSRF state token
            redirect_uri: Optional override for redirect URI

        Returns:
            Full authorization URL
        """
        # Use web client ID for web OAuth flows
        client_id = self.settings.google_web_client
        if not client_id:
            raise OAuthError("Google OAuth is not configured", provider="google")

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri or self.settings.google_callback_url,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),
            "state": state,
            "access_type": "offline",  # Request refresh token
            "prompt": "consent",  # Force consent to get refresh token
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
        # Use web client ID for web OAuth flows
        client_id = self.settings.google_web_client
        if not client_id:
            raise OAuthError("Google OAuth is not configured", provider="google")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": self.settings.google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri or self.settings.google_callback_url,
                },
            )

            if response.status_code != 200:
                raise OAuthError(
                    f"Google token exchange failed: {response.text}",
                    provider="google",
                )

            return response.json()

    async def _fetch_google_keys(self) -> dict[str, Any]:
        """Fetch Google's public keys for JWT verification.

        Keys are cached for 1 hour.
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
        audience_override: str | None = None,
    ) -> OAuthUserInfo:
        """Validate Google ID token and extract user info.

        Args:
            id_token: The ID token from Google
            access_token: The access token from Google (for at_hash verification)
            audience_override: Optional client ID to use for audience validation
                              (e.g., iOS client ID). If not provided, uses the
                              configured web client ID.

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

            # Use audience_override if provided (for iOS/Android native apps),
            # otherwise use the configured web client ID
            audience = audience_override or self.settings.google_web_client

            # Verify and decode
            # Pass access_token for at_hash claim verification
            payload = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=audience,
                issuer=["https://accounts.google.com", "accounts.google.com"],
                access_token=access_token,
            )

            return OAuthUserInfo(
                provider=OAuthProvider.GOOGLE,
                provider_user_id=payload["sub"],
                email=payload.get("email"),
                email_verified=payload.get("email_verified", False),
                name=payload.get("name"),
                picture=payload.get("picture"),
            )

        except JWTError as e:
            raise OAuthError(
                f"Invalid Google ID token: {e}",
                provider="google",
            )

    async def get_user_info(self, access_token: str) -> OAuthUserInfo:
        """Get user info from Google userinfo endpoint.

        Fallback if ID token validation is not desired.

        Args:
            access_token: Google access token

        Returns:
            Normalized user info
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                raise OAuthError(
                    f"Failed to fetch Google user info: {response.text}",
                    provider="google",
                )

            data = response.json()
            return OAuthUserInfo(
                provider=OAuthProvider.GOOGLE,
                provider_user_id=data["sub"],
                email=data.get("email"),
                email_verified=data.get("email_verified", False),
                name=data.get("name"),
                picture=data.get("picture"),
            )


# =============================================================================
# Apple Sign-In
# =============================================================================


class AppleOAuthService:
    """Apple Sign-In service."""

    APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
    APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
    APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"

    SCOPES = ["name", "email"]

    def __init__(self, settings: Settings) -> None:
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
            state: CSRF state token
            redirect_uri: Callback URL

        Returns:
            Full authorization URL
        """
        # Use configured redirect URI or construct from base URL
        callback_uri = redirect_uri
        if not callback_uri:
            callback_uri = f"{self.settings.base_url}/auth/oauth/apple/callback"

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

        # Get Apple private key (supports both env var and file path)
        private_key = self.settings.apple_private_key_content
        if not private_key:
            raise OAuthError(
                "Apple private key not configured. Set APPLE_PRIVATE_KEY or APPLE_PRIVATE_KEY_PATH",
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
        callback_uri = redirect_uri
        if not callback_uri:
            callback_uri = f"{self.settings.base_url}/auth/oauth/apple/callback"

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
                raise OAuthError(
                    f"Apple token exchange failed: {response.text}",
                    provider="apple",
                )

            return response.json()

    async def _fetch_apple_keys(self) -> dict[str, Any]:
        """Fetch Apple's public keys for JWT verification.

        Keys are cached for 1 hour.
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
        audience_override: str | None = None,
    ) -> OAuthUserInfo:
        """Validate Apple ID token and extract user info.

        Note: Apple only sends user info (name, email) on FIRST sign-in.
        After that, you must rely on the token claims.

        Args:
            id_token: The ID token from Apple
            user_data: JSON string with user info (only on first sign-in)
            audience_override: Optional client ID to use for audience validation
                              (e.g., iOS Bundle ID). If not provided, uses the
                              configured Services ID (apple_client_id).

        Returns:
            Normalized user info

        Raises:
            OAuthError: If token is invalid
        """
        logger.debug("Validating Apple ID token")
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

            # Use audience_override if provided (for iOS native apps),
            # otherwise use the configured Services ID
            audience = audience_override or self.settings.apple_client_id

            # Verify and decode
            logger.debug(f"Validating against audience: {audience}")
            payload = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=audience,
                issuer="https://appleid.apple.com",
            )
            logger.debug(f"Apple token validated, sub: {payload.get('sub')}, email: {payload.get('email')}")

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
                    logger.debug(f"Parsed user name: {name}")
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug(f"Could not parse user_data: {e}")

            return OAuthUserInfo(
                provider=OAuthProvider.APPLE,
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


# =============================================================================
# Main OAuth Service
# =============================================================================


class OAuthError(Exception):
    """OAuth-related error."""

    def __init__(self, message: str, provider: str) -> None:
        super().__init__(message)
        self.provider = provider


class OAuthService:
    """Main OAuth service handling user creation and linking."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.google = GoogleOAuthService(self.settings)
        self.apple = AppleOAuthService(self.settings)
        self.jwt_service = get_jwt_service()
        self.state_store = get_state_store()

    async def link_or_create_user(
        self,
        db: AsyncSession,
        user_info: OAuthUserInfo,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> OAuthResult:
        """Link OAuth account to existing user or create new user.

        This method handles the following scenarios:
        1. OAuth account already exists -> update tokens, return existing user
        2. User exists with same email -> link OAuth account to existing user
        3. No existing user -> create new user with OAuth account

        Args:
            db: Database session
            user_info: Normalized user info from OAuth provider
            ip_address: Client IP address for session
            user_agent: Client user agent for session

        Returns:
            OAuthResult with user, tokens, and whether user is new
        """
        # 1. Check if OAuth account already exists
        oauth_query = select(OAuthAccount).where(
            OAuthAccount.provider == user_info.provider,
            OAuthAccount.provider_user_id == user_info.provider_user_id,
        )
        oauth_result = await db.execute(oauth_query)
        existing_oauth = oauth_result.scalar_one_or_none()

        if existing_oauth:
            # Update tokens
            existing_oauth.access_token = user_info.access_token
            existing_oauth.refresh_token = user_info.refresh_token
            existing_oauth.token_expires_at = user_info.token_expires_at
            if user_info.email:
                existing_oauth.provider_email = user_info.email

            # Get user
            user_query = select(User).where(User.id == existing_oauth.user_id)
            user_result = await db.execute(user_query)
            user = user_result.scalar_one()

            # Update last sign in
            user.last_sign_in_at = datetime.now(timezone.utc)

            # Create session
            session, access_token, refresh_token = await self._create_session(
                db, user, ip_address, user_agent
            )

            await db.commit()
            await db.refresh(user)
            await db.refresh(existing_oauth)

            return OAuthResult(
                user=user,
                oauth_account=existing_oauth,
                session=session,
                access_token=access_token,
                refresh_token=refresh_token,
                is_new_user=False,
            )

        # 2. Check if user exists with same email (for linking)
        user: User | None = None
        is_new_user = True

        if user_info.email:
            user_query = select(User).where(User.email == user_info.email)
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()

            if user:
                is_new_user = False
                # If email is verified from OAuth, mark as verified
                if user_info.email_verified and not user.email_verified:
                    user.email_verified = True

        # 3. Create new user if needed
        if not user:
            user = User(
                email=user_info.email,
                email_verified=user_info.email_verified,
                is_anonymous=False,
                metadata_={
                    "name": user_info.name,
                    "picture": user_info.picture,
                },
            )
            db.add(user)
            await db.flush()  # Get user ID

        # Create OAuth account link
        oauth_account = OAuthAccount(
            user_id=user.id,
            provider=user_info.provider,
            provider_user_id=user_info.provider_user_id,
            provider_email=user_info.email,
            access_token=user_info.access_token,
            refresh_token=user_info.refresh_token,
            token_expires_at=user_info.token_expires_at,
        )
        db.add(oauth_account)

        # Update last sign in
        user.last_sign_in_at = datetime.now(timezone.utc)

        # Create session
        session, access_token, refresh_token = await self._create_session(
            db, user, ip_address, user_agent
        )

        await db.commit()
        await db.refresh(user)
        await db.refresh(oauth_account)
        await db.refresh(session)

        return OAuthResult(
            user=user,
            oauth_account=oauth_account,
            session=session,
            access_token=access_token,
            refresh_token=refresh_token,
            is_new_user=is_new_user,
        )

    async def _create_session(
        self,
        db: AsyncSession,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[Session, str, str]:
        """Create a new session with tokens.

        Args:
            db: Database session
            user: User to create session for
            ip_address: Client IP
            user_agent: Client user agent

        Returns:
            Tuple of (Session, access_token, refresh_token)
        """
        # Create placeholder session to get ID
        session = Session(
            user_id=user.id,
            refresh_token_hash="placeholder",
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=self.jwt_service.get_token_expiry("refresh"),
        )
        db.add(session)
        await db.flush()  # Get session ID

        # Create tokens
        access_token = self.jwt_service.create_access_token(
            user_id=user.id,
            email=user.email,
            is_anonymous=user.is_anonymous,
        )
        refresh_token, refresh_hash = self.jwt_service.create_refresh_token(
            user_id=user.id,
            session_id=session.id,
        )

        # Update session with real hash
        session.refresh_token_hash = refresh_hash

        return session, access_token, refresh_token


# =============================================================================
# Singleton
# =============================================================================


_oauth_service: OAuthService | None = None


def get_oauth_service() -> OAuthService:
    """Get the OAuth service singleton."""
    global _oauth_service
    if _oauth_service is None:
        _oauth_service = OAuthService()
    return _oauth_service
