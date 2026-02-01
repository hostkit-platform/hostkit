"""FastAPI dependencies for authentication."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from services.jwt_service import get_jwt_service

# Bearer token security scheme
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get the currently authenticated user from the JWT token.

    Args:
        credentials: Bearer token credentials
        db: Database session

    Returns:
        The authenticated User

    Raises:
        HTTPException: If token is missing, invalid, or user not found
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwt_service = get_jwt_service()

    try:
        payload = jwt_service.decode_token(credentials.credentials, token_type="access")
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch user from database
    try:
        result = await db.execute(select(User).where(User.id == UUID(user_id)))
        user = result.scalar_one_or_none()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: invalid user ID",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Get the current user if authenticated, None otherwise.

    Useful for endpoints that work with or without authentication.

    Args:
        credentials: Bearer token credentials (optional)
        db: Database session

    Returns:
        The authenticated User or None
    """
    if not credentials:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def get_verified_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current user, requiring email verification.

    Args:
        current_user: The authenticated user

    Returns:
        The user if email is verified

    Raises:
        HTTPException: If email is not verified
    """
    if not current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required",
        )
    return current_user
