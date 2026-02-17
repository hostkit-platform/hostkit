"""API routers for auth service endpoints."""

from routers.auth import router as auth_router
from routers.oauth import router as oauth_router
from routers.magic_link import router as magic_link_router
from routers.anonymous import router as anonymous_router
from routers.token import router as token_router
from routers.user import router as user_router
from routers.health import router as health_router
from routers.identity import router as identity_router
from routers.diagnose import router as diagnose_router
from routers.password_reset import router as password_reset_router
from routers.email_verification import router as email_verification_router

__all__ = [
    "auth_router",
    "oauth_router",
    "magic_link_router",
    "anonymous_router",
    "token_router",
    "user_router",
    "health_router",
    "identity_router",
    "diagnose_router",
    "password_reset_router",
    "email_verification_router",
]
