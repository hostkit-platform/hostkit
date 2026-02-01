"""Service database models (hostkit_vector)."""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from .base import ServiceBase


class VectorProject(ServiceBase):
    """Registered project with API key."""

    __tablename__ = "vector_projects"

    id = Column(Integer, primary_key=True)
    project_name = Column(String(255), unique=True, nullable=False, index=True)

    # API key (hashed)
    api_key_hash = Column(String(64), nullable=False, index=True)
    api_key_prefix = Column(String(32), nullable=False)

    # Settings
    settings = Column(JSONB, default=dict)

    # Usage tracking
    total_chunks = Column(Integer, default=0)
    total_tokens_used = Column(Integer, default=0)

    # Status
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_activity_at = Column(DateTime(timezone=True))

    # Relationships
    jobs = relationship("VectorJob", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_vector_projects_active", "is_active", postgresql_where=(is_active == True)),
    )


class VectorJob(ServiceBase):
    """Async ingestion job tracking."""

    __tablename__ = "vector_jobs"

    id = Column(String(32), primary_key=True)  # ULID format
    project_id = Column(
        Integer,
        ForeignKey("vector_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Job details
    collection_name = Column(String(255), nullable=False)
    source_type = Column(String(20), nullable=False)  # url, file, text
    source_identifier = Column(Text, nullable=False)

    # Status
    status = Column(String(20), default="queued")  # queued, processing, completed, failed, cancelled
    progress = Column(Integer, default=0)

    # Results
    chunks_created = Column(Integer)
    tokens_used = Column(Integer)
    document_id = Column(Integer)  # Reference to document in project DB

    # Error handling
    error_message = Column(Text)
    error_details = Column(JSONB)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

    # Extra data
    extra_data = Column(JSONB, default=dict)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    # Relationships
    project = relationship("VectorProject", back_populates="jobs")

    __table_args__ = (
        Index("idx_vector_jobs_project_status", "project_id", "status"),
        Index("idx_vector_jobs_status_pending", "status",
              postgresql_where=(status.in_(["queued", "processing"]))),
    )


class VectorAuditLog(ServiceBase):
    """Audit trail for security operations."""

    __tablename__ = "vector_audit_log"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(50), nullable=False, index=True)
    project_id = Column(
        Integer,
        ForeignKey("vector_projects.id", ondelete="SET NULL"),
        nullable=True
    )
    project_name = Column(String(255))  # Preserved even if project deleted
    actor = Column(String(255))
    details = Column(JSONB, default=dict)
    ip_address = Column(INET)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
