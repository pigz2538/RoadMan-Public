"""Season-aware candidate checks used by the tourism planning pass.

The ranking Agent is the primary decision maker.  This module only provides a
small, conservative safety net for unambiguous seasonal mismatches when the
remote Agent is unavailable or omits a decision.  It never invents a season
for an ordinary attraction and never removes a candidate without a clear
seasonal signal in the provider name/description/category metadata.
"""

from __future__ import annotations

from datetime import date
from typing import Any


# Windows are intentionally broad.  They are safety windows, not opening-hour
# promises: an Agent can still keep a candidate when its provider data says it
# is an indoor/all-season activity.
_SEASON_WINDOWS: tuple[tuple[tuple[str, ...], set[int], str], ...] = (
    (("樱花", "赏樱", "樱园"), {2, 3, 4, 5}, "樱花季通常在春季"),
    (("赏枫", "枫叶", "红叶", "红枫", "秋色"), {10, 11, 12}, "赏枫/红叶通常在秋季"),
    (("滑雪", "滑雪场", "雪场", "滑冰"), {11, 12, 1, 2, 3}, "户外雪上项目通常在冬季"),
    (("梅花", "赏梅"), {12, 1, 2, 3}, "梅花通常在冬末至早春"),
    (("油菜花", "菜花"), {2, 3, 4}, "油菜花通常在春季"),
    (("郁金香", "薰衣草"), {3, 4, 5, 6, 7, 8}, "花期通常在春夏季"),
    (("漂流", "水上乐园"), {5, 6, 7, 8, 9, 10}, "水上活动通常在暖季"),
)


def assess_candidate_season(
    candidate: dict[str, Any],
    start_date: date | None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Return a structured seasonal assessment for one candidate.

    ``seasonal_fit`` is deliberately tri-state: ``True`` means the broad
    window is compatible, ``False`` means a clear mismatch, and ``None`` means
    there is not enough evidence to decide.  This lets the remote Agent make
    the nuanced call for indoor venues and unusual regional seasons.
    """

    if start_date is None:
        return {"seasonal_fit": None, "seasonal_reason": "缺少出行日期，交由 Agent 复核"}
    place = candidate.get("place") or {}
    text = " ".join(
        str(value or "")
        for value in (
            place.get("name"),
            candidate.get("description"),
            candidate.get("categories"),
            candidate.get("kinds"),
        )
    ).casefold()
    if not text.strip():
        return {"seasonal_fit": None, "seasonal_reason": "候选缺少季节信息"}

    months = {start_date.month}
    if end_date is not None:
        # A trip spanning a month boundary should be accepted if any travel
        # day falls in the activity's broad safe window.
        cursor = start_date
        while cursor <= end_date:
            months.add(cursor.month)
            cursor = cursor.fromordinal(cursor.toordinal() + 1)

    for tokens, safe_months, reason in _SEASON_WINDOWS:
        if not any(token.casefold() in text for token in tokens):
            continue
        if months & safe_months:
            return {"seasonal_fit": True, "seasonal_reason": f"{reason}，出行月份有重合"}
        # Indoor venues are not rejected by the fallback.  Let the Agent or
        # provider details override the broad outdoor interpretation.
        if any(marker in text for marker in ("室内", "全年", "全天候", "室内馆")):
            return {"seasonal_fit": None, "seasonal_reason": "疑似室内/全年项目，需 Agent 结合详情复核"}
        return {"seasonal_fit": False, "seasonal_reason": f"{reason}，与当前出行月份不匹配"}

    return {"seasonal_fit": None, "seasonal_reason": "未识别到明确季节限制"}


def apply_seasonal_guard(
    candidates: dict[str, list[dict[str, Any]]],
    start_date: date | None,
    end_date: date | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Annotate candidates and move clearly unsuitable attractions to backup.

    Candidates remain available for the recommendation panel, but the tourism
    scheduler can exclude ``seasonal_excluded`` items from the formal plan.
    Returning a report makes the decision visible to the Agent panel instead
    of silently dropping a user's option.
    """

    report: list[dict[str, Any]] = []
    for category, items in candidates.items():
        if category != "attractions":
            continue
        for item in items:
            assessment = assess_candidate_season(item, start_date, end_date)
            # A remote Agent decision has priority, while the deterministic
            # guard can still reject an unmistakable off-season activity.
            agent_fit = item.get("seasonal_fit")
            if agent_fit is False:
                assessment = {
                    "seasonal_fit": False,
                    "seasonal_reason": item.get("seasonal_reason")
                    or item.get("agent_seasonal_reason")
                    or "Agent 判断与当前出行季节不匹配",
                }
            elif assessment["seasonal_fit"] is None and agent_fit is not None:
                assessment = {
                    "seasonal_fit": bool(agent_fit),
                    "seasonal_reason": item.get("seasonal_reason")
                    or item.get("agent_seasonal_reason")
                    or "Agent 季节适配判断",
                }
            item.update(assessment)
            item["seasonal_excluded"] = assessment["seasonal_fit"] is False
            if item["seasonal_excluded"]:
                item["backup"] = True
                item["seasonal_warning"] = assessment["seasonal_reason"]
                report.append(
                    {
                        "candidate_id": item.get("candidate_id"),
                        "name": (item.get("place") or {}).get("name"),
                        "reason": assessment["seasonal_reason"],
                        "severity": "warning",
                    }
                )
        items.sort(
            key=lambda item: (
                bool(item.get("seasonal_excluded")),
                -(item.get("agent_score") if item.get("agent_score") is not None else item.get("score", 0)),
                (item.get("place") or {}).get("name", ""),
            )
        )
    return candidates, report


def parse_trip_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
