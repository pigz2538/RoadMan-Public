from __future__ import annotations

from typing import Any, Iterable


def _value(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def walking_totals(stages: Iterable[Any]) -> tuple[int, float]:
    """Count walking stages plus station access and transit transfers."""
    minutes = 0
    distance_km = 0.0
    for stage in stages:
        if _value(stage, "mode") == "walking":
            minutes += int(_value(stage, "duration_minutes", 0) or 0)
            distance_km += float(_value(stage, "distance_km", 0) or 0)
        for leg in _value(stage, "transit_legs", []) or []:
            if _value(leg, "mode") != "walk":
                continue
            minutes += int(_value(leg, "duration_minutes", 0) or 0)
            distance_km += float(_value(leg, "distance_km", 0) or 0)
    return minutes, round(distance_km, 2)
