from __future__ import annotations

from datetime import date, datetime, time, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TripStatus(StrEnum):
    collecting = "collecting"
    clarification_required = "clarification_required"
    ready_to_plan = "ready_to_plan"
    planning = "planning"
    paused = "paused"
    completed = "completed"
    failed = "failed"


class Coordinates(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class PlaceRef(BaseModel):
    id: str | None = None
    name: str
    address: str | None = None
    city: str | None = None
    coordinates: Coordinates | None = None
    source_id: str | None = None


class MoneyRange(BaseModel):
    currency: str = "CNY"
    minimum: float = Field(ge=0)
    maximum: float = Field(ge=0)
    estimated: bool = True
    source_count: int = Field(default=0, ge=0)
    as_of: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "MoneyRange":
        if self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        return self


class SourceRecord(BaseModel):
    provider: str
    title: str
    url: str | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    license: str | None = None
    source_type: Literal[
        "official", "map", "ticketing", "travel_platform", "open_data",
        "web_search", "encyclopedia", "agent", "unknown"
    ] = "unknown"
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    facts: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def infer_source_type(self) -> "SourceRecord":
        """Keep legacy provider records useful without rewriting old snapshots."""
        if self.source_type != "unknown":
            return self
        provider = self.provider.casefold()
        title = self.title.casefold()
        if "高德" in provider or "amap" in provider or "地图" in title:
            self.source_type = "map"
        elif "飞猪" in provider or "flyai" in provider or "航班" in title or "车次" in title:
            self.source_type = "travel_platform"
        elif "opentripmap" in provider or "openstreetmap" in provider or "开放地图" in title:
            self.source_type = "open_data"
        elif "百科" in provider or "encyclopedia" in provider:
            self.source_type = "encyclopedia"
        elif "天气" in title or "meteo" in provider:
            self.source_type = "open_data"
        return self


class PlanWarning(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    estimated: bool = False


class VehicleSpecification(BaseModel):
    """One verified specification label/value returned by the vehicle Skill."""

    name: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=300)


class VehicleProfile(BaseModel):
    id: str = Field(default_factory=lambda: f"vehicle_{uuid4().hex[:10]}")
    brand: str
    series: str
    model: str
    year: int | None = Field(default=None, ge=1980, le=2100)
    power_type: Literal["electric", "hybrid", "fuel"]
    rated_range_km: float | None = Field(default=None, gt=0)
    current_energy_percent: float | None = Field(default=None, ge=0, le=100)
    battery_kwh: float | None = Field(default=None, gt=0)
    consumption_per_100km: float | None = Field(default=None, gt=0)
    max_charge_kw: float | None = Field(default=None, gt=0)
    # Public catalogues often publish a 30–80% DC charging duration while
    # omitting the peak charger power. Keep that measured duration separately
    # instead of deriving or inventing a kW value.
    dc_charge_time_hours: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    seats: int = Field(default=5, ge=1, le=20)
    plate_region: str | None = None
    has_etc: bool = False
    mountain_ready: bool = True
    unpaved_ready: bool = False
    safe_energy_reserve_percent: float = Field(default=15, ge=5, le=40)
    # Keep the provider identity and detailed evidence with the saved vehicle
    # so a later plan can show exactly which trim was selected. These fields
    # are stored inside the existing JSON document; no SQL migration is needed.
    source_id: str | None = None
    source_url: str | None = None
    detail_source_url: str | None = None
    price_min_cny: float | None = Field(default=None, ge=0)
    price_max_cny: float | None = Field(default=None, ge=0)
    specifications: list[VehicleSpecification] = Field(default_factory=list, max_length=160)


class VehicleUpdate(BaseModel):
    brand: str | None = None
    series: str | None = None
    model: str | None = None
    year: int | None = Field(default=None, ge=1980, le=2100)
    power_type: Literal["electric", "hybrid", "fuel"] | None = None
    rated_range_km: float | None = Field(default=None, gt=0)
    current_energy_percent: float | None = Field(default=None, ge=0, le=100)
    battery_kwh: float | None = Field(default=None, gt=0)
    consumption_per_100km: float | None = Field(default=None, gt=0)
    max_charge_kw: float | None = Field(default=None, gt=0)
    dc_charge_time_hours: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    seats: int | None = Field(default=None, ge=1, le=20)
    plate_region: str | None = None
    has_etc: bool | None = None
    mountain_ready: bool | None = None
    unpaved_ready: bool | None = None
    safe_energy_reserve_percent: float | None = Field(default=None, ge=5, le=40)
    source_id: str | None = None
    source_url: str | None = None
    detail_source_url: str | None = None
    price_min_cny: float | None = Field(default=None, ge=0)
    price_max_cny: float | None = Field(default=None, ge=0)
    specifications: list[VehicleSpecification] | None = Field(default=None, max_length=160)


class FileStatus(StrEnum):
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    rejected = "rejected"
    deleted = "deleted"


class FileRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"file_{uuid4().hex[:12]}")
    trip_id: str | None = None
    original_name: str
    stored_name: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    status: FileStatus = FileStatus.uploaded
    created_at: datetime = Field(default_factory=utc_now)


