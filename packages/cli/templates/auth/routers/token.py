"""Token management endpoints (refresh, revoke)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.session import Session
from models.user import User
from schemas.auth import MessageResponse, RefreshTokenRequest, TokenResponse
from services.jwt_service import get_jwt_service

router = APIRouter(prefix="/auth/token", tags=["token"])


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(
    request: RefreshTokenRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Refresh an access token using a refresh token.

    - Validates refresh token
    - Checks session is not revoked or expired
    - Issues new access token
    - Optionally rotates refresh token
    """
    jwt_service = get_jwt_service()

    try:
        payload = jwt_service.decode_token(request.refresh_token, token_type="refresh")
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid refresh token: {e}",
        )

    session_id = payload.get("sid")
    user_id = payload.get("sub")

    if not session_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token: missing claims",
        )

    # Find session
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session not found",
        )

    # Verify token hash
    if not jwt_service.verify_refresh_token_hash(
        request.refresh_token, session.refresh_token_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Check session validity
    if not session.is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or revoked",
        )

    # Get user
    user_result = await db.execute(select(User).where(User.id == session.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Update session last used
    session.last_used_at = datetime.now(timezone.utc)

    # Create new access token
    access_token = jwt_service.create_access_token(
        user_id=user.id,
        email=user.email,
        is_anonymous=user.is_anonymous,
    )

    # Optionally rotate refresh token (more secure)
    new_refresh_token, new_refresh_hash = jwt_service.create_refresh_token(
        user_id=user.id,
        session_id=session.id,
    )
    session.refresh_token_hash = new_refresh_hash
    session.expires_at = jwt_service.get_token_expiry("refresh")

    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=jwt_service.settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/revoke",
    response_model=MessageResponse,
    summary="Revoke refresh token",
)
async def revoke_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Revoke a refresh token (sign out).

    - Validates refresh token
    - Marks session as revoked
    """
    jwt_service = get_jwt_service()

    try:
        payload = jwt_service.decode_token(request.refresh_token, token_type="refresh")
    except JWTError:
        # Token invalid, but that's fine for revocation
        return MessageResponse(message="Token revoked", success=True)

    session_id = payload.get("sid")

    if session_id:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()

        if session:
            session.revoked_at = datetime.now(timezone.utc)
            await db.commit()

    return MessageResponse(message="Token revoked", success=True)


@router.post(
    "/revoke-all",
    response_model=MessageResponse,
    summary="Revoke all sessions",
)
async def revoke_all_tokens(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Revoke all refresh tokens for the current user (sign out everywhere).

    - Requires authentication
    - Revokes all active sessions
    """
    result = await db.execute(
        select(Session).where(
            Session.user_id == current_user.id,
            Session.revoked_at.is_(None),
        )
    )
    sessions = result.scalars().all()

    for session in sessions:
        session.revoked_at = datetime.now(timezone.utc)

    await db.commit()

    return MessageResponse(
        message=f"Revoked {len(sessions)} session(s)",
        success=True,
    )
