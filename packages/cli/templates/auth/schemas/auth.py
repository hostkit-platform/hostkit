"""Pydantic schemas for auth service request/response validation."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# =============================================================================
# Common Response Schemas
# =============================================================================


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    success: bool = True


class ErrorResponse(BaseModel):
    """Error response schema."""

    detail: str
    code: str | None = None


# =============================================================================
# User Schemas
# =============================================================================


class UserResponse(BaseModel):
    """User information response."""

    id: UUID
    email: str | None = None
    email_verified: bool = False
    is_anonymous: bool = False
    created_at: datetime
    last_sign_in_at: datetime | None = None
    metadata_: dict[str, Any] | None = Field(default=None, alias="metadata_")

    model_config = {"from_attributes": True}


class UserUpdateRequest(BaseModel):
    """Request to update user profile."""

    email: EmailStr | None = None
    metadata_: dict[str, Any] | None = None


# =============================================================================
# Token Schemas
# =============================================================================


class TokenResponse(BaseModel):
    """JWT token pair response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Seconds until access token expires


class RefreshTokenRequest(BaseModel):
    """Request to refresh access token."""

    refresh_token: str


# =============================================================================
# Auth Schemas (Email/Password)
# =============================================================================


class SignupRequest(BaseModel):
    """Email/password signup request."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    metadata_: dict[str, Any] | None = None


class SigninRequest(BaseModel):
    """Email/password signin request."""

    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    """Authentication response with user and tokens."""

    user: UserResponse
    session: TokenResponse


# =============================================================================
# Magic Link Schemas
# =============================================================================


class MagicLinkRequest(BaseModel):
    """Request to send magic link."""

    email: EmailStr
    redirect_url: str | None = None


class MagicLinkVerifyRequest(BaseModel):
    """Request to verify magic link token."""

    token: str


# =============================================================================
# OAuth Schemas
# =============================================================================


class OAuthInitRequest(BaseModel):
    """Request to initiate OAuth flow."""

    redirect_uri: str | None = None
    final_redirect_uri: str | None = None
    state: str | None = None


class OAuthCallbackRequest(BaseModel):
    """OAuth callback request."""

    code: str
    state: str | None = None


class GoogleTokenVerifyRequest(BaseModel):
    """Request to verify Google ID token from native apps."""

    id_token: str
    ios_client_id: str | None = None
    access_token: str | None = None


class AppleTokenVerifyRequest(BaseModel):
    """Request to verify Apple ID token from native apps."""

    id_token: str
    bundle_id: str | None = None
    user: str | None = None  # User info JSON (only on first sign-in)


class AppleCallbackRequest(BaseModel):
    """Apple OAuth callback request (form data)."""

    code: str
    state: str | None = None
    id_token: str | None = None
    user: str | None = None


# =============================================================================
# Anonymous Auth Schemas
# =============================================================================


class AnonymousSignupResponse(BaseModel):
    """Anonymous signup response."""

    user: UserResponse
    session: TokenResponse


class AnonymousConvertRequest(BaseModel):
    """Request to convert anonymous account to full account."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


# =============================================================================
# Identity Schemas (Central OAuth Proxy)
# =============================================================================


class IdentityVerifyRequest(BaseModel):
    """Request to verify identity token from central OAuth proxy."""

    token: str


class IdentityVerifyResponse(BaseModel):
    """Response from identity verification."""

    user: UserResponse
    session: TokenResponse
    is_new_user: bool = False


# =============================================================================
# Password Reset Schemas
# =============================================================================


class ForgotPasswordRequest(BaseModel):
    """Request to send password reset email."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Request to reset password with token."""

    token: str
    password: str = Field(min_length=8, max_length=128)


# =============================================================================
# Email Verification Schemas
# =============================================================================


class VerifyEmailSendRequest(BaseModel):
    """Request to send email verification."""

    email: EmailStr
    redirect_url: str | None = None


class VerifyEmailRequest(BaseModel):
    """Request to verify email with token."""

    token: str
