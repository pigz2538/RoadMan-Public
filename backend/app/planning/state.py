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
    # Explicitly confirmed map/candidate additions.  Provider discovery is
    # refreshed on every replan, so these user choices must travel with the
    # planning state instead of disappearing when a new search returns.
    confirmed_additions: list[dict[str, Any]]
    # Applied user deletions; these constraints survive provider refreshes and
    # are cleared only by an explicit restore/add action.
    excluded_places: list[dict[str, Any]]
    route_replan_required: bool
    last_applied_patch_id: str | None
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
