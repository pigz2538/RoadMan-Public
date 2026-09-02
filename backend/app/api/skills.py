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


@router.get("/metrics")
async def skill_metrics():
    return await SkillCallRepository().summary()


@router.post("/amap/geocode")
async def geocode(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("amap.geocode", payload, context)


@router.post("/amap/regeocode")
async def reverse_geocode(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("amap.reverse_geocode", payload, context)


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


@router.post("/amap/poi-detail")
async def poi_detail(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("amap.poi_detail", payload, context)


@router.post("/weather/forecast")
async def weather_forecast(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    # The home card fans out to several independent public sources.  Keep the
    # Open-Meteo name as a compatibility fallback for small test registries
    # and older integrations that do not register the composite adapter.
    adapter_name = (
        "weather.multi_source"
        if "weather.multi_source" in registry.names()
        else "open_meteo.forecast"
    )
    return await registry.execute(adapter_name, payload, context)


@router.post("/carinfo/search")
async def carinfo_search(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    # Keep the deterministic demo response for legacy planning calls that
    # filter only by power type. A user-entered query opts into the real
    # carinfo Skill catalog, which returns concrete brand/series/model records.
    adapter = "carinfo.catalog" if str(payload.get("query") or "").strip() else "carinfo.demo"
    return await registry.execute(adapter, payload, context)


@router.post("/flyai/poi")
async def flyai_poi(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.poi", payload, context)


@router.post("/flyai/hotel")
async def flyai_hotel(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.hotel", payload, context)


@router.post("/flyai/train")
async def flyai_train(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.train", payload, context)


@router.post("/flyai/flight")
async def flyai_flight(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.flight", payload, context)


@router.post("/transport/train-fallback")
async def train_fallback(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    """Query the public train fallback without exposing provider internals in the UI."""
    return await registry.execute("freeapi.train", payload, context)


@router.post("/transport/flight-fallback")
async def flight_fallback(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("sixapi.flight", payload, context)


@router.post("/travel/oil-price")
async def oil_price(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("freeapi.oil", payload, context)


@router.post("/flyai/ferry")
async def flyai_ferry(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.ferry", payload, context)


@router.post("/flyai/keyword-search")
async def flyai_keyword_search(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.keyword_search", payload, context)


@router.post("/flyai/ai-search")
async def flyai_ai_search(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("flyai.ai_search", payload, context)


@router.post("/opentripmap/nearby")
async def opentripmap_nearby(
    payload: dict,
    context: SkillContext = Depends(context_from_request),
    registry: SkillRegistry = Depends(get_registry),
):
    return await registry.execute("opentripmap.nearby", payload, context)
