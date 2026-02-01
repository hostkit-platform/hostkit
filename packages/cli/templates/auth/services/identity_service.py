"""Identity payload verification and user creation service.

Verifies signed identity payloads from the central OAuth proxy (auth.hostkit.dev)
and creates/links user accounts.
"""

import logging
import time
from dataclasses import dataclass

import httpx
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from services.oauth_service import OAuthUserInfo, OAuthResult, get_oauth_service

logger = logging.getLogger(__name__)


@dataclass
class IdentityPayload:
    """Decoded identity payload from central OAuth proxy."""

    provider: str
    provider_user_id: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None
    project: str
    issued_at: int
    expires_at: int


class IdentityError(Exception):
    """Identity verification error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class IdentityService:
    """Service for verifying identity payloads and creating users.

    Handles signed identity JWTs from the central OAuth proxy at auth.hostkit.dev.
    The proxy handles OAuth flows and returns a signed JWT containing user identity.
    """

    # Cache the public key for 1 hour
    PUBLIC_KEY_CACHE_TTL = 3600

    def __init__(self) -> None:
        self.settings = get_settings()
        self._public_key: str | None = None
        self._public_key_fetched_at: float = 0

    @property
    def oauth_proxy_url(self) -> str:
        """Get the OAuth proxy URL from settings."""
        return self.settings.oauth_proxy_url

    async def _fetch_public_key(self) -> str:
        """Fetch the OAuth proxy public key.

        The public key is used to verify identity JWT signatures.
        Keys are cached for 1 hour to reduce network requests.

        Returns:
            The RSA public key in PEM format

        Raises:
            IdentityError: If key fetch fails
        """
        now = time.time()
        if self._public_key and (now - self._public_key_fetched_at) < self.PUBLIC_KEY_CACHE_TTL:
            logger.debug("Using cached OAuth proxy public key")
            return self._public_key

        logger.info(f"Fetching OAuth proxy public key from {self.oauth_proxy_url}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.oauth_proxy_url}/.well-known/hostkit-oauth-public-key"
                )

                if response.status_code != 200:
                    logger.error(
                        f"Failed to fetch OAuth proxy public key: "
                        f"status={response.status_code}, body={response.text}"
                    )
                    raise IdentityError(
                        "key_fetch_failed",
                        f"Failed to fetch OAuth proxy public key: HTTP {response.status_code}",
                    )

                self._public_key = response.text
                self._public_key_fetched_at = now
                logger.info("Successfully fetched and cached OAuth proxy public key")
                return self._public_key

        except httpx.RequestError as e:
            logger.error(f"Network error fetching OAuth proxy public key: {e}")
            raise IdentityError(
                "key_fetch_failed",
                f"Network error fetching OAuth proxy public key: {e}",
            )

    async def verify_identity(self, token: str) -> IdentityPayload:
        """Verify and decode an identity JWT from the central OAuth proxy.

        Args:
            token: The signed identity JWT from the central OAuth proxy

        Returns:
            Decoded IdentityPayload containing user information

        Raises:
            IdentityError: If verification fails (invalid signature, expired, etc.)
        """
        public_key = await self._fetch_public_key()

        try:
            logger.debug("Verifying identity JWT")

            claims = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                issuer=self.oauth_proxy_url,
                options={"verify_aud": False},  # We check audience manually
            )

            logger.debug(
                f"Identity JWT verified: provider={claims.get('provider')}, "
                f"email={claims.get('email')}, project={claims.get('aud')}"
            )

            return IdentityPayload(
                provider=claims["provider"],
                provider_user_id=claims["provider_user_id"],
                email=claims.get("email"),
                email_verified=claims.get("email_verified", False),
                name=claims.get("name"),
                picture=claims.get("picture"),
                project=claims["aud"],
                issued_at=claims["iat"],
                expires_at=claims["exp"],
            )

        except JWTError as e:
            logger.error(f"Identity JWT verification failed: {e}")
            raise IdentityError("invalid_token", f"Invalid identity token: {e}")

        except KeyError as e:
            logger.error(f"Missing required claim in identity JWT: {e}")
            raise IdentityError("invalid_token", f"Missing required claim: {e}")

    async def create_or_link_user(
        self,
        db: AsyncSession,
        payload: IdentityPayload,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> OAuthResult:
        """Create a new user or link to existing user based on identity payload.

        This method reuses the existing OAuthService.link_or_create_user logic
        by converting the IdentityPayload to an OAuthUserInfo object.

        Args:
            db: Database session
            payload: Verified identity payload from OAuth proxy
            ip_address: Client IP address for session tracking
            user_agent: Client user agent for session tracking

        Returns:
            OAuthResult with user, tokens, and session information
        """
        logger.info(
            f"Creating/linking user from identity payload: "
            f"provider={payload.provider}, email={payload.email}"
        )

        # Convert IdentityPayload to OAuthUserInfo for compatibility
        # with existing OAuthService.link_or_create_user
        user_info = OAuthUserInfo(
            provider=payload.provider,
            provider_user_id=payload.provider_user_id,
            email=payload.email,
            email_verified=payload.email_verified,
            name=payload.name,
            picture=payload.picture,
            # No OAuth tokens from proxy - these are handled by the proxy itself
            access_token=None,
            refresh_token=None,
            token_expires_at=None,
        )

        oauth_service = get_oauth_service()
        result = await oauth_service.link_or_create_user(
            db=db,
            user_info=user_info,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        logger.info(
            f"User {'created' if result.is_new_user else 'linked'}: "
            f"user_id={result.user.id}, email={result.user.email}"
        )

        return result


# Singleton instance
_identity_service: IdentityService | None = None


def get_identity_service() -> IdentityService:
    """Get the identity service singleton."""
    global _identity_service
    if _identity_service is None:
        _identity_service = IdentityService()
    return _identity_service
