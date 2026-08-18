from ..core.config import Settings, get_settings
from ..repositories.skill_calls import record_skill_call
from ..skills.amap import (
    AmapDrivingAdapter,
    AmapGeocodeAdapter,
    AmapPoiAdapter,
    AmapPoiDetailAdapter,
    AmapRouteAdapter,
)
from ..skills.cache import RedisFallbackSkillCache
from ..skills.carinfo import CarInfoCatalogAdapter, CarInfoDemoAdapter
from ..skills.flyai import (
    FlyAIHotelAdapter,
    FlyAIPoiAdapter,
    FlyAIFerryAdapter,
    FlyAIFlightAdapter,
    FlyAIKeywordSearchAdapter,
    FlyAISemanticSearchAdapter,
    FlyAITrainAdapter,
)
from ..skills.opentripmap import OpenTripMapNearbyAdapter
from ..skills.registry import SkillRegistry
from ..skills.weather import OpenMeteoForecastAdapter


def build_skill_registry(settings: Settings | None = None) -> SkillRegistry:
    config = settings or get_settings()
    registry = SkillRegistry(
        cache=RedisFallbackSkillCache(
            config.redis_url,
            config.skill_cache_prefix,
            config.redis_connect_timeout_seconds,
        ),
        audit_sink=record_skill_call,
    )
    registry.register(AmapGeocodeAdapter(config.amap_webservice_key))
    registry.register(AmapDrivingAdapter(config.amap_webservice_key))
    registry.register(AmapRouteAdapter(config.amap_webservice_key))
    registry.register(AmapPoiAdapter(config.amap_webservice_key))
    registry.register(AmapPoiDetailAdapter(config.amap_webservice_key))
    registry.register(OpenMeteoForecastAdapter())
    registry.register(CarInfoDemoAdapter())
    registry.register(CarInfoCatalogAdapter())
    registry.register(FlyAIHotelAdapter())
    registry.register(FlyAIPoiAdapter())
    registry.register(FlyAITrainAdapter())
    registry.register(FlyAIFlightAdapter())
    registry.register(FlyAIFerryAdapter())
    registry.register(FlyAIKeywordSearchAdapter())
    registry.register(FlyAISemanticSearchAdapter())
    registry.register(OpenTripMapNearbyAdapter(config.opentripmap_api_key))
    return registry
