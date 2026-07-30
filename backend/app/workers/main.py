import json
from datetime import datetime, timezone

from arq.connections import RedisSettings

from ..core.config import get_settings
from ..db import JobRow, SessionLocal
from ..domain.models import JobStatus


async def execute_job(_: dict, job_id: str) -> dict:
    async with SessionLocal() as session:
        row = await session.get(JobRow, job_id)
        if not row:
            return {"error": "JOB_NOT_FOUND"}
        if row.cancel_requested:
            row.status = JobStatus.cancelled
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return {"cancelled": True}
        row.status = JobStatus.running
        row.progress = 20
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()

        payload = json.loads(row.payload_json)
        result = {"accepted": True, "kind": row.kind, "payload": payload}
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