class AttachmentExtraction(BaseModel):
    file_id: str
    status: Literal["preview", "confirmed"] = "preview"
    places: list[str] = Field(default_factory=list)
    hotels: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    order_numbers: list[str] = Field(default_factory=list)
    text_preview: str = ""
    warnings: list[str] = Field(default_factory=list)


class AttachmentConfirmation(BaseModel):
    accepted_places: list[str] = Field(default_factory=list, max_length=50)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobCreate(BaseModel):
    kind: Literal["planning", "file_processing", "skill_probe"] = "skill_probe"
    trip_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"job_{uuid4().hex[:12]}")
    kind: str
    trip_id: str | None = None
    status: JobStatus = JobStatus.queued
    progress: int = Field(default=0, ge=0, le=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TripRequest(BaseModel):
    raw_text: str
    origin: PlaceRef | None = None
    destination: PlaceRef | None = None
    # The Requirement Agent keeps the complete destination scope separate from
    # the single route anchor used by legacy route adapters.  This prevents a
    # province/city list such as ["西藏", "新疆"] from being stringified and
    # geocoded as if it were one POI.
    destination_names: list[str] = Field(default_factory=list)
    destination_scope: Literal[
        "poi", "city", "province", "region", "multi_destination", "unknown"
    ] = "unknown"
    travel_intents: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    departure_time: time | None = None
    return_time: time | None = None
    departure_period: Literal["morning", "afternoon", "evening"] | None = None
    return_before: datetime | None = None
    travelers: int | None = Field(default=None, ge=1)
    preferences: list[str] = Field(default_factory=list)
    # Semantic transport preferences returned by the Requirement Agent.  The
    # list is intentionally open-ended at the schema boundary so the Agent
    # can preserve future modes; route execution understands the intercity
    # schedule modes (train/flight/ferry) as well as AMap local modes.
    transport_modes: list[str] = Field(default_factory=list)
    special_events: list[str] = Field(default_factory=list)
    max_days: int | None = Field(default=None, ge=1, le=30)
    must_visit: list[PlaceRef] = Field(default_factory=list)
    # Keep a semantic edit such as “三天只在九宫山” from reusing stale
    # candidates from another city or nearby region.
    stay_only_at_destination: bool = False
    budget: MoneyRange | None = None
    max_continuous_drive_minutes: int = Field(default=120, ge=30)
    # Daily budget for long-distance self-drive legs.  This is separate from
    # the shorter continuous-drive limit because a Wuhan→Xinjiang leg must
    # span calendar days and reserve overnight accommodation.
    max_daily_drive_minutes: int = Field(default=540, ge=180, le=900)
    defaults_applied: list[str] = Field(default_factory=list)


class PreflightRequest(BaseModel):
    raw_text: str = Field(min_length=2, max_length=4000)
    answers: dict[str, str] = Field(default_factory=dict)
    previous_extracted: dict[str, Any] = Field(default_factory=dict)
    semantic_checked: bool = False
    confirmed: bool = False


class PreflightIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    severity: Literal["question", "error"] = "question"
    answer_type: Literal["text", "date", "choice", "time"] = "text"
    options: list[str] = Field(default_factory=list)


class PreflightResponse(BaseModel):
    ready: bool
    confirmation_required: bool = False
    semantic_checked: bool = False
    issues: list[PreflightIssue] = Field(default_factory=list)
    extracted: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    special_event_research: list[dict[str, Any]] = Field(default_factory=list)


class RouteSegment(BaseModel):
    id: str = Field(default_factory=lambda: f"segment_{uuid4().hex[:10]}")
    coordinates: list[Coordinates] = Field(min_length=2)
    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(ge=0)
    road_name: str | None = None
    toll: bool = False
    estimated: bool = False
    elevation_gain_m: float | None = Field(default=None, ge=0)


class EnergyEstimate(BaseModel):
    amount: float = Field(ge=0)
    unit: Literal["kWh", "L"]
    starting_percent: float | None = Field(default=None, ge=0, le=100)
    # Battery-equivalent energy consumed across a movement stage may exceed
    # 100% when the stage contains one or more replenishment stops. Actual SOC
    # fields remain bounded to 0–100; this value is an energy quantity, not a
    # gauge reading.
    consumed_percent: float | None = Field(default=None, ge=0)
    before_replenishment_percent: float | None = Field(default=None, ge=0, le=100)
    replenished_amount: float | None = Field(default=None, ge=0)
    replenished_unit: Literal["kWh", "L"] | None = None
    replenishment_minutes: int | None = Field(default=None, ge=0)
    charging_power_kw: float | None = Field(default=None, gt=0)
    after_replenishment_percent: float | None = Field(default=None, ge=0, le=100)
    remaining_percent: float | None = Field(default=None, ge=0, le=100)
    calculation_basis: Literal[
        "consumption_model",
        "measured_energy",
        "charger_power",
        "conservative_fallback",
        "fuel_service_estimate",
    ] = "consumption_model"
    estimated: bool = True


class WeatherSample(BaseModel):
    place: PlaceRef
    sampled_at: datetime
    temperature_c: float | None = None
    condition: str | None = None
    precipitation_probability: float | None = Field(default=None, ge=0, le=100)
    weather_code: int | None = None
    visibility_m: float | None = Field(default=None, ge=0)
    wind_speed_kmh: float | None = Field(default=None, ge=0)
    estimated: bool = False


class MovementStage(BaseModel):
    id: str = Field(default_factory=lambda: f"stage_{uuid4().hex[:10]}")
    day_id: str
    sequence: int = Field(ge=0)
    title: str
    mode: Literal["driving", "transit", "walking", "riding", "taxi", "flight", "train", "ferry"]
    transit_type: Literal["bus", "subway", "shuttle", "ferry"] | None = None
    transit_legs: list["TransitLeg"] = Field(default_factory=list)
    origin: PlaceRef
    destination: PlaceRef
    waypoints: list[PlaceRef] = Field(default_factory=list)
    route_segments: list[RouteSegment] = Field(default_factory=list)
    planned_start: datetime
    planned_end: datetime
    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(ge=0)
    elevation_gain_m: float | None = Field(default=None, ge=0)
    traffic_summary: str | None = None
    service_number: str | None = None
    service_operator: str | None = None
    departure_terminal: str | None = None
    arrival_terminal: str | None = None
    service_detail_url: str | None = None
    service_departure_at: datetime | None = None
    service_arrival_at: datetime | None = None
    service_seat_class: str | None = None
    service_price: MoneyRange | None = None
    service_status: Literal["confirmed", "estimated", "unavailable"] = "unavailable"
    transit_fare_cny: float | None = Field(default=None, ge=0)
    weather_summary: str | None = None
    toll_fee: MoneyRange | None = None
    energy_estimate: EnergyEstimate | None = None
    weather_samples: list[WeatherSample] = Field(default_factory=list)
    risk_level: Literal["low", "moderate", "high"] = "low"
    risk_tags: list[str] = Field(default_factory=list)
    status: Literal["pending", "active", "completed", "skipped"] = "pending"
    warnings: list[PlanWarning] = Field(default_factory=list)
    source_records: list[SourceRecord] = Field(default_factory=list)


class OpeningHours(BaseModel):
    text: str
    confirmed: bool = False
    source_count: int = Field(default=0, ge=0)
    as_of: date | None = None
    note: str | None = None
    source_urls: list[str] = Field(default_factory=list)


class TransitLeg(BaseModel):
    mode: Literal["bus", "subway", "rail", "walk", "shuttle", "ferry", "other"] = "other"
    line_name: str | None = None
    line_id: str | None = None
    line_type: str | None = None
    departure_stop: str | None = None
    arrival_stop: str | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    stop_count: int | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=0)
    distance_km: float | None = Field(default=None, ge=0)
    fare_cny: float | None = Field(default=None, ge=0)


