"""Public key endpoint for identity payload verification.

Projects fetch this key to verify identity payloads from the OAuth proxy.
"""

import logging

from fastapi import APIRouter, Response

from services.signing import get_signing_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["keys"])


@router.get(
    "/.well-known/hostkit-oauth-public-key",
    summary="Get OAuth proxy public key",
    response_class=Response,
)
async def get_public_key() -> Response:
    """Get the RSA public key for verifying identity payloads.

    Projects should fetch this key and use it to verify the signature
    on identity payloads received from the OAuth proxy.

    The key is returned in PEM format.

    Returns:
        PEM-encoded RSA public key with cache headers

    Example response:
        -----BEGIN PUBLIC KEY-----
        MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...
        -----END PUBLIC KEY-----

    Usage in project (Python):
        import httpx
        from jose import jwt

        # Fetch and cache public key
        response = httpx.get("https://auth.hostkit.dev/.well-known/hostkit-oauth-public-key")
        public_key = response.text

        # Verify identity payload
        identity = request.args.get("identity")
        claims = jwt.decode(
            identity,
            public_key,
            algorithms=["RS256"],
            audience="myproject",  # Your project name
            issuer="https://auth.hostkit.dev",
        )

        # claims contains:
        # - provider: "google" or "apple"
        # - provider_user_id: Provider's user ID
        # - email: User's email (may be None)
        # - email_verified: Whether email is verified
        # - name: User's display name (may be None)
        # - picture: Profile picture URL (may be None)
    """
    signing_service = get_signing_service()
    public_key = signing_service.public_key

    logger.debug("Public key requested")

    return Response(
        content=public_key,
        media_type="application/x-pem-file",
        headers={
            # Cache for 1 hour - key rotations should be rare
            "Cache-Control": "public, max-age=3600",
            # Allow CORS for this endpoint
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get(
    "/.well-known/hostkit-oauth-public-key.json",
    summary="Get OAuth proxy public key (JSON)",
)
async def get_public_key_json() -> dict:
    """Get the RSA public key in JSON format.

    Alternative to the PEM endpoint for clients that prefer JSON.

    Returns:
        JSON object with public key

    Example response:
        {
            "public_key": "-----BEGIN PUBLIC KEY-----\\n...",
            "algorithm": "RS256",
            "issuer": "https://auth.hostkit.dev"
        }
    """
    from config import get_settings

    signing_service = get_signing_service()
    settings = get_settings()

    return {
        "public_key": signing_service.public_key,
        "algorithm": "RS256",
        "issuer": settings.base_url,
    }
