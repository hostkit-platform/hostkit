"""FastAPI dependencies for authentication and database access."""

import hashlib
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models.project_key import ProjectKey


async def get_db() -> AsyncSession:
    """Database session dependency."""
    async for session in get_session():
        yield session


def hash_api_key(api_key: str) -> str:
    """Hash an API key for secure storage/lookup."""
    return hashlib.sha256(api_key.encode()).hexdigest()


async def get_api_key(
    authorization: Annotated[Optional[str], Header()] = None
) -> str:
    """Extract API key from Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Missing Authorization header",
                }
            }
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Invalid Authorization header format. Use: Bearer <api_key>",
                }
            }
        )

    return authorization[7:]  # Remove "Bearer " prefix


async def get_current_project(
    api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectKey:
    """Validate API key and return current project."""
    key_hash = hash_api_key(api_key)

    result = await db.execute(
        select(ProjectKey).where(ProjectKey.api_key_hash == key_hash)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Invalid API key",
                }
            }
        )

    if not project.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "FORBIDDEN",
                    "message": f"Claude access disabled for project '{project.project_name}'",
                }
            }
        )

    return project


# Type aliases for cleaner dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]
CurrentProject = Annotated[ProjectKey, Depends(get_current_project)]
