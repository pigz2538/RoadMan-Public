from arq import create_pool
from arq.connections import RedisSettings

from ..core.config import get_settings
from ..domain.models import JobRecord


async def enqueue_job(record: JobRecord) -> bool:
    settings = get_settings()
    if not settings.enable_job_queue:
        return False
    try:
        pool = await create_pool(
            RedisSettings.from_dsn(settings.redis_url),
            default_queue_name="roadman",
        )
        await pool.enqueue_job("execute_job", record.id, _job_id=record.id)
        await pool.aclose()
        return True
    except Exception:
        return False
