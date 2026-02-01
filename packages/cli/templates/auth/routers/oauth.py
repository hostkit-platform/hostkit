"""OAuth authentication endpoints for Google and Apple Sign-In."""

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from schemas.auth import (
    AppleCallbackRequest,
    AppleTokenVerifyRequest,
    AuthResponse,
    GoogleTokenVerifyRequest,
    OAuthInitRequest,
    TokenResponse,
    UserResponse,
)
from services.oauth_service import (
    OAuthError,
    OAuthUserInfo,
    get_oauth_service,
)

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])


def _build_redirect_with_tokens(
    base_url: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    token_type: str = "bearer",
    client_state: str | None = None,
) -> str:
    """Build redirect URL with tokens in fragment (implicit flow style).

    Tokens are passed in the URL fragment (#) for security:
    - Fragments are not sent to the server in HTTP requests
    - Only accessible via JavaScript on the client

    Args:
        base_url: The URL to redirect to
        access_token: JWT access token
        refresh_token: JWT refresh token
        expires_in: Token expiry in seconds
        token_type: Token type (default: bearer)
        client_state: Optional state to pass back to client

    Returns:
        Full redirect URL with fragment
    """
    params = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_type,
        "expires_in": str(expires_in),
    }
    if client_state:
        params["state"] = client_state

    fragment = urlencode(params)

    # Parse and reconstruct URL with fragment
    parsed = urlparse(base_url)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        fragment,
    ))


def _build_error_redirect(
    base_url: str,
    error: str,
    error_description: str,
    client_state: str | None = None,
) -> str:
    """Build redirect URL with error in fragment.

    Args:
        base_url: The URL to redirect to
        error: Error code
        error_description: Human-readable error description
        client_state: Optional state to pass back to client

    Returns:
        Full redirect URL with error fragment
    """
    params = {
        "error": error,
        "error_description": error_description,
    }
    if client_state:
        params["state"] = client_state

    fragment = urlencode(params)

    parsed = urlparse(base_url)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        fragment,
    ))


