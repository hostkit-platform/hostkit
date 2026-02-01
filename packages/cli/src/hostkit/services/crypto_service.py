"""Cryptographic services for HostKit secrets management.

This module provides AES-256-GCM encryption with Argon2id key derivation
for secure secrets storage.
"""

import os
import secrets
from pathlib import Path
from typing import NamedTuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# Constants
MASTER_KEY_PATH = Path("/etc/hostkit/master.key")
MASTER_KEY_LENGTH = 32  # 256 bits
SALT_LENGTH = 16  # 128 bits
NONCE_LENGTH = 12  # 96 bits (recommended for GCM)

# Argon2id parameters (OWASP recommendations for password hashing)
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536  # 64 MB
ARGON2_PARALLELISM = 4


class EncryptedData(NamedTuple):
    """Container for encrypted data with salt and nonce."""

    ciphertext: bytes
    salt: bytes
    nonce: bytes

    def to_bytes(self) -> bytes:
        """Serialize to bytes: salt + nonce + ciphertext."""
        return self.salt + self.nonce + self.ciphertext

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncryptedData":
        """Deserialize from bytes."""
        if len(data) < SALT_LENGTH + NONCE_LENGTH + 1:
            raise CryptoServiceError(
                code="INVALID_DATA",
                message="Encrypted data is too short to be valid",
            )
        salt = data[:SALT_LENGTH]
        nonce = data[SALT_LENGTH : SALT_LENGTH + NONCE_LENGTH]
        ciphertext = data[SALT_LENGTH + NONCE_LENGTH :]
        return cls(ciphertext=ciphertext, salt=salt, nonce=nonce)


