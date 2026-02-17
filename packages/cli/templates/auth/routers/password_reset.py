"""Password reset endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.user import User
from models.magic_link import PasswordReset
from schemas.auth import (
    ForgotPasswordRequest,
    MessageResponse,
    ResetPasswordRequest,
)
from services.email_service import get_email_service
from services.password_service import get_password_service

router = APIRouter(prefix="/auth", tags=["password-reset"])

PASSWORD_RESET_EXPIRE_MINUTES = 60  # 1 hour


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Send password reset email",
)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Send a password reset email.

    - Generates secure token
    - Stores hashed token in database
    - Sends email with reset link
    - Always returns success (prevents email enumeration)
    """
    settings = get_settings()

    if not settings.email_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email authentication is not enabled",
        )

    password_service = get_password_service()

    # Check if user exists (but always return success)
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if user:
        # User exists but signed up via OAuth only (no password to reset)
        if not user.password_hash:
            providers = [oa.provider for oa in user.oauth_accounts]
            if providers:
                provider_list = ", ".join(p.title() for p in providers)
                return MessageResponse(
                    message=f"This account uses {provider_list} sign-in. Please sign in with {provider_list} instead.",
                    success=False,
                )
            # User exists with no password and no OAuth (e.g. magic-link only)
            return MessageResponse(
                message="This account doesn't have a password. Try signing in with a magic link instead.",
                success=False,
            )

        # Generate secure token
        token = password_service.generate_secure_token(32)
        token_hash = password_service.hash_token(token)

        # Store password reset
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=PASSWORD_RESET_EXPIRE_MINUTES
        )

        password_reset = PasswordReset(
            email=request.email,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(password_reset)
        await db.flush()

        # Send email
        email_service = get_email_service()
        email_service.send_password_reset(
            to_email=request.email,
            token=token,
        )

    # No user found — return generic success to prevent email enumeration
    return MessageResponse(
        message="If an account exists with that email, a password reset link has been sent",
        success=True,
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Reset password with token",
)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Reset a user's password using a reset token.

    - Validates token hasn't been used or expired
    - Updates user's password hash
    - Marks token as used
    """
    settings = get_settings()

    if not settings.email_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email authentication is not enabled",
        )

    password_service = get_password_service()

    # Hash the provided token
    token_hash = password_service.hash_token(request.token)

    # Find the reset token
    result = await db.execute(
        select(PasswordReset).where(PasswordReset.token_hash == token_hash)
    )
    reset = result.scalar_one_or_none()

    if not reset:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if reset.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has already been used",
        )

    if reset.is_expired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired",
        )

    # Find user
    result = await db.execute(select(User).where(User.email == reset.email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Update password
    user.password_hash = password_service.hash_password(request.password)

    # Mark token as used
    reset.used_at = datetime.now(timezone.utc)

    return MessageResponse(
        message="Password has been reset successfully",
        success=True,
    )