def _get_client_ip(request: Request) -> str | None:
    """Extract client IP from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _build_auth_response(
    user: Any,
    access_token: str,
    refresh_token: str,
    settings: Any,
) -> AuthResponse:
    """Build AuthResponse from OAuth result."""
    return AuthResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            email_verified=user.email_verified,
            is_anonymous=user.is_anonymous,
            created_at=user.created_at,
            last_sign_in_at=user.last_sign_in_at,
            metadata_=user.metadata_,
        ),
        session=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
        ),
    )


# =============================================================================
# Google OAuth
# =============================================================================


@router.post(
    "/google",
    summary="Initiate Google OAuth flow",
    response_model=dict,
)
async def google_oauth_init(
    request: OAuthInitRequest,
) -> dict:
    """Initiate Google OAuth 2.0 authorization flow.

    Returns the authorization URL to redirect the user to.

    The client should redirect the user to the returned URL.
    After authentication, Google will redirect to the callback URL.

    If `final_redirect_uri` is provided, the callback will redirect there
    with tokens in the URL fragment. Otherwise, JSON is returned directly.
    """
    settings = get_settings()

    if not settings.google_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google OAuth is not configured",
        )

    oauth_service = get_oauth_service()

    # Generate state token with optional client data
    state_data = {}
    if request.redirect_uri:
        state_data["redirect_uri"] = request.redirect_uri
    if request.final_redirect_uri:
        state_data["final_redirect_uri"] = request.final_redirect_uri
    if request.state:
        state_data["client_state"] = request.state

    state = oauth_service.state_store.generate_state(state_data)

    # Build authorization URL
    authorization_url = oauth_service.google.build_authorization_url(
        state=state,
        redirect_uri=request.redirect_uri,
    )

    return {
        "authorization_url": authorization_url,
        "state": state,
    }


@router.get(
    "/google/callback",
    summary="Handle Google OAuth callback",
    response_model=None,
)
async def google_oauth_callback(
    code: str,
    state: str | None = None,
    req: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback.

    This endpoint is called by Google after user authentication.

    - Validates state parameter (CSRF protection)
    - Exchanges authorization code for tokens
    - Validates Google ID token
    - Creates or links user account
    - Returns HostKit auth tokens (JSON or redirect based on flow)

    If `final_redirect_uri` was provided during init, redirects there
    with tokens in URL fragment. Otherwise, returns JSON directly.
    """
    settings = get_settings()

    if not settings.google_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google OAuth is not configured",
        )

    oauth_service = get_oauth_service()

    # Validate state (CSRF protection)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing state parameter",
        )

    state_data = oauth_service.state_store.validate_and_consume(state)
    if state_data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    # Extract redirect info from state
    final_redirect_uri = state_data.get("final_redirect_uri")
    client_state = state_data.get("client_state")

    try:
        # Exchange code for tokens
        redirect_uri = state_data.get("redirect_uri")
        token_response = await oauth_service.google.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
        )

        # Get ID token and validate it
        id_token = token_response.get("id_token")
        if not id_token:
            raise OAuthError("No ID token in Google response", provider="google")

        # Get access token for at_hash verification
        access_token = token_response.get("access_token")

        # Validate ID token and extract user info
        # Pass access_token for at_hash claim verification
        user_info = await oauth_service.google.validate_id_token(
            id_token,
            access_token=access_token,
        )

        # Add token info to user_info for storage
        user_info.access_token = access_token
        user_info.refresh_token = token_response.get("refresh_token")

        expires_in = token_response.get("expires_in")
        if expires_in:
            user_info.token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=expires_in
            )

        # Create or link user
        result = await oauth_service.link_or_create_user(
            db=db,
            user_info=user_info,
            ip_address=_get_client_ip(req),
            user_agent=req.headers.get("User-Agent") if req else None,
        )

        # If final_redirect_uri is set, redirect with tokens in fragment
        if final_redirect_uri:
            redirect_url = _build_redirect_with_tokens(
                base_url=final_redirect_uri,
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                expires_in=settings.access_token_expire_minutes * 60,
                client_state=client_state,
            )
            return RedirectResponse(url=redirect_url, status_code=302)

        # Otherwise return JSON response
        return _build_auth_response(
            user=result.user,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            settings=settings,
        )

    except OAuthError as e:
        # If final_redirect_uri is set, redirect with error
        if final_redirect_uri:
            redirect_url = _build_error_redirect(
                base_url=final_redirect_uri,
                error="oauth_error",
                error_description=str(e),
                client_state=client_state,
            )
            return RedirectResponse(url=redirect_url, status_code=302)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/google/verify-token",
    summary="Verify Google ID token from native apps",
    response_model=AuthResponse,
)
async def google_verify_token(
    request: GoogleTokenVerifyRequest,
    req: Request = None,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Verify a Google ID token from native mobile apps.

    This endpoint accepts ID tokens directly from Google Sign-In SDK,
    bypassing the authorization code flow for simpler mobile integration.

    For iOS apps using a different client ID, provide the ios_client_id
    parameter for proper audience validation.

    - Validates Google ID token signature and claims
    - Creates or links user account
    - Returns HostKit auth tokens
    """
    settings = get_settings()

    if not settings.google_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google OAuth is not configured",
        )

    oauth_service = get_oauth_service()

    try:
        # Validate ID token
        # Pass ios_client_id for audience validation if provided
        user_info = await oauth_service.google.validate_id_token(
            id_token=request.id_token,
            audience_override=request.ios_client_id,
        )

        # Create or link user
        result = await oauth_service.link_or_create_user(
            db=db,
            user_info=user_info,
            ip_address=_get_client_ip(req),
            user_agent=req.headers.get("User-Agent") if req else None,
        )

        return _build_auth_response(
            user=result.user,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            settings=settings,
        )

    except OAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# =============================================================================
# Apple Sign-In
# =============================================================================


@router.post(
    "/apple",
    summary="Initiate Apple Sign-In flow",
    response_model=dict,
)
async def apple_oauth_init(
    request: OAuthInitRequest,
) -> dict:
    """Initiate Apple Sign-In authorization flow.

    Returns the authorization URL to redirect the user to.

    Note: Apple uses POST with form_post response mode,
    so the callback will be a POST request.

    If `final_redirect_uri` is provided, the callback will redirect there
    with tokens in the URL fragment. Otherwise, JSON is returned directly.
    """
    settings = get_settings()

    if not settings.apple_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Apple Sign-In is not configured",
        )

    oauth_service = get_oauth_service()

    # Generate state token
    state_data = {}
    if request.redirect_uri:
        state_data["redirect_uri"] = request.redirect_uri
    if request.final_redirect_uri:
        state_data["final_redirect_uri"] = request.final_redirect_uri
    if request.state:
        state_data["client_state"] = request.state

    state = oauth_service.state_store.generate_state(state_data)

    # Build authorization URL
    authorization_url = oauth_service.apple.build_authorization_url(
        state=state,
        redirect_uri=request.redirect_uri,
    )

    return {
        "authorization_url": authorization_url,
        "state": state,
    }


@router.post(
    "/apple/callback",
    summary="Handle Apple Sign-In callback",
    response_model=None,
)
async def apple_oauth_callback(
    req: Request,
    code: str = Form(...),
    state: str = Form(None),
    id_token: str = Form(None),
    user: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle Apple Sign-In callback.

    Note: Apple uses POST with form data for callbacks (response_mode=form_post).

    Important: Apple only sends user info (name, email) on the FIRST sign-in.
    After that, only the ID token is available. Store user info on first sign-in!

    - Validates state parameter (CSRF protection)
    - Validates Apple ID token
    - Creates or links user account
    - Returns HostKit auth tokens (JSON or redirect based on flow)

    If `final_redirect_uri` was provided during init, redirects there
    with tokens in URL fragment. Otherwise, returns JSON directly.
    """
    settings = get_settings()

    if not settings.apple_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Apple Sign-In is not configured",
        )

    oauth_service = get_oauth_service()

    # Validate state (CSRF protection)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing state parameter",
        )

    state_data = oauth_service.state_store.validate_and_consume(state)
    if state_data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    # Extract redirect info from state
    final_redirect_uri = state_data.get("final_redirect_uri")
    client_state = state_data.get("client_state")

    try:
        # Apple can send ID token directly, or we need to exchange code
        apple_id_token = id_token

        if not apple_id_token:
            # Exchange code for tokens
            redirect_uri = state_data.get("redirect_uri")
            token_response = await oauth_service.apple.exchange_code(
                code=code,
                redirect_uri=redirect_uri,
            )
            apple_id_token = token_response.get("id_token")

        if not apple_id_token:
            raise OAuthError("No ID token from Apple", provider="apple")

        # Validate ID token and extract user info
        # Pass user data for first sign-in
        user_info = await oauth_service.apple.validate_id_token(
            id_token=apple_id_token,
            user_data=user,
        )

        # Apple doesn't provide refresh tokens for users (only for server-to-server)
        # Access token is short-lived

        # Create or link user
        result = await oauth_service.link_or_create_user(
            db=db,
            user_info=user_info,
            ip_address=_get_client_ip(req),
            user_agent=req.headers.get("User-Agent") if req else None,
        )

        # If final_redirect_uri is set, redirect with tokens in fragment
        if final_redirect_uri:
            redirect_url = _build_redirect_with_tokens(
                base_url=final_redirect_uri,
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                expires_in=settings.access_token_expire_minutes * 60,
                client_state=client_state,
            )
            return RedirectResponse(url=redirect_url, status_code=302)

        # Otherwise return JSON response
        return _build_auth_response(
            user=result.user,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            settings=settings,
        )

    except OAuthError as e:
        # If final_redirect_uri is set, redirect with error
        if final_redirect_uri:
            redirect_url = _build_error_redirect(
                base_url=final_redirect_uri,
                error="oauth_error",
                error_description=str(e),
                client_state=client_state,
            )
            return RedirectResponse(url=redirect_url, status_code=302)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/apple/verify-token",
    summary="Verify Apple ID token from native apps",
    response_model=AuthResponse,
)
async def apple_verify_token(
    request: AppleTokenVerifyRequest,
    req: Request = None,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Verify an Apple ID token from native mobile apps.

    This endpoint accepts ID tokens directly from Apple Sign-In SDK
    (ASAuthorizationAppleIDCredential), bypassing the web OAuth redirect
    flow for simpler native app integration.

    Unlike the callback endpoint, this does NOT require state parameter
    since native apps don't use redirects.

    Important: Apple only sends user info (name) on FIRST sign-in.
    Pass the user parameter if available from the credential.

    For iOS apps, the token audience is the Bundle ID (not the Services ID).
    Pass bundle_id in the request, or configure APPLE_BUNDLE_ID env var.

    - Validates Apple ID token signature using Apple's public keys
    - Validates claims (issuer, audience, expiry)
    - Creates or links user account
    - Returns HostKit auth tokens
    """
    settings = get_settings()

    if not settings.apple_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Apple Sign-In is not configured",
        )

    oauth_service = get_oauth_service()

    try:
        # Determine audience for validation:
        # 1. Request bundle_id takes precedence (per-request override)
        # 2. APPLE_BUNDLE_ID env var (configured for native apps)
        # 3. Fall back to APPLE_CLIENT_ID (Services ID, for web OAuth)
        audience_override = request.bundle_id or settings.apple_bundle_id

        # Validate ID token and extract user info
        user_info = await oauth_service.apple.validate_id_token(
            id_token=request.id_token,
            user_data=request.user,
            audience_override=audience_override,
        )

        # Create or link user
        result = await oauth_service.link_or_create_user(
            db=db,
            user_info=user_info,
            ip_address=_get_client_ip(req),
            user_agent=req.headers.get("User-Agent") if req else None,
        )

        return _build_auth_response(
            user=result.user,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            settings=settings,
        )

    except OAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
