from fastapi import APIRouter

from ..repositories.skill_calls import SkillCallRepository
from ..services.observability import RequestMetrics

router = APIRouter(prefix="/api/v1/ops", tags=["operations"])
request_metrics: RequestMetrics | None = None


def set_request_metrics(metrics: RequestMetrics) -> None:
    global request_metrics
    request_metrics = metrics


@router.get("/metrics")
async def operational_metrics() -> dict:
    skill_metrics = await SkillCallRepository().summary()
    return {
        "service": (request_metrics.snapshot() if request_metrics else {}),
        "skills": skill_metrics,
    }
