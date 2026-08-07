from typing import Any, TypedDict


class RoadManState(TypedDict, total=False):
    trip_id: str
    raw_input: str
    selected_vehicle_id: str | None
    vehicle_profile: dict[str, Any] | None
    trip_request: dict[str, Any]
    missing_fields: list[str]
    clarification_round: int
    clarification_question: str | None
    clarification_answers: list[dict[str, Any]]
    route_candidates: list[dict[str, Any]]
    local_routes: list[dict[str, Any]]
    tourism_candidates: dict[str, list[dict[str, Any]]]
    selected_route: dict[str, Any] | None
    weather_results: list[dict[str, Any]]
    service_pois: dict[str, dict[str, list[dict[str, Any]]]]
    day_plans: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    verification_result: dict[str, Any] | None
    # Number of automatic review/repair passes already completed. The old
    # boolean is kept for snapshots written by earlier versions.
    repair_attempts: int
    repair_attempted: bool
    progress: dict[str, Any]
    sources: list[dict[str, Any]]
    plan_markdown: str | None
    error: dict[str, Any] | None
    messages: list[dict[str, Any]]
    special_event_research: list[dict[str, Any]]
    seasonal_review: list[dict[str, Any]]
    destination_research: dict[str, Any]