class CryptoServiceError(Exception):
    """Exception for cryptographic service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class CryptoService:
    """Service for encrypting and decrypting secrets using AES-256-GCM."""

    def __init__(self, master_key_path: Path | None = None) -> None:
        """Initialize the crypto service.

        Args:
            master_key_path: Path to master key file (default: /etc/hostkit/master.key)
        """
        self.master_key_path = master_key_path or MASTER_KEY_PATH
        self._master_key: bytes | None = None

    def _ensure_master_key(self) -> bytes:
        """Ensure master key exists and load it.

        Returns:
            The master key bytes

        Raises:
            CryptoServiceError: If master key doesn't exist or is invalid
        """
        if self._master_key is not None:
            return self._master_key

        if not self.master_key_path.exists():
            raise CryptoServiceError(
                code="MASTER_KEY_NOT_FOUND",
                message=f"Master key not found at {self.master_key_path}",
                suggestion="Run 'hostkit secrets init' to generate a master key",
            )

        # Check permissions (should be 0600)
        mode = self.master_key_path.stat().st_mode & 0o777
        if mode != 0o600:
            raise CryptoServiceError(
                code="MASTER_KEY_INSECURE",
                message=f"Master key has insecure permissions: {oct(mode)}",
                suggestion=f"Run: chmod 600 {self.master_key_path}",
            )

        key = self.master_key_path.read_bytes()
        if len(key) != MASTER_KEY_LENGTH:
            raise CryptoServiceError(
                code="MASTER_KEY_INVALID",
                message=f"Master key has invalid length: {len(key)} (expected {MASTER_KEY_LENGTH})",
                suggestion="Generate a new master key with 'hostkit secrets init --force'",
            )

        self._master_key = key
        return key

    def generate_master_key(self, force: bool = False) -> Path:
        """Generate a new master key.

        Args:
            force: If True, overwrite existing key

        Returns:
            Path to the generated key file

        Raises:
            CryptoServiceError: If key exists and force is False
        """
        if self.master_key_path.exists() and not force:
            raise CryptoServiceError(
                code="MASTER_KEY_EXISTS",
                message=f"Master key already exists at {self.master_key_path}",
                suggestion="Use --force to regenerate (WARNING: existing secrets will be unreadable)",
            )

        # Ensure parent directory exists
        self.master_key_path.parent.mkdir(parents=True, exist_ok=True)

        # Generate cryptographically secure random key
        key = secrets.token_bytes(MASTER_KEY_LENGTH)

        # Write with secure permissions
        self.master_key_path.write_bytes(key)
        os.chmod(self.master_key_path, 0o600)

        # Clear cached key
        self._master_key = None

        return self.master_key_path

    def master_key_exists(self) -> bool:
        """Check if master key exists."""
        return self.master_key_path.exists()

    def _derive_key(self, salt: bytes, context: str = "") -> bytes:
        """Derive an encryption key from master key using Argon2id.

        Args:
            salt: Random salt for key derivation
            context: Optional context string (e.g., project name)

        Returns:
            Derived 256-bit key
        """
        try:
            from argon2.low_level import Type, hash_secret_raw
        except ImportError:
            raise CryptoServiceError(
                code="ARGON2_NOT_INSTALLED",
                message="argon2-cffi package is not installed",
                suggestion="Run: pip install argon2-cffi",
            )

        master_key = self._ensure_master_key()

        # Combine master key with context for additional binding
        secret = master_key + context.encode()

        derived_key = hash_secret_raw(
            secret=secret,
            salt=salt,
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            hash_len=MASTER_KEY_LENGTH,
            type=Type.ID,  # Argon2id
        )

        return derived_key

    def encrypt(self, plaintext: bytes, context: str = "") -> EncryptedData:
        """Encrypt data using AES-256-GCM.

        Args:
            plaintext: Data to encrypt
            context: Optional context string (e.g., project name) for key binding

        Returns:
            EncryptedData containing ciphertext, salt, and nonce
        """
        # Generate random salt and nonce
        salt = secrets.token_bytes(SALT_LENGTH)
        nonce = secrets.token_bytes(NONCE_LENGTH)

        # Derive encryption key
        key = self._derive_key(salt, context)

        # Encrypt with AES-256-GCM
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, context.encode() if context else None)

        return EncryptedData(ciphertext=ciphertext, salt=salt, nonce=nonce)

    def decrypt(self, encrypted: EncryptedData, context: str = "") -> bytes:
        """Decrypt data using AES-256-GCM.

        Args:
            encrypted: EncryptedData to decrypt
            context: Context string used during encryption

        Returns:
            Decrypted plaintext

        Raises:
            CryptoServiceError: If decryption fails (wrong key, corrupted data, etc.)
        """
        # Derive encryption key from salt
        key = self._derive_key(encrypted.salt, context)

        # Decrypt with AES-256-GCM
        aesgcm = AESGCM(key)
        try:
            plaintext = aesgcm.decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                context.encode() if context else None,
            )
        except Exception as e:
            raise CryptoServiceError(
                code="DECRYPTION_FAILED",
                message="Failed to decrypt data: authentication tag verification failed",
                suggestion="The data may be corrupted or encrypted with a different key",
            ) from e

        return plaintext

    def encrypt_string(self, plaintext: str, context: str = "") -> bytes:
        """Encrypt a string and return serialized bytes.

        Args:
            plaintext: String to encrypt
            context: Optional context string

        Returns:
            Serialized encrypted data (salt + nonce + ciphertext)
        """
        encrypted = self.encrypt(plaintext.encode(), context)
        return encrypted.to_bytes()

    def decrypt_string(self, data: bytes, context: str = "") -> str:
        """Decrypt serialized bytes to a string.

        Args:
            data: Serialized encrypted data
            context: Context string used during encryption

        Returns:
            Decrypted string
        """
        encrypted = EncryptedData.from_bytes(data)
        plaintext = self.decrypt(encrypted, context)
        return plaintext.decode()


# Global crypto service instance (loaded lazily)
_crypto: CryptoService | None = None


def get_crypto() -> CryptoService:
    """Get the global crypto service instance."""
    global _crypto
    if _crypto is None:
        _crypto = CryptoService()
    return _crypto
