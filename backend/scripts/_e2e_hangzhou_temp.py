import json
import time

import requests


base = "http://localhost:8000/api/v1"
raw = (
    "下个月找个周末去杭州，最多五天，我不租车，高铁往返，住在杭州东站附近但不想每天都回车站。"
    "西湖、灵隐寺和几处适合慢慢走的地方想看看，地铁公交、骑行和短距离步行你帮我搭配，"
    "遇到下雨给我留室内选择，也算算每天大概走多少路。从武汉出发。"
)
request = {
    "raw_text": raw,
    "origin": {"name": "武汉"},
    "destination": {"name": "杭州"},
    "destination_names": ["杭州"],
    "destination_scope": "city",
    "travel_intents": ["慢游", "文化观光", "室内备选"],
    "start_date": "2026-10-03",
    "end_date": "2026-10-04",
    "travelers": 1,
    "preferences": [
        "住在杭州东站附近",
        "不想每天都回车站",
        "雨天室内备选",
        "计算每日步行量",
    ],
    "transport_modes": ["train", "transit", "riding", "walking"],
    "max_days": 5,
}
trip = requests.post(
    f"{base}/trips",
    json={"title": "杭州公共交通完整链路验证", "request": request},
    timeout=60,
).json()
trip_id = trip["id"]
print(f"TRIP_ID={trip_id}", flush=True)
started = requests.post(f"{base}/trips/{trip_id}/planning/start", timeout=60)
print(f"START={started.status_code}", flush=True)
last = None
snapshot = {}
for _ in range(180):
    snapshot = requests.get(f"{base}/trips/{trip_id}/planning", timeout=30).json()
    marker = (
        snapshot.get("status"),
        (snapshot.get("progress") or {}).get("value"),
        (snapshot.get("progress") or {}).get("node"),
    )
    if marker != last:
        print(f"PROGRESS={marker!r}", flush=True)
        last = marker
    if snapshot.get("status") in {"completed", "failed"}:
        break
    time.sleep(2)

trip = requests.get(f"{base}/trips/{trip_id}", timeout=30).json()
stages = [stage for day in trip.get("days") or [] for stage in day.get("stages") or []]
print(
    json.dumps(
        {
            "trip_id": trip_id,
            "status": snapshot.get("status"),
            "error": snapshot.get("error"),
            "days": len(trip.get("days") or []),
            "stage_count": len(stages),
            "modes": [stage.get("mode") for stage in stages],
            "titles": [stage.get("title") for stage in stages],
            "weather_count": sum(1 for stage in stages if stage.get("weather")),
            "route_count": sum(
                1 for stage in stages if (stage.get("route") or {}).get("segments")
            ),
            "verification": trip.get("verification"),
        },
        ensure_ascii=True,
    ),
    flush=True,
)
