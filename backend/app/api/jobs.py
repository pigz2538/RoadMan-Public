from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db import get_session
from ..domain.models import JobCreate, JobRecord
from ..repositories import JobRepository
from ..services.job_queue import enqueue_job

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def get_repo(session: AsyncSession = Depends(get_session)) -> JobRepository:
    return JobRepository(session)


@router.post("", response_model=JobRecord, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: JobCreate,
    repo: JobRepository = Depends(get_repo),
) -> JobRecord:
    record = await repo.create(payload)
    await enqueue_job(record)
    return record


@router.get("/{job_id}", response_model=JobRecord)
async def get_job(
    job_id: str,
    repo: JobRepository = Depends(get_repo),
) -> JobRecord:
    record = await repo.get(job_id)
    if not record:
        raise AppError("JOB_NOT_FOUND", "任务不存在", 404, {"job_id": job_id})
    return record


@router.post("/{job_id}/cancel", response_model=JobRecord)
async def cancel_job(
    job_id: str,
    repo: JobRepository = Depends(get_repo),
) -> JobRecord:
    record = await repo.cancel(job_id)
    if not record:
        raise AppError("JOB_NOT_FOUND", "任务不存在", 404, {"job_id": job_id})
    return record