class Activity(BaseModel):
    id: str = Field(default_factory=lambda: f"activity_{uuid4().hex[:10]}")
    day_id: str
    sequence: int = Field(ge=0)
    type: Literal[
        "attraction", "meal", "hotel", "rest", "charging", "fueling",
        "parking", "service"
    ]
    place: PlaceRef
    planned_start: datetime
    planned_end: datetime
    duration_minutes: int = Field(ge=0)
    locked: bool = False
    required: bool = False
    backup: bool = False
    user_note: str | None = None
    description: str | None = None
    image_url: str | None = None
    detail_url: str | None = None
    # Meal blocks may intentionally sit inside a train/flight/ferry or a
    # service-stop movement stage. They are not a second conflicting route;
    # they represent onboard/waypoint dining during that movement.
    in_transit: bool = False
    ticket_or_price: MoneyRange | None = None
    ticket_name: str | None = None
    ticket_status: Literal["known", "free", "unknown"] = "unknown"
    ticket_note: str | None = None
    parking_or_price: MoneyRange | None = None
    parking_note: str | None = None
    information_summary: str | None = None
    opening_hours: OpeningHours | None = None
    official_url: str | None = None
    booking_url: str | None = None
    information_status: Literal["complete", "partial", "unavailable"] = "unavailable"
    information_checked_at: datetime | None = None
    information_sources_count: int = Field(default=0, ge=0)
    reservation_status: Literal["required", "recommended", "not_required", "unknown"] = "unknown"
    reservation_note: str | None = None
    risk_level: Literal["low", "moderate", "high"] = "low"
    risk_tags: list[str] = Field(default_factory=list)
    risk_note: str | None = None
    source_records: list[SourceRecord] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)


