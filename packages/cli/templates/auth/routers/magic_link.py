"""Magic link (passwordless) authentication endpoints."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.user import User
from models.session import Session
from models.magic_link import MagicLink
from schemas.auth import (
    AuthResponse,
    MagicLinkRequest,
    MagicLinkVerifyRequest,
    MessageResponse,
    TokenResponse,
    UserResponse,
)
from services.jwt_service import get_jwt_service
from services.password_service import get_password_service
from services.email_service import get_email_service

router = APIRouter(prefix="/auth/magic-link", tags=["magic-link"])


@router.post(
    "/send",
    response_model=MessageResponse,
    summary="Send magic link email",
)
async def send_magic_link(
    request: MagicLinkRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Send a magic link to the user's email.

    - Generates secure token
    - Stores hashed token in database
    - Sends email with link
    - Returns success message (even if email doesn't exist, for security)
    """
    settings = get_settings()

    if not settings.magic_link_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Magic link authentication is not enabled",
        )

    password_service = get_password_service()

    # Generate secure token
    token = password_service.generate_secure_token(32)
    token_hash = password_service.hash_token(token)

    # Calculate expiry
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.magic_link_expire_minutes
    )

    # Store magic link
    magic_link = MagicLink(
        email=request.email,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(magic_link)
    await db.commit()

    # Send magic link email
    email_service = get_email_service()
    email_service.send_magic_link(
        to_email=request.email,
        token=token,
        redirect_url=request.redirect_url,
    )

    # Always return success to prevent email enumeration
    return MessageResponse(
        message="If an account exists, a magic link has been sent",
        success=True,
    )


@router.post(
    "/verify",
    response_model=AuthResponse,
    summary="Verify magic link and sign in",
)
async def verify_magic_link(
    request: MagicLinkVerifyRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Verify a magic link token and authenticate the user.

    - Validates token hasn't been used
    - Validates token hasn't expired
    - Creates user if doesn't exist
    - Creates session and returns tokens
    """
    settings = get_settings()

    if not settings.magic_link_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Magic link authentication is not enabled",
        )

    password_service = get_password_service()
    jwt_service = get_jwt_service()

    # Hash the provided token
    token_hash = password_service.hash_token(request.token)

    # Find the magic link
    result = await db.execute(
        select(MagicLink).where(MagicLink.token_hash == token_hash)
    )
    magic_link = result.scalar_one_or_none()

    if not magic_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired magic link",
        )

    # Check if already used
    if magic_link.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Magic link has already been used",
        )

    # Check if expired
    if magic_link.is_expired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Magic link has expired",
        )

    # Mark as used
    magic_link.used_at = datetime.now(timezone.utc)

    # Find or create user
    result = await db.execute(select(User).where(User.email == magic_link.email))
    user = result.scalar_one_or_none()

    if not user:
        # Create new user
        user = User(
            email=magic_link.email,
            email_verified=True,  # Magic link verifies email
            is_anonymous=False,
        )
        db.add(user)
        await db.flush()
    else:
        # Mark email as verified
        user.email_verified = True
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


@router.get(
    "/verify",
    response_class=HTMLResponse,
    summary="Verify magic link from email click",
)
async def verify_magic_link_get(
    token: str = Query(..., description="Magic link token from email"),
    redirect: str | None = Query(None, description="URL to redirect after auth"),
    req: Request = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Handle GET request when user clicks magic link in email.

    Browsers make GET requests when clicking links. This endpoint:
    - Validates the magic link token
    - Creates/authenticates the user
    - Returns an HTML page that stores tokens and redirects
    """
    settings = get_settings()

    if not settings.magic_link_enabled:
        return _error_page("Magic link authentication is not enabled")

    password_service = get_password_service()
    jwt_service = get_jwt_service()

    # Hash the provided token
    token_hash = password_service.hash_token(token)

    # Find the magic link
    result = await db.execute(
        select(MagicLink).where(MagicLink.token_hash == token_hash)
    )
    magic_link = result.scalar_one_or_none()

    if not magic_link:
        return _error_page("Invalid or expired magic link")

    if magic_link.is_used:
        return _error_page("This magic link has already been used")

    if magic_link.is_expired:
        return _error_page("This magic link has expired")

    # Mark as used
    magic_link.used_at = datetime.now(timezone.utc)

    # Find or create user
    result = await db.execute(select(User).where(User.email == magic_link.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            email=magic_link.email,
            email_verified=True,
            is_anonymous=False,
        )
        db.add(user)
        await db.flush()
    else:
        user.email_verified = True
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
        ip_address=req.client.host if req and req.client else None,
        user_agent=req.headers.get("user-agent") if req else None,
        expires_at=jwt_service.get_token_expiry("refresh"),
    )
    db.add(session)
    await db.commit()

    # Return HTML that stores tokens and redirects
    return _success_page(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=str(user.id),
        email=user.email,
        redirect_url=redirect,
        project_name=settings.project_name,
    )


def _error_page(message: str) -> HTMLResponse:
    """Return an error page."""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Magic Link Error</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: #f5f5f5;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            max-width: 400px;
        }}
        h1 {{ color: #dc3545; font-size: 24px; }}
        p {{ color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Unable to Sign In</h1>
        <p>{message}</p>
        <p>Please request a new magic link.</p>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=400)


def _success_page(
    access_token: str,
    refresh_token: str,
    user_id: str,
    email: str,
    redirect_url: str | None,
    project_name: str,
) -> HTMLResponse:
    """Return a success page that stores tokens and redirects.

    Uses postMessage for app scheme redirects (mobile apps) and
    localStorage + redirect for web apps.
    """
    # Determine if redirect is an app scheme (e.g., myapp://)
    is_app_scheme = False
    if redirect_url:
        parsed = urlparse(redirect_url)
        is_app_scheme = parsed.scheme not in ("http", "https", "")

    # Build token data as JSON
    token_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": user_id,
        "email": email,
    }

    # Serialize token data as proper JSON for JavaScript
    token_data_json = json.dumps(token_data)
    redirect_url_json = json.dumps(redirect_url) if redirect_url else "null"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Signing in...</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: #f5f5f5;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .spinner {{
            width: 40px;
            height: 40px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #0066cc;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }}
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        h1 {{ color: #333; font-size: 20px; margin: 0; }}
        p {{ color: #666; font-size: 14px; }}
        .success {{ display: none; }}
        .success h1 {{ color: #28a745; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="loading">
            <div class="spinner"></div>
            <h1>Signing you in...</h1>
        </div>
        <div class="success">
            <h1>✓ Signed in successfully</h1>
            <p>You can close this window.</p>
        </div>
    </div>
    <script>
        const tokenData = {token_data_json};
        const redirectUrl = {redirect_url_json};
        const isAppScheme = {str(is_app_scheme).lower()};

        // Store tokens in localStorage for web apps
        try {{
            localStorage.setItem('auth_tokens', JSON.stringify(tokenData));
        }} catch (e) {{
            console.warn('Could not store tokens in localStorage:', e);
        }}

        // Handle redirect
        if (redirectUrl) {{
            if (isAppScheme) {{
                // For app schemes, append tokens to URL
                const separator = redirectUrl.includes('?') ? '&' : '?';
                const params = new URLSearchParams({{
                    access_token: tokenData.access_token,
                    refresh_token: tokenData.refresh_token,
                }});
                window.location.href = redirectUrl + separator + params.toString();
            }} else {{
                // For web URLs, use fragment to avoid server logging
                const fragment = new URLSearchParams({{
                    access_token: tokenData.access_token,
                    refresh_token: tokenData.refresh_token,
                }});
                window.location.href = redirectUrl + '#' + fragment.toString();
            }}
        }} else {{
            // No redirect - show success message
            document.querySelector('.loading').style.display = 'none';
            document.querySelector('.success').style.display = 'block';
        }}
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)
