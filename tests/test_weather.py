"""``sky.weather`` — METAR, TAF, winds aloft, PIREPs, AIRMET/SIGMET.

Only ``weather_metar.json`` is a recorded fixture (verbatim from the router's
OpenAPI example); the other endpoints have no example block upstream, so their
payloads are built inline from the backend pydantic models documented in
research/02.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.weather import (
    AirSigmetResponse,
    Metar,
    MetarWithParsed,
    PirepsResponse,
    Taf,
    TafWithParsed,
    WindsAloftResponse,
)
from skylink_api.resources.weather import (
    Weather,
    _airsigmet_spec,
    _metar_spec,
    _pireps_spec,
    _taf_spec,
    _winds_aloft_spec,
)

BBOX = (40.0, -80.0, 42.0, -73.0)
BBOX_WIRE = "40.0,-80.0,42.0,-73.0"


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── namespace wiring ─────────────────────────────────────────────────────────


def test_namespace_is_attached_and_cached(client: SkyLink) -> None:
    assert isinstance(client.weather, Weather)
    # cached_property: the same instance on every access.
    assert client.weather is client.weather


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    assert _metar_spec("KJFK").path == "/weather/metar/KJFK"
    assert _metar_spec("KJFK").cast_to is Metar
    assert _metar_spec("KJFK", parsed=True).cast_to is MetarWithParsed

    assert _taf_spec("EGLL").path == "/weather/taf/EGLL"
    assert _taf_spec("EGLL").cast_to is Taf
    assert _taf_spec("EGLL", parsed=True).cast_to is TafWithParsed

    # bbox tuples are flattened by the builder, not by the transport.
    assert _winds_aloft_spec(bbox=BBOX).query == {
        "bbox": BBOX_WIRE,
        "forecast": 12,
        "level": "low",
    }
    assert _pireps_spec(bbox="40,-80,42,-73").query == {"bbox": "40,-80,42,-73", "hours": 2}
    assert _airsigmet_spec(bbox=BBOX).query == {"bbox": BBOX_WIRE, "type": None}

    assert {spec.method for spec in (_metar_spec("KJFK"), _pireps_spec(bbox=BBOX))} == {"GET"}


# ── METAR ────────────────────────────────────────────────────────────────────


def test_metar_happy_path(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/metar/KJFK", load_fixture("weather_metar"))

    metar = client.weather.metar("KJFK")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/weather/metar/KJFK"
    assert request.url.params["parsed"] == "false"
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(metar, Metar)
    assert metar.icao == "KJFK"
    assert metar.raw is not None
    assert metar.raw.startswith("METAR KJFK")
    assert metar.airport_name == "John F Kennedy International Airport"
    # The envelope timestamp is API-generated ISO-8601 with Z → a real datetime.
    assert metar.timestamp == datetime(2025, 9, 27, 12, 0, tzinfo=timezone.utc)


def test_metar_parsed_true(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    payload = {
        **load_fixture("weather_metar"),
        "parsed": {
            "wind": {"direction": 160, "speed": 13, "gust": None, "variable": None},
            "visibility": {"value": 10, "repr": "10SM"},
            "clouds": [{"type": "FEW", "base": 24, "repr": "FEW024"}],
            "temperature": 15,
            "dewpoint": 9,
            "altimeter": 30.2,
            "flight_rules": "VFR",
            "wx_codes": [],
            "remarks": "AO2 SLP216 T01500089",
            "time": "2025-09-27T18:51:00Z",
            "density_altitude": 289,
            "pressure_altitude": -47,
            "relative_humidity": 0.67,
        },
    }
    route = _mock(respx_mock, "/weather/metar/KJFK", payload)

    metar = client.weather.metar("KJFK", parsed=True)

    assert route.calls.last.request.url.params["parsed"] == "true"
    assert isinstance(metar, MetarWithParsed)
    assert metar.parsed is not None
    assert metar.parsed.wind is not None
    assert metar.parsed.wind.direction == 160
    assert metar.parsed.wind.gust is None
    assert metar.parsed.visibility is not None
    assert metar.parsed.visibility.repr == "10SM"
    assert [layer.repr for layer in metar.parsed.clouds] == ["FEW024"]
    assert metar.parsed.flight_rules == "VFR"
    assert metar.parsed.wx_codes == []
    # Opaque decoder timestamp stays a string.
    assert metar.parsed.time == "2025-09-27T18:51:00Z"


def test_metar_parsed_null_survives(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    """The backend swallows decoder failures and sends ``parsed: null``."""

    _mock(respx_mock, "/weather/metar/KJFK", {**load_fixture("weather_metar"), "parsed": None})

    metar = client.weather.metar("KJFK", parsed=True)

    assert isinstance(metar, MetarWithParsed)
    assert metar.parsed is None


def test_metar_keeps_whole_numbers_and_unknown_fields(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    payload = {
        "raw": "METAR KJFK ...",
        "icao": "KJFK",
        "airport_name": None,
        "timestamp": "2025-09-27T12:00:00Z",
        "brand_new_field": {"nested": 1},
        "parsed": {
            "wind": {"direction": 160, "speed": 13},
            "temperature": 15,
            "relative_humidity": 0.67,
        },
    }
    _mock(respx_mock, "/weather/metar/KJFK", payload)

    metar = client.weather.metar("KJFK", parsed=True)

    # extra="allow": additions on the backend never break a pinned SDK.
    assert metar.model_extra is not None
    assert metar.model_extra["brand_new_field"] == {"nested": 1}
    assert metar.airport_name is None
    assert metar.parsed is not None
    # int stays int, float stays float — no silent widening.
    assert metar.parsed.temperature == 15
    assert isinstance(metar.parsed.temperature, int)
    assert isinstance(metar.parsed.relative_humidity, float)
    # Missing collections fall back to empty lists.
    assert metar.parsed.clouds == []


def test_metar_request_options_are_forwarded(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/metar/KJFK", load_fixture("weather_metar"))

    client.weather.metar(
        "KJFK", request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}}
    )

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


def test_metar_404_raises(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar/ZZZZ").mock(
        return_value=httpx.Response(404, json={"detail": "Airport not found for ICAO code: ZZZZ"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        client.weather.metar("ZZZZ")

    assert excinfo.value.status_code == 404
    assert "ZZZZ" in str(excinfo.value)


# ── TAF ──────────────────────────────────────────────────────────────────────

TAF_PAYLOAD: dict[str, Any] = {
    "raw": "TAF KJFK 271720Z 2718/2824 16012KT P6SM FEW025 SCT250 FM272100 18010KT P6SM SCT025",
    "icao": "KJFK",
    "airport_name": "John F Kennedy International Airport",
    "timestamp": "2025-09-27T12:00:00Z",
}


def test_taf_happy_path(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/taf/KJFK", TAF_PAYLOAD)

    taf = client.weather.taf("KJFK")

    assert route.calls.last.request.url.path == "/v3.1/weather/taf/KJFK"
    assert route.calls.last.request.url.params["parsed"] == "false"
    assert isinstance(taf, Taf)
    assert taf.raw is not None
    assert taf.raw.startswith("TAF KJFK")


def test_taf_parsed_true(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    payload = {
        **TAF_PAYLOAD,
        "parsed": {
            "start_time": "2025-09-27T18:00:00Z",
            "end_time": "2025-09-28T24:00:00Z",
            "forecast": [
                {
                    "type": "FROM",
                    "start_time": "2025-09-27T18:00:00Z",
                    "end_time": "2025-09-27T21:00:00Z",
                    "wind": {"direction": 160, "speed": 12, "gust": None, "variable": None},
                    "visibility": {"value": 6, "repr": "P6SM"},
                    "clouds": [{"type": "FEW", "base": 25, "repr": "FEW025"}],
                    "wx_codes": [],
                    "flight_rules": "VFR",
                    "probability": None,
                    "turbulence": [],
                    "icing": [],
                }
            ],
        },
    }
    route = _mock(respx_mock, "/weather/taf/KJFK", payload)

    taf = client.weather.taf("KJFK", parsed=True)

    assert route.calls.last.request.url.params["parsed"] == "true"
    assert isinstance(taf, TafWithParsed)
    assert taf.parsed is not None
    assert len(taf.parsed.forecast) == 1
    period = taf.parsed.forecast[0]
    assert period.type == "FROM"
    assert period.wind is not None
    assert period.wind.speed == 12
    assert period.turbulence == []
    # Validity window stays an opaque string.
    assert taf.parsed.end_time == "2025-09-28T24:00:00Z"


def test_taf_parsed_null_survives(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/weather/taf/KJFK", {**TAF_PAYLOAD, "parsed": None})

    taf = client.weather.taf("KJFK", parsed=True)

    assert isinstance(taf, TafWithParsed)
    assert taf.parsed is None


# ── winds aloft ──────────────────────────────────────────────────────────────

WINDS_PAYLOAD: dict[str, Any] = {
    "bbox": BBOX_WIRE,
    "forecast_hour": 12,
    "level": "low",
    "valid_time": "130000Z",
    "stations": [
        {
            "station": "JFK",
            "latitude": 40.6413,
            "longitude": -73.7781,
            "winds": [
                {
                    "altitude_ft": 3000,
                    "wind_direction": 270,
                    "wind_speed_kt": 9,
                    "temperature_c": None,
                    "light_and_variable": False,
                    "raw": "2709",
                },
                {
                    "altitude_ft": 6000,
                    "wind_direction": None,
                    "wind_speed_kt": None,
                    "temperature_c": 8,
                    "light_and_variable": True,
                    "raw": "9900+08",
                },
            ],
            "raw_text": "JFK      2709 3012+08",
        }
    ],
    "total": 1,
}


def test_winds_aloft_defaults(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/winds-aloft", WINDS_PAYLOAD)

    winds = client.weather.winds_aloft(bbox=BBOX)

    request = route.calls.last.request
    assert request.url.path == "/v3.1/weather/winds-aloft"
    assert request.url.params["bbox"] == BBOX_WIRE
    assert request.url.params["forecast"] == "12"
    assert request.url.params["level"] == "low"

    assert isinstance(winds, WindsAloftResponse)
    assert winds.total == 1
    assert winds.valid_time == "130000Z"
    station = winds.stations[0]
    assert station.station == "JFK"
    assert [level.altitude_ft for level in station.winds] == [3000, 6000]
    assert station.winds[0].light_and_variable is False
    assert station.winds[1].light_and_variable is True
    assert station.winds[1].wind_direction is None


def test_winds_aloft_explicit_forecast_and_level(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/weather/winds-aloft", {**WINDS_PAYLOAD, "level": "high"})

    client.weather.winds_aloft(bbox="40,-80,42,-73", forecast=24, level="high")

    params = route.calls.last.request.url.params
    assert params["bbox"] == "40,-80,42,-73"
    assert params["forecast"] == "24"
    assert params["level"] == "high"


# ── PIREPs ───────────────────────────────────────────────────────────────────

PIREPS_PAYLOAD: dict[str, Any] = {
    "bbox": BBOX_WIRE,
    "hours": 6,
    "reports": [
        {
            "raw": "UA /OV JFK/TM 1845/FL085/TP B738/TB MOD/RM CONT MOD CHOP",
            "report_type": "UA",
            "location": "JFK",
            "time": "2025-01-15T18:45:00Z",
            "altitude": "FL085",
            "aircraft_type": "B738",
            "sky_conditions": None,
            "turbulence": "Moderate",
            "icing": None,
            "temperature": "-12",
            "wind": None,
            "remarks": "CONT MOD CHOP",
            "latitude": 40.64,
            "longitude": -73.78,
        }
    ],
    "total": 1,
}


def test_pireps(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/pireps", PIREPS_PAYLOAD)

    pireps = client.weather.pireps(bbox=BBOX, hours=6)

    request = route.calls.last.request
    assert request.url.path == "/v3.1/weather/pireps"
    assert request.url.params["bbox"] == BBOX_WIRE
    assert request.url.params["hours"] == "6"

    assert isinstance(pireps, PirepsResponse)
    assert pireps.total == 1
    report = pireps.reports[0]
    assert report.report_type == "UA"
    # Numeric-looking PIREP fields stay strings — they carry units/encodings.
    assert report.altitude == "FL085"
    assert report.temperature == "-12"
    assert report.latitude == 40.64


def test_pireps_default_hours_and_empty_result(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/weather/pireps", {"bbox": BBOX_WIRE, "hours": 2})

    pireps = client.weather.pireps(bbox=BBOX)

    assert route.calls.last.request.url.params["hours"] == "2"
    # No reports is a plain 200, not an error; defaults fill the envelope in.
    assert pireps.reports == []
    assert pireps.total == 0


# ── AIRMET / SIGMET ──────────────────────────────────────────────────────────

AIRSIGMET_PAYLOAD: dict[str, Any] = {
    "bbox": BBOX_WIRE,
    "reports": [
        {
            "raw": "WAUS46 KKCI 151445 WA6S AIRMET SIERRA",
            "bulletin_type": "AIRMET",
            "country": "US",
            "issuer": "KKCI",
            "area": "S",
            "report_type": "AIRMET",
            "start_time": "151445",
            "end_time": "152100",
            "body": "AIRMET IFR...",
            "region": "BOS",
            "observation": {
                "type": "IFR",
                "intensity": None,
                "floor": "SFC",
                "ceiling": "FL180",
                "coords": [{"lat": 41.0, "lon": -74.0}, {"lat": 42.0, "lon": -73.0}],
                "movement": {"direction": "270", "speed": "20"},
            },
            "forecast": None,
        }
    ],
    "total": 1,
    "filter_type": "airmet",
}


def test_airsigmet_with_type_filter(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/airsigmet", AIRSIGMET_PAYLOAD)

    result = client.weather.airsigmet(bbox=BBOX, type="airmet")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/weather/airsigmet"
    assert request.url.params["bbox"] == BBOX_WIRE
    assert request.url.params["type"] == "airmet"

    assert isinstance(result, AirSigmetResponse)
    assert result.filter_type == "airmet"
    report = result.reports[0]
    assert report.forecast is None
    assert report.observation is not None
    assert report.observation.floor == "SFC"
    assert report.observation.movement is not None
    # Movement direction/speed are strings on the wire, not numbers.
    assert report.observation.movement.direction == "270"
    assert [(c.lat, c.lon) for c in report.observation.coords] == [(41.0, -74.0), (42.0, -73.0)]


def test_airsigmet_type_is_omitted_when_none(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/airsigmet", {"bbox": BBOX_WIRE, "reports": [], "total": 0})

    result = client.weather.airsigmet(bbox=BBOX)

    params = route.calls.last.request.url.params
    assert "type" not in params
    assert result.reports == []
    assert result.filter_type is None


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_metar(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/weather/metar/KJFK", load_fixture("weather_metar"))

    metar = await async_client.weather.metar("KJFK")

    assert route.calls.last.request.url.path == "/v3.1/weather/metar/KJFK"
    assert isinstance(metar, Metar)
    assert metar.icao == "KJFK"


async def test_async_airsigmet_matches_sync_wire_format(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/weather/airsigmet", AIRSIGMET_PAYLOAD)

    result = await async_client.weather.airsigmet(bbox=BBOX, type="sigmet")

    assert route.calls.last.request.url.params["bbox"] == BBOX_WIRE
    assert route.calls.last.request.url.params["type"] == "sigmet"
    assert result.total == 1
