from datetime import date

import pytest

from app.skills.base import SkillContext
from app.skills.fallbacks import (
    FreeApiOilAdapter,
    FreeApiTrainAdapter,
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