class DayItemRef(BaseModel):
    type: Literal["stage", "activity"]
    id: str


class DayPlan(BaseModel):
    id: str = Field(default_factory=lambda: f"day_{uuid4().hex[:10]}")
    day_index: int = Field(ge=1)
    date: date
    title: str
    items: list[DayItemRef] = Field(default_factory=list)
    stages: list[MovementStage] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    total_distance_km: float = Field(default=0, ge=0)
    total_drive_minutes: int = Field(default=0, ge=0)
    total_walk_minutes: int = Field(default=0, ge=0)
    total_walk_distance_km: float = Field(default=0, ge=0)
    estimated_cost: MoneyRange | None = None
    weather_summary: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Trip(BaseModel):
    id: str = Field(default_factory=lambda: f"trip_{uuid4().hex[:12]}")
    user_id: str | None = None
    title: str
    status: TripStatus = TripStatus.collecting
    start_date: date | None = None
    end_date: date | None = None
    origin: PlaceRef | None = None
    destination: PlaceRef | None = None
    selected_vehicle_id: str | None = None
    request: TripRequest
    days: list[DayPlan] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TripCreate(BaseModel):
    title: str
    request: TripRequest
    selected_vehicle_id: str | None = None


class TripUpdate(BaseModel):
    title: str | None = None
    status: TripStatus | None = None
    selected_vehicle_id: str | None = None


class ClarificationAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class PlanningSnapshot(BaseModel):
    trip_id: str
    status: TripStatus
    missing_fields: list[str] = Field(default_factory=list)
    clarification_round: int = 0
    clarification_question: str | None = None
    defaults_applied: list[str] = Field(default_factory=list)
    progress: dict[str, Any] = Field(default_factory=dict)
    verification_result: dict[str, Any] | None = None
    special_event_research: list[dict[str, Any]] = Field(default_factory=list)
    plan_markdown: str | None = None
    job_id: str | None = None
    planning_batch_id: str | None = None
    edit_confirmation_pending: bool = False
    route_replan_required: bool = False
    excluded_places: list[dict[str, Any]] = Field(default_factory=list)


class PlanPatch(BaseModel):
    id: str = Field(default_factory=lambda: f"patch_{uuid4().hex[:12]}")
    trip_id: str
    target_type: str
    target_id: str
    operation: str
    original_value: dict[str, Any]
    proposed_value: dict[str, Any]
    impact_scope: list[str] = Field(default_factory=list)
    time_delta_minutes: int = 0
    cost_delta: MoneyRange | None = None
    risk_delta: str | None = None
    requires_replan: bool = False
    status: Literal["preview", "accepted", "rejected", "applied", "rolled_back"] = "preview"


class TripVersionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class TripVersion(BaseModel):
    id: str = Field(default_factory=lambda: f"version_{uuid4().hex[:12]}")
    trip_id: str
    name: str
    note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class VerificationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "error", "blocker"]
    title: str
    description: str
    affected_ids: list[str] = Field(default_factory=list)
    source: str
    user_confirmation_required: bool = False
    auto_fix_available: bool = False


class SkillResult(BaseModel):
    success: bool
    provider: str
    data: dict[str, Any] | list[Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    estimated: bool = False
    cache_hit: bool = False
    latency_ms: int = Field(default=0, ge=0)
    error_code: str | None = None


class SkillCallRecord(BaseModel):
    id: str
    request_id: str | None = None
    trip_id: str | None = None
    adapter: str
    provider: str
    success: bool
    cache_hit: bool = False
    latency_ms: int = 0
    error_code: str | None = None
    source_summary: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class SSEEvent(BaseModel):
    event: Literal[
        "planning_started", "node_started", "tool_started", "tool_completed",
        "node_completed", "clarification_required", "progress", "warning",
        "plan_updated",
        "patch_preview_ready", "planning_paused", "planning_resumed",
        "planning_completed", "planning_failed"
    ]
    trip_id: str
    node: str | None = None
    tool: str | None = None
    label: str
    progress: int = Field(ge=0, le=100)
    batch_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
