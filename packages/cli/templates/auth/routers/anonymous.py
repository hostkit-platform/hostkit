"""Anonymous authentication endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.user import User
from models.session import Session
from schemas.auth import (
    AnonymousConvertRequest,
    AnonymousSignupResponse,
    AuthResponse,
    TokenResponse,
    UserResponse,
)
from services.jwt_service import get_jwt_service
from services.password_service import get_password_service
from dependencies import get_current_user

router = APIRouter(prefix="/auth/anonymous", tags=["anonymous"])


@router.post(
    "/signup",
    response_model=AnonymousSignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create anonymous session",
)
async def anonymous_signup(
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> AnonymousSignupResponse:
    """Create an anonymous user session.

    - Creates user with is_anonymous=True
    - No email or password required
    - Can be converted to full account later
    - Returns access and refresh tokens
    """
    settings = get_settings()

    if not settings.anonymous_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Anonymous authentication is not enabled",
        )

    jwt_service = get_jwt_service()

    # Create anonymous user
    user = User(
        email=None,
        password_hash=None,
        email_verified=False,
        is_anonymous=True,
    )
    db.add(user)
    await db.flush()

    # Create session
    access_token = jwt_service.create_access_token(
        user_id=user.id,
        is_anonymous=True,
    )
    refresh_token, refresh_hash = jwt_service.create_refresh_token(
        user_id=user.id,
        session_id=user.id,
    )

    session = Session(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        ip_address=req.client.host if req.client else None,
        user_agent=req.headers.get("user-agent"),
        expires_at=jwt_service.get_token_expiry("refresh"),
    )
    db.add(session)
    await db.commit()

    return AnonymousSignupResponse(
        user=UserResponse.model_validate(user),
        session=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=jwt_service.settings.access_token_expire_minutes * 60,
        ),
    )


@router.post(
    "/convert",
    response_model=AuthResponse,
    summary="Convert anonymous account to full account",
)
async def convert_anonymous(
    request: AnonymousConvertRequest,
    current_user: User = Depends(get_current_user),
    req: Request = None,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Convert an anonymous account to a full account.

    - Requires authenticated anonymous user
    - Sets email and password
    - Marks account as non-anonymous
    - Returns new tokens
    """
    if not current_user.is_anonymous:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is not anonymous",
        )

    password_service = get_password_service()
    jwt_service = get_jwt_service()

    # Check if email already exists
    result = await db.execute(select(User).where(User.email == request.email))
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Update user
    current_user.email = request.email
    current_user.password_hash = password_service.hash_password(request.password)
    current_user.is_anonymous = False
    current_user.email_verified = False  # Needs verification
    current_user.updated_at = datetime.now(timezone.utc)

    # Create new session (invalidate old tokens)
    access_token = jwt_service.create_access_token(
        user_id=current_user.id,
        email=current_user.email,
    )
    refresh_token, refresh_hash = jwt_service.create_refresh_token(
        user_id=current_user.id,
        session_id=current_user.id,
    )

    session = Session(
        user_id=current_user.id,
        refresh_token_hash=refresh_hash,
        ip_address=req.client.host if req.client else None,
        user_agent=req.headers.get("user-agent") if req else None,
        expires_at=jwt_service.get_token_expiry("refresh"),
    )
    db.add(session)
    await db.commit()

    # TODO: Send verification email

    return AuthResponse(
        user=UserResponse.model_validate(current_user),
        session=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=jwt_service.settings.access_token_expire_minutes * 60,
        ),
    )
