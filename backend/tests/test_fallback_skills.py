from datetime import date

import pytest

from app.skills.base import SkillContext
from app.skills.fallbacks import (
    AviationstackFlightAdapter,
    FreeApiOilAdapter,
    FreeApiTrainAdapter,
    Mcp12306TrainAdapter,
    OpenStreetMapGeocodeAdapter,
    SixApiFlightAdapter,
)


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._body


class _Client:
    response = None
    calls = []

    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        type(self).calls.append((url, kwargs))
        return type(self).response


class _McpClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        type(self).calls.append((url, kwargs))
        method = kwargs["json"]["method"]
        if method == "initialize":
            return _McpResponse(
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"mcp-session-id": "session-1"},
            )
        if method == "notifications/initialized":
            return _McpResponse({}, status_code=202)
        return _McpResponse(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "isError": False,
                    "content": [
                        {
                            "type": "text",
                            "text": '{"success":true,"trains":[{"train_no":"G423","from_station":"武汉站","to_station":"长沙南站","start_time":"08:10","arrive_time":"09:38","duration":"01:28","seats":{"second_class":"有"}}]}',
                        }
                    ],
                },
            }
        )

    async def delete(self, url, **kwargs):
        type(self).calls.append((url, kwargs))
        return _McpResponse({})


class _McpResponse(_Response):
    def __init__(self, body, status_code=200, headers=None):
        super().__init__(body, status_code)
        self.headers = headers or {}
        self.text = ""


@pytest.mark.asyncio
async def test_free_train_fallback_normalizes_overnight_and_seat(monkeypatch):
    _Client.response = _Response(
        {
            "code": 200,
            "msg": "ok",
            "data": [
                {
                    "TrainNumber": "Z99",
                    "start": "武汉站",
                    "end": "北京西站",
                    "DepartTime": "23:35",
                    "ArriveTime": "06:20",
                    "TimeDifference": "6小时45分",
                    "SeatList": {"软卧": "¥320"},
                }
            ],
        }
    )
    monkeypatch.setattr("app.skills.fallbacks.httpx.AsyncClient", _Client)
    result = await FreeApiTrainAdapter("https://example.test/train").execute(
        {"origin": "武汉", "destination": "北京", "dep_date": date(2026, 9, 4)},
        SkillContext(),
    )
    assert result.success is True
    item = result.data["items"][0]
    assert item["train_number"] == "Z99"
    assert item["arrival_at"].startswith("2026-09-05T06:20")
    assert item["seat_class"] == "软卧"
    assert item["price"] == 320
    assert result.sources[0].url.endswith("/doc/675")


@pytest.mark.asyncio
async def test_free_train_fallback_rejects_wrong_route(monkeypatch):
    _Client.response = _Response(
        {"code": 200, "data": [{"TrainNumber": "G1", "start": "上海", "end": "北京", "DepartTime": "08:00", "ArriveTime": "12:00"}]}
    )
    monkeypatch.setattr("app.skills.fallbacks.httpx.AsyncClient", _Client)
    result = await FreeApiTrainAdapter("https://example.test/train").execute(
        {"origin": "武汉", "destination": "北京", "dep_date": date(2026, 9, 4)},
        SkillContext(),
    )
    assert result.success is False
    assert result.error_code == "FREEAPI_TRAIN_NO_RESULTS"


@pytest.mark.asyncio
async def test_mcp_12306_fallback_runs_handshake_and_returns_real_train(monkeypatch):
    _McpClient.calls = []
    monkeypatch.setattr("app.skills.fallbacks.httpx.AsyncClient", _McpClient)
    result = await Mcp12306TrainAdapter("http://rail.test:8000").execute(
        {"origin": "武汉", "destination": "长沙", "dep_date": date(2026, 9, 4)},
        SkillContext(),
    )
    assert result.success is True
    assert result.data["items"][0]["train_number"] == "G423"
    assert result.data["items"][0]["departure_station"] == "武汉站"
    assert [call[1].get("json", {}).get("method") for call in _McpClient.calls[:3]] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]


