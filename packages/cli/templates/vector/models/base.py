"""SQLAlchemy base classes for service and project databases."""

from sqlalchemy.orm import DeclarativeBase


class ServiceBase(DeclarativeBase):
    """Base class for service database models (hostkit_vector)."""
    pass


class ProjectBase(DeclarativeBase):
    """Base class for project database models ({project}_vector)."""
    pass
