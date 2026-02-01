"""Job management service."""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.service import VectorJob, VectorProject
from .job_id import generate_job_id


async def create_job(
    session: AsyncSession,
    project_id: int,
    collection_name: str,
    source_type: str,
    source_identifier: str,
    extra_data: Optional[dict] = None,
) -> VectorJob:
    """Create a new ingestion job."""
    job = VectorJob(
        id=generate_job_id(),
        project_id=project_id,
        collection_name=collection_name,
        source_type=source_type,
        source_identifier=source_identifier,
        status="queued",
        extra_data=extra_data or {},
    )
    session.add(job)
    await session.flush()
    return job


async def get_job_by_id(
    session: AsyncSession,
    job_id: str,
    project_id: Optional[int] = None,
) -> Optional[VectorJob]:
    """Get job by ID, optionally filtered by project."""
    query = select(VectorJob).where(VectorJob.id == job_id)
    if project_id:
        query = query.where(VectorJob.project_id == project_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def list_jobs(
    session: AsyncSession,
    project_id: int,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[VectorJob], int]:
    """List jobs for a project."""
    query = select(VectorJob).where(VectorJob.project_id == project_id)

    if status:
        query = query.where(VectorJob.status == status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await session.execute(count_query)
    total = count_result.scalar()

    # Get paginated results
    query = query.order_by(VectorJob.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    jobs = result.scalars().all()

    return list(jobs), total


async def update_job_status(
    session: AsyncSession,
    job_id: str,
    status: str,
    progress: Optional[int] = None,
    error_message: Optional[str] = None,
    error_details: Optional[dict] = None,
    chunks_created: Optional[int] = None,
    tokens_used: Optional[int] = None,
    document_id: Optional[int] = None,
):
    """Update job status and related fields."""
    updates = {"status": status}

    if progress is not None:
        updates["progress"] = progress

    if status == "processing":
        updates["started_at"] = datetime.utcnow()

    if status in ("completed", "failed", "cancelled"):
        updates["completed_at"] = datetime.utcnow()

    if error_message:
        updates["error_message"] = error_message
    if error_details:
        updates["error_details"] = error_details
    if chunks_created is not None:
        updates["chunks_created"] = chunks_created
    if tokens_used is not None:
        updates["tokens_used"] = tokens_used
    if document_id is not None:
        updates["document_id"] = document_id

    await session.execute(
        update(VectorJob).where(VectorJob.id == job_id).values(**updates)
    )
    await session.commit()


async def increment_retry_count(session: AsyncSession, job_id: str) -> int:
    """Increment retry count and return new value."""
    job = await get_job_by_id(session, job_id)
    if job:
        job.retry_count += 1
        await session.commit()
        return job.retry_count
    return 0


async def cancel_job(session: AsyncSession, job_id: str) -> bool:
    """Cancel a job if it's still queued or processing."""
    job = await get_job_by_id(session, job_id)
    if job and job.status in ("queued", "processing"):
        job.status = "cancelled"
        job.completed_at = datetime.utcnow()
        await session.commit()
        return True
    return False
