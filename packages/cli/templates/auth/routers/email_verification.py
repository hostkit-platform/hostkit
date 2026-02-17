"""Email verification endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.user import User
from models.magic_link import EmailVerification
from schemas.auth import (
    MessageResponse,
    VerifyEmailRequest,
    VerifyEmailSendRequest,
)
from services.email_service import get_email_service
from services.password_service import get_password_service

router = APIRouter(prefix="/auth", tags=["email-verification"])

EMAIL_VERIFY_EXPIRE_HOURS = 24


@router.post(
    "/verify-email/send",
    response_model=MessageResponse,
    summary="Send email verification",
)
async def send_verification(
    request: VerifyEmailSendRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Send an email verification link.

    - Generates secure token
    - Stores hashed token in database
    - Sends email with verification link
    - Always returns success (prevents email enumeration)
    """
    settings = get_settings()

    if not settings.email_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email authentication is not enabled",
        )

    password_service = get_password_service()

    # Check if user exists
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if user and not user.email_verified:
        # Generate secure token
        token = password_service.generate_secure_token(32)
        token_hash = password_service.hash_token(token)

        # Store verification
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=EMAIL_VERIFY_EXPIRE_HOURS
        )

        verification = EmailVerification(
            email=request.email,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(verification)
        await db.flush()

        # Send email
        email_service = get_email_service()
        email_service.send_verification_email(
            to_email=request.email,
            token=token,
            redirect_url=request.redirect_url,
        )

    # Always return success to prevent email enumeration
    return MessageResponse(
        message="If an account exists with that email, a verification link has been sent",
        success=True,
    )


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    summary="Verify email with token",
)
async def verify_email(
    request: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Verify a user's email address using a verification token.

    - Validates token hasn't been used or expired
    - Marks user's email as verified
    - Marks token as verified
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

    # Find the verification
    result = await db.execute(
        select(EmailVerification).where(EmailVerification.token_hash == token_hash)
    )
    verification = result.scalar_one_or_none()

    if not verification:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    if verification.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email has already been verified",
        )

    if verification.is_expired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has expired",
        )

    # Find user
    result = await db.execute(select(User).where(User.email == verification.email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    # Mark email as verified
    user.email_verified = True

    # Mark verification as used
    verification.verified_at = datetime.now(timezone.utc)

    return MessageResponse(
        message="Email has been verified successfully",
        success=True,
    )
