from ..core.config import Settings, get_settings
from ..repositories.skill_calls import record_skill_call
from ..skills.amap import (
    AmapDrivingAdapter,
    AmapGeocodeAdapter,
    AmapReverseGeocodeAdapter,
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
from ..skills.fallbacks import (
    AviationstackFlightAdapter,
    FreeApiOilAdapter,
    FreeApiTrainAdapter,
    Mcp12306TrainAdapter,
    OpenStreetMapGeocodeAdapter,
    SixApiFlightAdapter,
)
from ..skills.opentripmap import OpenTripMapNearbyAdapter
from ..skills.registry import SkillRegistry
from ..skills.weather import (
    MetNoForecastAdapter,
    MultiSourceWeatherAdapter,
    OpenMeteoForecastAdapter,
    SevenTimerForecastAdapter,
    WttrInForecastAdapter,
)


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
    registry.register(AmapReverseGeocodeAdapter(config.amap_webservice_key))
    registry.register(AmapDrivingAdapter(config.amap_webservice_key))
    registry.register(AmapRouteAdapter(config.amap_webservice_key))
    registry.register(AmapPoiAdapter(config.amap_webservice_key))
    registry.register(AmapPoiDetailAdapter(config.amap_webservice_key))
    registry.register(OpenMeteoForecastAdapter())
    registry.register(WttrInForecastAdapter())
    registry.register(MetNoForecastAdapter())
    registry.register(SevenTimerForecastAdapter())
    registry.register(MultiSourceWeatherAdapter())
    registry.register(CarInfoDemoAdapter())
    registry.register(CarInfoCatalogAdapter())
    registry.register(FlyAIHotelAdapter())
    registry.register(FlyAIPoiAdapter())
    registry.register(FlyAITrainAdapter())
    registry.register(FlyAIFlightAdapter())
    registry.register(FlyAIFerryAdapter())
    registry.register(FlyAIKeywordSearchAdapter())
    registry.register(FlyAISemanticSearchAdapter())
    # Primary travel search remains FlyAI.  These adapters are only consulted
    # after a provider outage or empty response and return the same normalized
    # ``data.items`` contract, so route selection does not fabricate tickets.
    registry.register(FreeApiTrainAdapter(config.train_fallback_url))
    registry.register(Mcp12306TrainAdapter(config.mcp_12306_url))
    registry.register(SixApiFlightAdapter(config.flight_fallback_url, config.flight_fallback_api_key))
    registry.register(AviationstackFlightAdapter(config.aviationstack_url, config.aviationstack_api_key))
    registry.register(FreeApiOilAdapter(config.oil_api_url, config.oil_app_id, config.oil_app_secret))
    registry.register(OpenStreetMapGeocodeAdapter())
    registry.register(OpenTripMapNearbyAdapter(config.opentripmap_api_key))
    return registry
