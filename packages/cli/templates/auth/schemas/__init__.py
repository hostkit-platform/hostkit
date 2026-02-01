"""Pydantic schemas for request/response validation."""

from schemas.auth import (
    # Auth
    SignupRequest,
    SigninRequest,
    AuthResponse,
    TokenResponse,
    RefreshTokenRequest,
    # User
    UserResponse,
    UserUpdateRequest,
    # Magic Link
    MagicLinkRequest,
    MagicLinkVerifyRequest,
    # OAuth
    OAuthInitRequest,
    OAuthCallbackRequest,
    # Anonymous
    AnonymousSignupResponse,
    AnonymousConvertRequest,
    # Common
    MessageResponse,
    ErrorResponse,
)

__all__ = [
    "SignupRequest",
    "SigninRequest",
    "AuthResponse",
    "TokenResponse",
    "RefreshTokenRequest",
    "UserResponse",
    "UserUpdateRequest",
    "MagicLinkRequest",
    "MagicLinkVerifyRequest",
    "OAuthInitRequest",
    "OAuthCallbackRequest",
    "AnonymousSignupResponse",
    "AnonymousConvertRequest",
    "MessageResponse",
    "ErrorResponse",
]
