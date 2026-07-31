from fastapi import APIRouter, Depends, Query, Request

from ..domain.models import SkillCallRecord
from ..repositories.skill_calls import SkillCallRepository
from ..skills.base import SkillContext
from ..skills.registry import SkillRegistry

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def get_registry() -> SkillRegistry:
    from ..main import registry

    return registry


def context_from_request(request: Request) -> SkillContext:
    return SkillContext(
        trip_id=request.headers.get("X-Trip-ID"),
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/health")
async def skills_health(registry: SkillRegistry = Depends(get_registry)):
    return await registry.health()


@router.get("/calls", response_model=list[SkillCallRecord])
async def skill_calls(limit: int = Query(default=50, ge=1, le=200)):
    return await SkillCallRepository().list(limit)


@router.post("/amap/geocode")
async def geocode(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("amap.geocode", payload, context)


@router.post("/amap/driving")
async def driving(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("amap.driving", payload, context)


@router.post("/amap/route")
async def route(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("amap.route", payload, context)


@router.post("/amap/poi")
async def poi(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("amap.poi", payload, context)


@router.post("/weather/forecast")
async def weather_forecast(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("open_meteo.forecast", payload, context)


@router.post("/carinfo/search")
async def carinfo_search(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("carinfo.demo", payload, context)


@router.post("/flyai/poi")
async def flyai_poi(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.poi", payload, context)


@router.post("/opentripmap/nearby")
async def opentripmap_nearby(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("opentripmap.nearby", payload, context)
