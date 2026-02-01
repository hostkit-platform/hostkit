"""Password hashing and verification service."""

import hashlib
import secrets

import bcrypt


class PasswordService:
    """Service for password hashing and secure token generation.

    Uses bcrypt for password hashing with automatic salting.
    """

    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            Bcrypt hash string
        """
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against its hash.

        Args:
            password: Plain text password
            password_hash: Bcrypt hash to verify against

        Returns:
            True if password matches
        """
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                password_hash.encode("utf-8"),
            )
        except Exception:
            return False

    def generate_secure_token(self, length: int = 32) -> str:
        """Generate a cryptographically secure token.

        Args:
            length: Number of random bytes (token will be ~1.3x longer when base64 encoded)

        Returns:
            URL-safe base64 encoded token
        """
        return secrets.token_urlsafe(length)

    def hash_token(self, token: str) -> str:
        """Hash a token using SHA-256.

        Used for storing magic links and other one-time tokens.

        Args:
            token: The token to hash

        Returns:
            Hex-encoded SHA-256 hash
        """
        return hashlib.sha256(token.encode()).hexdigest()

    def verify_token_hash(self, token: str, token_hash: str) -> bool:
        """Verify a token against its hash.

        Uses constant-time comparison to prevent timing attacks.

        Args:
            token: The token to verify
            token_hash: The stored hash

        Returns:
            True if token matches the hash
        """
        computed_hash = self.hash_token(token)
        return secrets.compare_digest(computed_hash, token_hash)


# Singleton instance
_password_service: PasswordService | None = None


def get_password_service() -> PasswordService:
    """Get the password service singleton."""
    global _password_service
    if _password_service is None:
        _password_service = PasswordService()
    return _password_service
