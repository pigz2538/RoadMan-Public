import json
from datetime import datetime, timezone

from arq.connections import RedisSettings

from ..core.config import get_settings
from ..db import JobRow, SessionLocal
from ..domain.models import JobStatus
from ..planning import pause_planning, run_planning


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
        else:
            result = {"accepted": True, "kind": kind, "payload": payload}
    except Exception as exc:
        async with SessionLocal() as session:
            row = await session.get(JobRow, job_id)
            if row:
                row.status = JobStatus.failed
                row.error_json = json.dumps(
                    {"code": "JOB_EXECUTION_FAILED", "message": type(exc).__name__},
                )
                row.updated_at = datetime.now(timezone.utc)
                await session.commit()
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


class WorkerSettings:
    functions = [execute_job]
    queue_name = "roadman"
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
