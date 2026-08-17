"""Persistent user exclusions for itinerary replanning.

Deleting an activity is a user constraint, not a transient UI operation.  The
planning graph can discover the same place again from another provider, so the
identity is stored using the candidate id, source ids, normalized name and
coordinates.  This module deliberately does not infer intent from the raw
message; it only records an explicit, applied edit and removes that record
when the user explicitly adds the place back.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any


def normalize_exclusion_name(value: Any) -> str:
    return "".join(str(value or "").split()).casefold()


def _place_ids(place: dict[str, Any] | None) -> set[str]:
    place = place or {}
    return {
        str(place.get(key)).strip()
        for key in ("id", "source_id")
        if str(place.get(key) or "").strip()
    }


def _candidate_ids(candidate: dict[str, Any] | None) -> set[str]:
    candidate = candidate or {}
    ids = {
        str(candidate.get("candidate_id") or "").strip(),
        *_place_ids(candidate.get("place") or {}),
    }
    return {item for item in ids if item}


def _distance_km(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float | None:
    first = (left or {}).get("coordinates") or {}
    second = (right or {}).get("coordinates") or {}
    try:
        lon1, lat1 = float(first["longitude"]), float(first["latitude"])
        lon2, lat2 = float(second["longitude"]), float(second["latitude"])
    except (KeyError, TypeError, ValueError):
        return None
    dlon, dlat = radians(lon2 - lon1), radians(lat2 - lat1)
    value = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(value))


def candidate_is_excluded(
    candidate: dict[str, Any],
    excluded_places: list[dict[str, Any]] | None,
    *,
    category: str | None = None,
) -> bool:
    """Return whether a provider candidate matches an applied user removal."""

    if not excluded_places:
        return False
    place = candidate.get("place") or {}
    candidate_ids = _candidate_ids(candidate)
    candidate_name = normalize_exclusion_name(place.get("name"))
    for excluded in excluded_places:
        if not isinstance(excluded, dict):
            continue
        excluded_category = str(excluded.get("category") or "").strip()
        if category and excluded_category and excluded_category != category:
            continue
        if candidate_ids.intersection(
            {
                str(excluded.get(key) or "").strip()
                for key in ("candidate_id", "place_id", "source_id")
                if str(excluded.get(key) or "").strip()
            }
        ):
            return True
        if candidate_name and candidate_name == str(excluded.get("name_key") or ""):
            return True
        if _distance_km(place, excluded) is not None and _distance_km(place, excluded) <= 1.0:
            return True
    return False


def filter_excluded_candidates(
    candidates: dict[str, list[dict[str, Any]]],
    excluded_places: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    if not excluded_places:
        return candidates
    return {
        category: [
            item
            for item in items
            if not candidate_is_excluded(item, excluded_places, category=category)
        ]
        for category, items in candidates.items()
    }


def exclusion_record(
    activity: Any,
    *,
    category: str,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    place = activity.place.model_dump(mode="json") if hasattr(activity.place, "model_dump") else dict(activity.place or {})
    candidate = candidate or {}
    candidate_place = candidate.get("place") or {}
    candidate_id = str(candidate.get("candidate_id") or "").strip() or None
    place_id = str(candidate_place.get("id") or place.get("id") or "").strip() or None
    source_id = str(candidate_place.get("source_id") or place.get("source_id") or "").strip() or None
    coordinates = place.get("coordinates") or candidate_place.get("coordinates")
    return {
        "name": str(place.get("name") or "").strip(),
        "name_key": normalize_exclusion_name(place.get("name")),
        "category": category,
        "candidate_id": candidate_id,
        "place_id": place_id,
        "source_id": source_id,
        "coordinates": coordinates,
        "reason": "user_removed",
    }


def remember_exclusion(
    state: dict[str, Any],
    activity: Any,
    *,
    category: str,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = exclusion_record(activity, category=category, candidate=candidate)
    exclusions = state.setdefault("excluded_places", [])
    if not isinstance(exclusions, list):
        exclusions = []
        state["excluded_places"] = exclusions
    exclusions[:] = [
        item
        for item in exclusions
        if not (
            isinstance(item, dict)
            and (
                (record.get("candidate_id") and item.get("candidate_id") == record["candidate_id"])
                or (
                    record.get("name_key")
                    and item.get("name_key") == record["name_key"]
                    and item.get("category") == record["category"]
                )
            )
        )
    ]
    exclusions.append(record)
    return record


def remember_named_exclusions(
    state: dict[str, Any],
    trip: Any,
    names: list[str],
) -> list[dict[str, Any]]:
    """Persist explicit natural-language removals for scheduled activities.

    UI delete patches call :func:`remember_exclusion` directly.  This companion
    path covers a confirmed chat instruction such as “不要再安排某景点” so
    the next provider search cannot silently add it back.  It intentionally
    matches only the Agent's structured activity names; raw user text is never
    parsed here.
    """

    wanted = {
        normalize_exclusion_name(name)
        for name in names
        if normalize_exclusion_name(name)
    }
    if not wanted:
        return []
    category_by_type = {
        "attraction": "attractions",
        "hotel": "hotels",
        "meal": "meals",
    }
    saved: list[dict[str, Any]] = []
    for day in getattr(trip, "days", []) or []:
        for activity in getattr(day, "activities", []) or []:
            if normalize_exclusion_name(getattr(getattr(activity, "place", None), "name", "")) not in wanted:
                continue
            category = category_by_type.get(str(getattr(activity, "type", "") or ""), str(getattr(activity, "type", "") or ""))
            candidate = next(
                (
                    item
                    for item in (state.get("tourism_candidates", {}).get(category, []) or [])
                    if normalize_exclusion_name((item.get("place") or {}).get("name"))
                    == normalize_exclusion_name(getattr(activity.place, "name", ""))
                ),
                None,
            )
            saved.append(
                remember_exclusion(
                    state,
                    activity,
                    category=category,
                    candidate=candidate,
                )
            )
    return saved


def clear_exclusion_for_candidate(
    state: dict[str, Any],
    candidate: dict[str, Any],
    *,
    category: str | None = None,
) -> None:
    exclusions = state.get("excluded_places")
    if not isinstance(exclusions, list):
        return
    state["excluded_places"] = [
        item
        for item in exclusions
        if not candidate_is_excluded(candidate, [item], category=category)
    ]


def clear_exclusions_for_names(state: dict[str, Any], names: list[str]) -> None:
    """Clear deletion markers only for explicitly re-added names."""

    exclusions = state.get("excluded_places")
    if not isinstance(exclusions, list):
        return
    keys = {normalize_exclusion_name(name) for name in names if normalize_exclusion_name(name)}
    state["excluded_places"] = [
        item
        for item in exclusions
        if not isinstance(item, dict)
        or str(item.get("name_key") or "") not in keys
    ]
