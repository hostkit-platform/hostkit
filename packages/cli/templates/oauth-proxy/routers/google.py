"""Google OAuth endpoints for the central OAuth proxy.

Handles OAuth flow for all HostKit projects using a single callback URL.
"""

import base64
import json
import logging
import secrets
import time
from urllib.parse import urlencode, quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from config import get_settings
from services.oauth import GoogleOAuthService, OAuthError, get_google_service
from services.signing import get_signing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth/google", tags=["google"])


def _encode_state(data: dict) -> str:
    """Encode state data as URL-safe base64.

    Args:
        data: State data to encode

    Returns:
        URL-safe base64 encoded string
    """
    json_bytes = json.dumps(data).encode("utf-8")
    return base64.urlsafe_b64encode(json_bytes).decode("utf-8")


def _decode_state(state: str) -> dict | None:
    """Decode state data from URL-safe base64.

    Args:
        state: URL-safe base64 encoded string

    Returns:
        Decoded state data, or None if invalid
    """
    try:
        json_bytes = base64.urlsafe_b64decode(state.encode("utf-8"))
        return json.loads(json_bytes.decode("utf-8"))
    except Exception as e:
        logger.warning(f"Failed to decode state: {e}")
        return None


def _validate_return_url(return_url: str) -> bool:
    """Validate that return_url is a valid hostkit.dev subdomain.

    Args:
        return_url: The URL to validate

    Returns:
        True if valid, False otherwise
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(return_url)
        # Must be HTTPS
        if parsed.scheme != "https":
            return False
        # Must be a hostkit.dev subdomain
        if not parsed.netloc.endswith(".hostkit.dev"):
            return False
        return True
    except Exception:
        return False


def _build_error_redirect(return_url: str, error: str, description: str, state: str | None = None) -> str:
    """Build error redirect URL with error in query params.

    Args:
        return_url: Base URL to redirect to
        error: Error code
        description: Human-readable error description
        state: Optional client state to pass back

    Returns:
        Full redirect URL with error params
    """
    params = {
        "error": error,
        "error_description": description,
    }
    if state:
        params["state"] = state

    separator = "&" if "?" in return_url else "?"
    return f"{return_url}{separator}{urlencode(params)}"


@router.get(
    "/start",
    summary="Initiate Google OAuth flow",
    response_class=RedirectResponse,
)
async def google_oauth_start(
    project: str = Query(..., description="Target project name"),
    return_url: str = Query(..., description="URL to redirect to after OAuth"),
    state: str | None = Query(None, description="Optional client state to pass through"),
) -> RedirectResponse:
    """Initiate Google OAuth 2.0 authorization flow.

    This endpoint redirects the user to Google for authentication.
    After authentication, Google redirects back to /oauth/google/callback,
    which then redirects to the project's return_url with a signed identity payload.

    Args:
        project: Target project name (e.g., "myapp")
        return_url: URL to redirect to after OAuth (must be https://*.hostkit.dev/...)
        state: Optional client state to pass through the OAuth flow

    Returns:
        Redirect to Google's authorization page
    """
    settings = get_settings()

    if not settings.google_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google OAuth is not configured",
        )

    # Validate return_url
    if not _validate_return_url(return_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="return_url must be an HTTPS URL on a hostkit.dev subdomain",
        )

    # Create state containing project info and return URL
    state_data = {
        "project": project,
        "return_url": return_url,
        "nonce": secrets.token_urlsafe(16),  # Prevent replay attacks
        "ts": int(time.time()),  # Timestamp for expiry check
    }
    if state:
        state_data["client_state"] = state

    encoded_state = _encode_state(state_data)

    # Build authorization URL
    google_service = get_google_service()
    authorization_url = google_service.build_authorization_url(state=encoded_state)

    logger.info(f"Initiating Google OAuth for project={project}")

    return RedirectResponse(url=authorization_url, status_code=302)


@router.get(
    "/callback",
    summary="Handle Google OAuth callback",
    response_class=RedirectResponse,
)
async def google_oauth_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    """Handle Google OAuth callback.

    This endpoint is called by Google after user authentication.
    It validates the state, exchanges the code for tokens, validates
    the ID token, creates a signed identity payload, and redirects
    to the project's return_url.

    Args:
        code: Authorization code from Google
        state: Encoded state from the start request
        error: Error code if authentication failed
        error_description: Error description if authentication failed

    Returns:
        Redirect to project's return_url with identity payload or error
    """
    settings = get_settings()

    # Handle OAuth errors from Google
    if error:
        logger.warning(f"Google OAuth error: {error} - {error_description}")
        # Try to decode state to get return_url
        if state:
            state_data = _decode_state(state)
            if state_data and "return_url" in state_data:
                return RedirectResponse(
                    url=_build_error_redirect(
                        state_data["return_url"],
                        error,
                        error_description or "OAuth authentication failed",
                        state_data.get("client_state"),
                    ),
                    status_code=302,
                )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth error: {error} - {error_description}",
        )

    # Validate state
    if not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing state parameter",
        )

    state_data = _decode_state(state)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter",
        )

    # Check state expiry (10 minutes)
    if time.time() - state_data.get("ts", 0) > 600:
        return_url = state_data.get("return_url")
        if return_url:
            return RedirectResponse(
                url=_build_error_redirect(
                    return_url,
                    "state_expired",
                    "OAuth state has expired. Please try again.",
                    state_data.get("client_state"),
                ),
                status_code=302,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state has expired",
        )

    project = state_data.get("project")
    return_url = state_data.get("return_url")
    client_state = state_data.get("client_state")

    if not project or not return_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state: missing project or return_url",
        )

    # Validate code
    if not code:
        return RedirectResponse(
            url=_build_error_redirect(
                return_url,
                "missing_code",
                "Missing authorization code",
                client_state,
            ),
            status_code=302,
        )

    try:
        # Exchange code for tokens
        google_service = get_google_service()
        token_response = await google_service.exchange_code(code=code)

        # Validate ID token
        id_token = token_response.get("id_token")
        if not id_token:
            raise OAuthError("No ID token in Google response", provider="google")

        access_token = token_response.get("access_token")
        user_info = await google_service.validate_id_token(
            id_token=id_token,
            access_token=access_token,
        )

        # Create signed identity payload
        signing_service = get_signing_service()
        identity_payload = signing_service.create_identity_payload(
            provider="google",
            provider_user_id=user_info.provider_user_id,
            project=project,
            email=user_info.email,
            email_verified=user_info.email_verified,
            name=user_info.name,
            picture=user_info.picture,
        )

        identity_token = signing_service.sign_identity(identity_payload)

        logger.info(
            f"Google OAuth successful for project={project}, "
            f"user={user_info.provider_user_id}"
        )

        # Build redirect URL with identity payload
        params = {"identity": identity_token}
        if client_state:
            params["state"] = client_state

        separator = "&" if "?" in return_url else "?"
        redirect_url = f"{return_url}{separator}{urlencode(params)}"

        return RedirectResponse(url=redirect_url, status_code=302)

    except OAuthError as e:
        logger.error(f"Google OAuth failed for project={project}: {e}")
        return RedirectResponse(
            url=_build_error_redirect(
                return_url,
                "oauth_error",
                str(e),
                client_state,
            ),
            status_code=302,
        )
    except Exception as e:
        logger.exception(f"Unexpected error in Google OAuth callback: {e}")
        return RedirectResponse(
            url=_build_error_redirect(
                return_url,
                "internal_error",
                "An unexpected error occurred",
                client_state,
            ),
            status_code=302,
        )
