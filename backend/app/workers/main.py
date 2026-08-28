import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
from arq.connections import RedisSettings
from arq import cron
from sqlalchemy import select

from ..core.config import get_settings
from ..db import FileRow, JobRow, SessionLocal
from ..domain.models import FileStatus, JobStatus
from ..planning import pause_planning, run_planning
from ..repositories import TripRepository

logger = structlog.get_logger()


async def cleanup_expired_files(_: dict) -> dict:
    settings = get_settings()
    upload_root = Path(settings.upload_dir).resolve()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.file_retention_days)
    removed = 0
    async with SessionLocal() as session:
        rows = list((await session.scalars(
            select(FileRow).where(
                FileRow.created_at < cutoff,
                FileRow.status != FileStatus.deleted,
            )
        )).all())
        for row in rows:
            candidate = Path(row.storage_path).resolve()
            if candidate != upload_root and upload_root not in candidate.parents:
                logger.warning("file_cleanup_skipped_outside_upload_root", file_id=row.id)
                continue
            candidate.unlink(missing_ok=True)
            row.status = FileStatus.deleted
            removed += 1
        await session.commit()
    logger.info("file_retention_cleanup_completed", removed=removed)
    return {"removed": removed, "retention_days": settings.file_retention_days}


async def execute_job(_: dict, job_id: str) -> dict:
    async with SessionLocal() as session:
        row = await session.get(JobRow, job_id)
        if not row:
            return {"error": "JOB_NOT_FOUND"}
        payload = json.loads(row.payload_json)
        if row.cancel_requested:
            row.status = JobStatus.cancelled
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            if row.kind == "planning" and payload.get("trip_id"):
                await pause_planning(payload["trip_id"])
            return {"cancelled": True}
        row.status = JobStatus.running
        row.progress = 20
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()

        kind = row.kind

    try:
        if kind == "planning":
            result = await run_planning(
                payload["trip_id"],
                clarification_answer=payload.get("clarification_answer"),
                job_id=job_id,
            )
            # A trip can be deleted while a queued planning job is starting
            # (the UI deliberately allows deleting a history item at any
            # time).  ``run_planning`` reports this as a structured result;
            # do not let the worker mark that job completed with an error
            # payload or replay it as a failed planning run.
            if (
                isinstance(result, dict)
                and isinstance(result.get("error"), dict)
                and result["error"].get("code") == "TRIP_NOT_FOUND"
            ):
                return await _discard_deleted_trip_job(
                    job_id,
                    payload.get("trip_id"),
                )
        else:
            result = {"accepted": True, "kind": kind, "payload": payload}
    except Exception as exc:
        if isinstance(exc, KeyError):
            error_message = "行程数据缺少必要标识，已停止保存并可重新规划。"
        else:
            error_message = str(exc).strip()[:240] or type(exc).__name__
        async with SessionLocal() as session:
            row = await session.get(JobRow, job_id)
            if row:
                row.status = JobStatus.failed
                row.error_json = json.dumps(
                    {"code": "JOB_EXECUTION_FAILED", "message": error_message},
                    ensure_ascii=False,
                )
                row.updated_at = datetime.now(timezone.utc)
                await session.commit()
            if kind == "planning" and payload.get("trip_id"):
                trip = await TripRepository(session).get(payload["trip_id"])
                if not trip:
                    # The only durable record left is the job itself.  A
                    # deleted trip is an expected cancellation, not an
                    # application failure, and should not produce a noisy
                    # traceback or a stale failure event in the UI.
                    if row:
                        row.status = JobStatus.cancelled
                        row.result_json = json.dumps(
                            {
                                "cancelled": True,
                                "reason": "TRIP_DELETED",
                                "trip_id": payload["trip_id"],
                            },
                            ensure_ascii=False,
                        )
                        row.error_json = json.dumps(
                            {
                                "code": "TRIP_DELETED",
                                "message": "行程已删除，未继续执行规划任务",
                            },
                            ensure_ascii=False,
                        )
                        row.updated_at = datetime.now(timezone.utc)
                        await session.commit()
                    logger.info(
                        "planning_job_discarded_trip_deleted",
                        job_id=job_id,
                        trip_id=payload["trip_id"],
                    )
                    return {
                        "cancelled": True,
                        "reason": "TRIP_DELETED",
                        "trip_id": payload["trip_id"],
                    }
                if trip:
                    state, markdown = await TripRepository(session).get_planning_snapshot(trip.id)
                    state = state or {}
                    state["progress"] = {"node": "failed", "value": 100, "label": "规划执行失败"}
                    state["verification_result"] = {
                        "passed": False,
                        "issues": [{
                            "code": "PLANNING_EXECUTION_FAILED",
                            "severity": "blocker",
                            "description": f"规划执行中断：{error_message}",
                        }],
                    }
                    await TripRepository(session).save_planning_result(trip, state, markdown)
        logger.exception(
            "planning_job_failed",
            job_id=job_id,
            trip_id=payload.get("trip_id"),
            error_type=type(exc).__name__,
            error_message=error_message,
        )
        return {"error": "JOB_EXECUTION_FAILED"}

    async with SessionLocal() as session:
        row = await session.get(JobRow, job_id)
        if not row:
            return result
        if row.cancel_requested:
            row.status = JobStatus.cancelled
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return {"cancelled": True}
        row.status = JobStatus.completed
        row.progress = 100
        row.result_json = json.dumps(result, ensure_ascii=False)
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return result


async def _discard_deleted_trip_job(job_id: str, trip_id: str | None) -> dict:
    """Cancel a planning job whose trip was removed before it could run."""
    async with SessionLocal() as session:
        row = await session.get(JobRow, job_id)
        if row:
            row.status = JobStatus.cancelled
            row.progress = 100
            row.result_json = json.dumps(
                {
                    "cancelled": True,
                    "reason": "TRIP_DELETED",
                    "trip_id": trip_id,
                },
                ensure_ascii=False,
            )
            row.error_json = json.dumps(
                {
                    "code": "TRIP_DELETED",
                    "message": "行程已删除，未继续执行规划任务",
                },
                ensure_ascii=False,
            )
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
    logger.info(
        "planning_job_discarded_trip_deleted",
        job_id=job_id,
        trip_id=trip_id,
    )
    return {"cancelled": True, "reason": "TRIP_DELETED", "trip_id": trip_id}


class WorkerSettings:
    functions = [execute_job]
    cron_jobs = [cron(cleanup_expired_files, hour=3, minute=15)]
    queue_name = "roadman"
    # DeepSeek V4 Flash runs with maximum reasoning and a planning graph can
    # legitimately fan out to many map/travel lookups.  The old ARQ default
    # (five minutes) could kill a healthy long-distance plan in the final
    # service-discovery pass.  Keep the finite bound, but leave enough room
    # for the complete plan plus an automatic verification/retry cycle.
    job_timeout = 900
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
