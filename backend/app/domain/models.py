from __future__ import annotations

from datetime import date, datetime, timezone
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


class PlanWarning(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    estimated: bool = False


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
    height_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    seats: int = Field(default=5, ge=1, le=20)
    plate_region: str | None = None
    has_etc: bool = False
    mountain_ready: bool = True
    unpaved_ready: bool = False


class TripRequest(BaseModel):
    raw_text: str
    origin: PlaceRef | None = None
    destination: PlaceRef | None = None
    start_date: date | None = None
    end_date: date | None = None
    return_before: datetime | None = None
    travelers: int | None = Field(default=None, ge=1)
    preferences: list[str] = Field(default_factory=list)
    must_visit: list[PlaceRef] = Field(default_factory=list)
    budget: MoneyRange | None = None
    max_continuous_drive_minutes: int = Field(default=120, ge=30)
    defaults_applied: list[str] = Field(default_factory=list)


class RouteSegment(BaseModel):
    id: str = Field(default_factory=lambda: f"segment_{uuid4().hex[:10]}")
    coordinates: list[Coordinates] = Field(min_length=2)
    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(ge=0)
    road_name: str | None = None
    toll: bool = False
    estimated: bool = False


class EnergyEstimate(BaseModel):
    amount: float = Field(ge=0)
    unit: Literal["kWh", "L"]
    remaining_percent: float | None = Field(default=None, ge=0, le=100)
    estimated: bool = True


class WeatherSample(BaseModel):
    place: PlaceRef
    sampled_at: datetime
    temperature_c: float | None = None
    condition: str | None = None
    precipitation_probability: float | None = Field(default=None, ge=0, le=100)
    estimated: bool = False


class MovementStage(BaseModel):
    id: str = Field(default_factory=lambda: f"stage_{uuid4().hex[:10]}")
    day_id: str
    sequence: int = Field(ge=0)
    title: str
    mode: Literal["driving", "transit", "walking", "taxi", "flight", "train"]
    origin: PlaceRef
    destination: PlaceRef
    waypoints: list[PlaceRef] = Field(default_factory=list)
    route_segments: list[RouteSegment] = Field(default_factory=list)
    planned_start: datetime
    planned_end: datetime
    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(ge=0)
    toll_fee: MoneyRange | None = None
    energy_estimate: EnergyEstimate | None = None
    weather_samples: list[WeatherSample] = Field(default_factory=list)
    status: Literal["pending", "active", "completed", "skipped"] = "pending"
    warnings: list[PlanWarning] = Field(default_factory=list)
    source_records: list[SourceRecord] = Field(default_factory=list)


class OpeningHours(BaseModel):
    text: str
    confirmed: bool = False


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
    ticket_or_price: MoneyRange | None = None
    opening_hours: OpeningHours | None = None
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
    status: Literal["preview", "accepted", "rejected", "applied"] = "preview"


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


class SSEEvent(BaseModel):
    event: Literal[
        "planning_started", "node_started", "tool_started", "tool_completed",
        "node_completed", "clarification_required", "progress", "warning",
        "patch_preview_ready", "planning_paused", "planning_resumed",
        "planning_completed", "planning_failed"
    ]
    trip_id: str
    node: str | None = None
    tool: str | None = None
    label: str
    progress: int = Field(ge=0, le=100)
    timestamp: datetime = Field(default_factory=utc_now)
