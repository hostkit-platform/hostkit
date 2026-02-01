"""Identity verification endpoints for central OAuth proxy."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from schemas.auth import (
    AuthResponse,
    IdentityVerifyRequest,
    IdentityVerifyResponse,
    TokenResponse,
    UserResponse,
)
from services.identity_service import IdentityError, get_identity_service

router = APIRouter(prefix="/auth/identity", tags=["identity"])


@router.post(
    "/verify",
    response_model=IdentityVerifyResponse,
    summary="Verify identity token from central OAuth proxy",
)
async def verify_identity(
    request: IdentityVerifyRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> IdentityVerifyResponse:
    """Verify an identity token from the central OAuth proxy (auth.hostkit.dev).

    This endpoint is used when the central OAuth proxy redirects back to the
    project after successful OAuth authentication. The token contains verified
    user identity information signed by the proxy.

    - Validates the identity JWT signature
    - Creates or links user account
    - Returns HostKit auth tokens
    """
    settings = get_settings()
    identity_service = get_identity_service()

    # Get client info
    ip_address = None
    forwarded = req.headers.get("X-Forwarded-For")
    if forwarded:
        ip_address = forwarded.split(",")[0].strip()
    elif req.client:
        ip_address = req.client.host

    user_agent = req.headers.get("User-Agent")

    try:
        # Verify the identity token
        payload = await identity_service.verify_identity(request.token)

        # Verify this token is for our project
        if payload.project != settings.project_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Token audience mismatch: expected {settings.project_name}",
            )

        # Create or link user
        result = await identity_service.create_or_link_user(
            db=db,
            payload=payload,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return IdentityVerifyResponse(
            user=UserResponse.model_validate(result.user),
            session=TokenResponse(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                token_type="bearer",
                expires_in=settings.access_token_expire_minutes * 60,
            ),
            is_new_user=result.is_new_user,
        )

    except IdentityError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Identity verification failed: {e.message}",
        )
