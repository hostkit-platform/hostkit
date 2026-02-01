"""OAuth Proxy routers."""

from .google import router as google_router
from .apple import router as apple_router
from .keys import router as keys_router

__all__ = [
    "google_router",
    "apple_router",
    "keys_router",
]