@pytest.mark.asyncio
async def test_sixapi_flight_fallback_requires_key(monkeypatch):
    adapter = SixApiFlightAdapter("https://example.test/flight", "")
    result = await adapter.execute(
        {"origin": "武汉", "destination": "北京", "dep_date": date(2026, 9, 4)},
        SkillContext(),
    )
    assert result.success is False
    assert result.error_code == "SKILL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_sixapi_flight_fallback_normalizes_result(monkeypatch):
    _Client.response = _Response(
        {
            "code": 200,
            "data": [
                {
                    "flightNo": "MU2455",
                    "depTime": "17:50",
                    "arrTime": "19:45",
                    "airline": "东方航空",
                    "depAirportName": "武汉天河",
                    "arrAirportName": "北京首都",
                }
            ],
        }
    )
    monkeypatch.setattr("app.skills.fallbacks.httpx.AsyncClient", _Client)
    result = await SixApiFlightAdapter("https://example.test/flight", "key").execute(
        {"origin": "武汉", "destination": "北京", "dep_date": date(2026, 9, 4)},
        SkillContext(),
    )
    assert result.success is True
    assert result.data["items"][0]["flight_number"] == "MU2455"
    assert result.data["items"][0]["departure_airport"] == "武汉天河"


@pytest.mark.asyncio
async def test_aviationstack_flight_fallback_requires_real_number(monkeypatch):
    _Client.response = _Response(
        {
            "data": [
                {
                    "departure": {"scheduled": "2026-09-04T17:50:00+08:00", "airport": "Wuhan Tianhe"},
                    "arrival": {"scheduled": "2026-09-04T19:45:00+08:00", "airport": "Beijing Capital"},
                    "airline": {"name": "China Eastern"},
                    "flight": {"iata": "MU2455"},
                },
                {
                    "departure": {"scheduled": "2026-09-04T18:00:00+08:00"},
                    "arrival": {"scheduled": "2026-09-04T20:00:00+08:00"},
                    "flight": {},
                },
            ]
        }
    )
    monkeypatch.setattr("app.skills.fallbacks.httpx.AsyncClient", _Client)
    result = await AviationstackFlightAdapter("https://example.test/flights", "key").execute(
        {"origin": "武汉", "destination": "北京", "dep_date": date(2026, 9, 4)},
        SkillContext(),
    )
    assert result.success is True
    assert [item["flight_number"] for item in result.data["items"]] == ["MU2455"]


@pytest.mark.asyncio
async def test_oil_fallback_normalizes_public_prices(monkeypatch):
    _Client.response = _Response(
        {
            "code": 1,
            "data": {"province": "湖北", "t89": "7.12", "t92": "7.46", "t95": "7.98", "t98": "8.68"},
        }
    )
    monkeypatch.setattr("app.skills.fallbacks.httpx.AsyncClient", _Client)
    result = await FreeApiOilAdapter("https://example.test/oil", "id", "secret").execute(
        {"province": "湖北"},
        SkillContext(),
    )
    assert result.success is True
    assert result.data["prices"]["92"] == "7.46"
    assert result.data["province"] == "湖北"


@pytest.mark.asyncio
async def test_osm_geocode_fallback_normalizes_named_place(monkeypatch):
    _Client.response = _Response(
        [
            {
                "osm_type": "node",
                "osm_id": 123,
                "name": "喀纳斯",
                "display_name": "喀纳斯, 新疆维吾尔自治区, 中国",
                "lat": "48.6912931",
                "lon": "87.0300192",
            }
        ]
    )
    monkeypatch.setattr("app.skills.fallbacks.httpx.AsyncClient", _Client)
    result = await OpenStreetMapGeocodeAdapter("https://example.test/geocode").execute(
        {"query": "喀纳斯 新疆 中国"},
        SkillContext(),
    )
    assert result.success is True
    assert result.data["location"] == "87.0300192,48.6912931"
    assert result.sources[0].provider == "OpenStreetMap"
