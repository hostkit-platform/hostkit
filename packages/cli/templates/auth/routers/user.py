"""User profile endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from schemas.auth import MessageResponse, UserResponse, UserUpdateRequest

router = APIRouter(prefix="/auth/user", tags=["user"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Get the current authenticated user's profile.

    - Requires authentication
    - Returns user info
    """
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update current user",
)
async def update_me(
    request: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the current user's profile.

    - Requires authentication
    - Can update email (requires re-verification) and metadata
    """
    # Update email if provided
    if request.email and request.email != current_user.email:
        # Check if email already exists
        result = await db.execute(select(User).where(User.email == request.email))
        existing_user = result.scalar_one_or_none()

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

        current_user.email = request.email
        current_user.email_verified = False  # Requires re-verification

    # Update metadata if provided
    if request.metadata_ is not None:
        if current_user.metadata_:
            current_user.metadata_.update(request.metadata_)
        else:
            current_user.metadata_ = request.metadata_

    current_user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(current_user)

    return UserResponse.model_validate(current_user)


@router.delete(
    "/me",
    response_model=MessageResponse,
    summary="Delete current user",
)
async def delete_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Delete the current user's account.

    - Requires authentication
    - Permanently deletes user and all associated data
    """
    await db.delete(current_user)
    await db.commit()

    return MessageResponse(
        message="Account deleted successfully",
        success=True,
    )
