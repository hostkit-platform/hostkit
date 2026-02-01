"""JWT service for creating and validating tokens."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from config import get_settings


class JWTService:
    """Service for creating and validating JWT tokens.

    Uses RS256 (RSA) for token signing with separate public/private keys.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    def create_access_token(
        self,
        user_id: UUID,
        email: str | None = None,
        is_anonymous: bool = False,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """Create a JWT access token.

        Args:
            user_id: User's UUID
            email: User's email (optional for anonymous users)
            is_anonymous: Whether user is anonymous
            extra_claims: Additional claims to include

        Returns:
            Signed JWT access token
        """
        now = datetime.now(timezone.utc)
        expire = now + timedelta(minutes=self.settings.access_token_expire_minutes)

        claims = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
            "type": "access",
        }

        if email:
            claims["email"] = email

        if is_anonymous:
            claims["anonymous"] = True

        if extra_claims:
            claims.update(extra_claims)

        return jwt.encode(
            claims,
            self.settings.jwt_private_key,
            algorithm="RS256",
        )

    def create_refresh_token(
        self,
        user_id: UUID,
        session_id: UUID,
    ) -> tuple[str, str]:
        """Create a refresh token.

        Returns both the token (for the client) and its hash (for storage).

        Args:
            user_id: User's UUID
            session_id: Session UUID for binding

        Returns:
            Tuple of (refresh_token, token_hash)
        """
        now = datetime.now(timezone.utc)
        expire = now + timedelta(days=self.settings.refresh_token_expire_days)

        # Include a random component for revocation tracking
        jti = secrets.token_urlsafe(16)

        claims = {
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
            "type": "refresh",
        }

        token = jwt.encode(
            claims,
            self.settings.jwt_private_key,
            algorithm="RS256",
        )

        # Hash the token for storage
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        return token, token_hash

    def decode_token(
        self,
        token: str,
        token_type: str = "access",
    ) -> dict[str, Any]:
        """Decode and validate a JWT token.

        Args:
            token: The JWT token to decode
            token_type: Expected token type ("access" or "refresh")

        Returns:
            Token payload

        Raises:
            JWTError: If token is invalid or wrong type
        """
        try:
            payload = jwt.decode(
                token,
                self.settings.jwt_public_key,
                algorithms=["RS256"],
            )

            # Verify token type
            if payload.get("type") != token_type:
                raise JWTError(f"Invalid token type: expected {token_type}")

            return payload

        except JWTError:
            raise

    def verify_refresh_token_hash(self, token: str, stored_hash: str) -> bool:
        """Verify a refresh token against its stored hash.

        Args:
            token: The refresh token
            stored_hash: The hash stored in the database

        Returns:
            True if token matches the hash
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return secrets.compare_digest(token_hash, stored_hash)

    def get_token_expiry(self, token_type: str = "access") -> datetime:
        """Get the expiry datetime for a token type.

        Args:
            token_type: "access" or "refresh"

        Returns:
            Expiry datetime
        """
        now = datetime.now(timezone.utc)
        if token_type == "refresh":
            return now + timedelta(days=self.settings.refresh_token_expire_days)
        return now + timedelta(minutes=self.settings.access_token_expire_minutes)


# Singleton instance
_jwt_service: JWTService | None = None


def get_jwt_service() -> JWTService:
    """Get the JWT service singleton."""
    global _jwt_service
    if _jwt_service is None:
        _jwt_service = JWTService()
    return _jwt_service
