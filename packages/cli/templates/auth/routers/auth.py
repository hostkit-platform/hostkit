"""Email/password authentication endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.user import User
from models.session import Session
from schemas.auth import (
    AuthResponse,
    SigninRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)
from services.jwt_service import get_jwt_service
from services.password_service import get_password_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
)
async def signup(
    request: SignupRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Create a new user account with email and password.

    - Validates email is not already registered
    - Hashes password securely
    - Creates user and session
    - Returns access and refresh tokens
    """
    settings = get_settings()

    if not settings.email_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email authentication is not enabled",
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

    # Create user
    user = User(
        email=request.email,
        password_hash=password_service.hash_password(request.password),
        email_verified=False,
        is_anonymous=False,
        metadata_=request.metadata_,
    )
    db.add(user)
    await db.flush()

    # Create session
    access_token = jwt_service.create_access_token(
        user_id=user.id,
        email=user.email,
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

    return AuthResponse(
        user=UserResponse.model_validate(user),
        session=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=jwt_service.settings.access_token_expire_minutes * 60,
        ),
    )


@router.post(
    "/signin",
    response_model=AuthResponse,
    summary="Sign in with email and password",
)
async def signin(
    request: SigninRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Sign in with email and password.

    - Validates credentials
    - Creates new session
    - Returns access and refresh tokens
    """
    settings = get_settings()

    if not settings.email_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email authentication is not enabled",
        )

    password_service = get_password_service()
    jwt_service = get_jwt_service()

    # Find user
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Verify password
    if not password_service.verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Update last sign in
    user.last_sign_in_at = datetime.now(timezone.utc)

    # Create session
    access_token = jwt_service.create_access_token(
        user_id=user.id,
        email=user.email,
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

    return AuthResponse(
        user=UserResponse.model_validate(user),
        session=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=jwt_service.settings.access_token_expire_minutes * 60,
        ),
    )
