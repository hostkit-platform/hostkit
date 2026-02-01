"""HostKit Booking Service - FastAPI Application.

Time-first appointment scheduling with provider pooling and room management.
Supports three booking modes: provider-based, resource-based, and class-based.

Version 2.0.0 - Implements all 11 booking enhancement requests.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import (
    health,
    config,
    providers,
    services,
    availability,
    appointments,
    resources,
    classes,
    admin
)

app = FastAPI(
    title="HostKit Booking Service",
    description="Time-first appointment scheduling with provider pooling, resource booking, and class scheduling",
    version="2.0.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure based on deployment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public routes - Core booking
app.include_router(health.router)
app.include_router(config.router, prefix="/api/booking", tags=["config"])
app.include_router(providers.router, prefix="/api/booking", tags=["providers"])
app.include_router(services.router, prefix="/api/booking", tags=["services"])
app.include_router(availability.router, prefix="/api/booking/availability", tags=["availability"])
app.include_router(appointments.router, prefix="/api/booking/appointments", tags=["appointments"])

# Public routes - Resource booking (Phase 3)
app.include_router(resources.router, prefix="/api/booking/resources", tags=["resources"])

# Public routes - Class booking (Phase 3)
app.include_router(classes.router, prefix="/api/booking/classes", tags=["classes"])

# Admin routes
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "HostKit Booking",
        "version": "2.0.0",
        "docs": "/docs",
        "modes": {
            "provider": "Time-first appointment scheduling",
            "resource": "Table/room/bay booking with capacity",
            "class": "Class/event booking with spots"
        }
    }
