"""``sky.adsb`` — live feed, statistics and ingest health.

``adsb_aircraft.json`` is a hand-built fixture (the router's OpenAPI example
omits the interesting cases); its second element is an aircraft with **no
position at all**, which is the shape that breaks naive typings.

The namespace is not attached to the client yet (task A8 does the wiring), so
the tests instantiate ``Adsb``/``AsyncAdsb`` directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import BadRequestError
from skylink_api.models.adsb import AdsbAircraft, AdsbAircraftList, AdsbHealth, AdsbStatistics
from skylink_api.resources.adsb import (
    MAX_ITER_PAGE_SIZE,
    Adsb,
    AsyncAdsb,
    _aircraft_spec,
    _health_spec,
    _statistics_spec,
)

BBOX = (51.0, -1.0, 52.0, 0.5)
BBOX_WIRE = "51.0,-1.0,52.0,0.5"


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


@pytest.fixture
def adsb(client: SkyLink) -> Adsb:
    return Adsb(client)


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    spec = _aircraft_spec()
    assert (spec.method, spec.path) == ("GET", "/adsb/aircraft")
    assert spec.cast_to is AdsbAircraftList
    # photos defaults to False for ADS-B (the aircraft lookup defaults to True).
    assert spec.query is not None
    assert spec.query["photos"] is False
    # Every unset filter is None so `build_query` drops it.
    assert set(spec.query) == {
        "icao24",
        "callsign",
        "lat",
        "lon",
        "radius",
        "bbox",
        "min_alt",
        "max_alt",
        "min_speed",
        "max_speed",
        "registration",
        "airline",
        "photos",
        "limit",
        "offset",
    }
    assert {key for key, value in spec.query.items() if value is None} == set(spec.query) - {
        "photos"
    }

    # bbox tuples are flattened by the builder, not by the transport.
    bbox_spec = _aircraft_spec(bbox=BBOX)
    assert bbox_spec.query is not None
    assert bbox_spec.query["bbox"] == BBOX_WIRE
    passthrough = _aircraft_spec(bbox="51,-1,52,0.5")
    assert passthrough.query is not None
    assert passthrough.query["bbox"] == "51,-1,52,0.5"

    assert _statistics_spec().path == "/adsb/aircraft/statistics"
    assert _statistics_spec().cast_to is AdsbStatistics
    assert _health_spec().path == "/adsb/health"
    assert _health_spec().cast_to is AdsbHealth


def test_limit_has_no_upper_bound() -> None:
    """v3.1 declares ``limit`` with ``ge=1`` and no ``le`` — the SDK must not cap it."""

    spec = _aircraft_spec(limit=25_000, offset=24_000)
    assert spec.query is not None
    assert spec.query["limit"] == 25_000
    assert spec.query["offset"] == 24_000


# ── aircraft ─────────────────────────────────────────────────────────────────


def test_aircraft_happy_path(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))

    result = adsb.aircraft()

    request = route.calls.last.request
    assert request.url.path == "/v3.1/adsb/aircraft"
    assert request.url.params["photos"] == "false"
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, AdsbAircraftList)
    assert result.total_count == 2
    first = result.aircraft[0]
    assert first.icao24 == "4ca1fb"
    assert first.callsign == "BAW117"
    assert first.altitude == 36000
    assert first.ground_speed == 452.5
    assert first.is_on_ground is False
    assert first.airline == "British Airways"
    # Naive on the wire: no Z, no offset — a real datetime with tzinfo None.
    assert first.last_seen == datetime(2026, 2, 11, 12, 0, 3, 412870)
    assert first.last_seen is not None
    assert first.last_seen.tzinfo is None
    assert result.timestamp == datetime(2026, 2, 11, 12, 0, 4, 881233)


def test_aircraft_without_position_survives(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    """A Mode S transponder broadcasting only identity: every position field null."""

    _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))

    result = adsb.aircraft()

    ghost = result.aircraft[1]
    assert ghost.icao24 == "a1b2c3"
    assert ghost.latitude is None
    assert ghost.longitude is None
    assert ghost.altitude is None
    assert ghost.ground_speed is None
    assert ghost.track is None
    assert ghost.vertical_rate is None
    assert ghost.is_on_ground is None
    assert ghost.callsign is None
    assert ghost.registration is None
    assert ghost.photo_url is None
    # Timestamps are the only fields the feed always has.
    assert ghost.first_seen == datetime(2026, 2, 11, 11, 59, 12, 110447)


def test_aircraft_all_filters_are_serialised(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))

    adsb.aircraft(
        icao24="4ca1fb",
        callsign="BAW",
        lat=51.5,
        lon=-0.45,
        radius=100,
        bbox=BBOX,
        min_alt=1000,
        max_alt=40000,
        min_speed=100,
        max_speed=600,
        registration="G-STBA",
        airline="British",
        photos=True,
        limit=50,
        offset=100,
    )

    params = route.calls.last.request.url.params
    assert params["icao24"] == "4ca1fb"
    assert params["callsign"] == "BAW"
    assert params["lat"] == "51.5"
    assert params["lon"] == "-0.45"
    assert params["radius"] == "100"
    assert params["bbox"] == BBOX_WIRE
    assert params["min_alt"] == "1000"
    assert params["max_alt"] == "40000"
    assert params["min_speed"] == "100"
    assert params["max_speed"] == "600"
    assert params["registration"] == "G-STBA"
    assert params["airline"] == "British"
    assert params["photos"] == "true"
    assert params["limit"] == "50"
    assert params["offset"] == "100"


def test_aircraft_unset_filters_are_dropped(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))

    adsb.aircraft(limit=10)

    params = route.calls.last.request.url.params
    assert params["limit"] == "10"
    # Never sent as empty strings — dropped entirely.
    for dropped in ("icao24", "callsign", "bbox", "lat", "lon", "radius", "offset"):
        assert dropped not in params


def test_aircraft_unknown_fields_and_empty_feed(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    _mock(
        respx_mock,
        "/adsb/aircraft",
        {"aircraft": [], "total_count": 0, "timestamp": "2026-02-11T12:00:04", "squawk": "7700"},
    )

    result = adsb.aircraft()

    assert result.aircraft == []
    assert result.total_count == 0
    # extra="allow": backend additions never break a pinned SDK.
    assert result.model_extra is not None
    assert result.model_extra["squawk"] == "7700"


def test_aircraft_bad_bbox_raises(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/adsb/aircraft").mock(
        return_value=httpx.Response(
            400,
            json={"detail": "Invalid bounding box format: Invalid bounding box: lat1 < lat2"},
        )
    )

    with pytest.raises(BadRequestError) as excinfo:
        adsb.aircraft(bbox=(52.0, 0.5, 51.0, -1.0))

    assert excinfo.value.status_code == 400


def test_aircraft_request_options_are_forwarded(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))

    adsb.aircraft(request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}})

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


# ── iter_aircraft (auto-pagination) ──────────────────────────────────────────


def _page(*addresses: str, total: int | None = None) -> dict[str, Any]:
    """A feed page holding one row per address."""

    return {
        "aircraft": [{"icao24": address, "callsign": f"CS{address[-3:]}"} for address in addresses],
        "total_count": total if total is not None else len(addresses),
    }


def _pages(respx_mock: respx.MockRouter, *payloads: dict[str, Any]) -> respx.Route:
    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}/adsb/aircraft").mock(
        side_effect=[httpx.Response(200, json=payload) for payload in payloads]
    )


def _limits_and_offsets(route: respx.Route) -> list[tuple[str | None, str | None]]:
    return [
        (call.request.url.params.get("limit"), call.request.url.params.get("offset"))
        for call in route.calls
    ]


def test_iter_aircraft_walks_three_pages(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _pages(
        respx_mock,
        _page("aaa001", "aaa002", total=5),
        _page("aaa003", "aaa004", total=5),
        _page("aaa005", total=5),  # short page ends the walk
    )

    found = list(adsb.iter_aircraft(page_size=2))

    assert [aircraft.icao24 for aircraft in found] == [
        "aaa001",
        "aaa002",
        "aaa003",
        "aaa004",
        "aaa005",
    ]
    assert all(isinstance(aircraft, AdsbAircraft) for aircraft in found)
    assert _limits_and_offsets(route) == [("2", "0"), ("2", "2"), ("2", "4")]


def test_iter_aircraft_stops_on_a_full_final_page_followed_by_an_empty_one(
    adsb: Adsb, respx_mock: respx.MockRouter
) -> None:
    """A result that is an exact multiple of ``page_size`` costs one extra request."""

    route = _pages(respx_mock, _page("aaa001", "aaa002"), _page())

    found = list(adsb.iter_aircraft(page_size=2))

    assert len(found) == 2
    assert route.call_count == 2


def test_iter_aircraft_empty_first_page_yields_nothing(
    adsb: Adsb, respx_mock: respx.MockRouter
) -> None:
    route = _pages(respx_mock, _page())

    assert list(adsb.iter_aircraft(page_size=50)) == []
    assert route.call_count == 1


def test_iter_aircraft_max_items_stops_mid_page(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _pages(respx_mock, _page("aaa001", "aaa002", "aaa003"))

    found = list(adsb.iter_aircraft(page_size=3, max_items=2))

    assert [aircraft.icao24 for aircraft in found] == ["aaa001", "aaa002"]
    assert route.call_count == 1


def test_iter_aircraft_last_request_asks_only_for_what_is_left(
    adsb: Adsb, respx_mock: respx.MockRouter
) -> None:
    """``max_items`` shrinks the final ``limit`` instead of paying for extra rows."""

    route = _pages(respx_mock, _page("aaa001", "aaa002"), _page("aaa003"))

    found = list(adsb.iter_aircraft(page_size=2, max_items=3))

    assert len(found) == 3
    assert _limits_and_offsets(route) == [("2", "0"), ("1", "2")]


def test_iter_aircraft_stops_when_the_backend_ignores_offset(
    adsb: Adsb, respx_mock: respx.MockRouter
) -> None:
    """Same ``icao24`` set twice ⇒ the walk is looping; stop instead of spinning."""

    route = _pages(
        respx_mock,
        _page("aaa001", "aaa002", total=99),
        _page("aaa001", "aaa002", total=99),
        _page("aaa001", "aaa002", total=99),
    )

    found = list(adsb.iter_aircraft(page_size=2))

    assert [aircraft.icao24 for aircraft in found] == ["aaa001", "aaa002"]
    assert route.call_count == 2  # the repeat is detected, not yielded


def test_iter_aircraft_forwards_every_filter(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _pages(respx_mock, _page("aaa001"))

    list(adsb.iter_aircraft(page_size=10, bbox=BBOX, airline="British", min_alt=1000, photos=True))

    params = route.calls.last.request.url.params
    assert params["bbox"] == BBOX_WIRE
    assert params["airline"] == "British"
    assert params["min_alt"] == "1000"
    assert params["photos"] == "true"
    assert params["limit"] == "10"


def test_iter_aircraft_is_lazy(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    """Building the iterator must not fetch anything; ``break`` must stop fetching."""

    route = _pages(respx_mock, _page("aaa001", "aaa002"), _page("aaa003", "aaa004"))

    iterator = adsb.iter_aircraft(page_size=2)
    assert route.call_count == 0

    first = next(iter(iterator))

    assert first.icao24 == "aaa001"
    assert route.call_count == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_size": 0},
        {"page_size": -1},
        {"page_size": "100"},  # not a number
        {"max_items": 0},
        {"max_items": -5},
    ],
)
def test_iter_aircraft_rejects_bad_paging_before_any_request(
    adsb: Adsb, respx_mock: respx.MockRouter, kwargs: dict[str, Any]
) -> None:
    route = _pages(respx_mock, _page("aaa001"))

    with pytest.raises(ValueError):
        adsb.iter_aircraft(**kwargs)

    assert route.call_count == 0


def test_iter_aircraft_clamps_an_oversized_page_size(
    adsb: Adsb, respx_mock: respx.MockRouter
) -> None:
    """Above the cap is clamped, not rejected — the rule shared with the TS SDK."""

    route = _pages(respx_mock, _page("aaa001"))

    list(adsb.iter_aircraft(page_size=MAX_ITER_PAGE_SIZE + 5000))

    assert route.calls.last.request.url.params["limit"] == str(MAX_ITER_PAGE_SIZE)


def test_max_iter_page_size_is_documented_and_generous() -> None:
    assert MAX_ITER_PAGE_SIZE == 1000


# ── statistics ───────────────────────────────────────────────────────────────

STATS_PAYLOAD: dict[str, Any] = {
    "total_aircraft": 4691,
    "positioned_aircraft": 4102,
    "on_ground": 318,
    "airborne": 3784,
    "altitude_stats": {
        "min_altitude": 25,
        "max_altitude": 45000,
        "avg_altitude": 21837.4,
    },
    "timestamp": "2026-02-11T12:00:04.881233",
}


def test_statistics(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/adsb/aircraft/statistics", STATS_PAYLOAD)

    stats = adsb.statistics()

    assert route.calls.last.request.url.path == "/v3.1/adsb/aircraft/statistics"
    assert isinstance(stats, AdsbStatistics)
    assert stats.total_aircraft == 4691
    assert stats.positioned_aircraft == 4102
    assert stats.on_ground == 318
    assert stats.airborne == 3784
    # int stays int, float stays float — no silent widening.
    assert stats.altitude_stats.min_altitude == 25
    assert isinstance(stats.altitude_stats.min_altitude, int)
    assert isinstance(stats.altitude_stats.avg_altitude, float)
    assert stats.timestamp == datetime(2026, 2, 11, 12, 0, 4, 881233)


def test_statistics_empty_altitude_stats(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    """Nothing airborne ⇒ ``altitude_stats: {}`` — every leaf must be optional."""

    _mock(
        respx_mock,
        "/adsb/aircraft/statistics",
        {
            "total_aircraft": 3,
            "positioned_aircraft": 0,
            "on_ground": 3,
            "airborne": 0,
            "altitude_stats": {},
            "timestamp": "2026-02-11T12:00:04.881233",
        },
    )

    stats = adsb.statistics()

    assert stats.airborne == 0
    assert stats.altitude_stats.min_altitude is None
    assert stats.altitude_stats.max_altitude is None
    assert stats.altitude_stats.avg_altitude is None


def test_statistics_missing_keys_fall_back_to_zero(
    adsb: Adsb, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/adsb/aircraft/statistics", {})

    stats = adsb.statistics()

    assert (stats.total_aircraft, stats.positioned_aircraft) == (0, 0)
    assert stats.altitude_stats.avg_altitude is None
    assert stats.timestamp is None


# ── health ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["healthy", "offline", "connected_no_data", "degraded"])
def test_health_all_four_statuses(adsb: Adsb, respx_mock: respx.MockRouter, status: str) -> None:
    """All four backend states arrive as plain ``str`` — never a validation error."""

    _mock(
        respx_mock,
        "/adsb/health",
        {
            "status": status,
            "connected": status in {"healthy", "connected_no_data"},
            "active_aircraft_count": 4691 if status == "healthy" else 0,
            "connection_uptime": None,
            "last_message_received": "2026-02-11T12:00:03.412870" if status == "healthy" else None,
        },
    )

    health = adsb.health()

    assert isinstance(health, AdsbHealth)
    assert health.status == status
    assert isinstance(health.status, str)
    assert health.connection_uptime is None


def test_health_unknown_status_is_not_an_error(adsb: Adsb, respx_mock: respx.MockRouter) -> None:
    route = _mock(
        respx_mock,
        "/adsb/health",
        {"status": "reconnecting", "connected": False, "active_aircraft_count": 0},
    )

    health = adsb.health()

    assert route.calls.last.request.url.path == "/v3.1/adsb/health"
    assert health.status == "reconnecting"
    assert health.connected is False
    assert health.last_message_received is None


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_aircraft(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))

    result = await AsyncAdsb(async_client).aircraft(bbox=BBOX, limit=100)

    params = route.calls.last.request.url.params
    assert params["bbox"] == BBOX_WIRE
    assert params["limit"] == "100"
    assert params["photos"] == "false"
    assert isinstance(result, AdsbAircraftList)
    assert result.total_count == 2


async def test_async_iter_aircraft(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _pages(respx_mock, _page("aaa001", "aaa002"), _page("aaa003"))

    found = [aircraft async for aircraft in AsyncAdsb(async_client).iter_aircraft(page_size=2)]

    assert [aircraft.icao24 for aircraft in found] == ["aaa001", "aaa002", "aaa003"]
    assert _limits_and_offsets(route) == [("2", "0"), ("2", "2")]


async def test_async_iter_aircraft_max_items_and_validation(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    _pages(respx_mock, _page("aaa001", "aaa002"))
    adsb = AsyncAdsb(async_client)

    found = [aircraft async for aircraft in adsb.iter_aircraft(page_size=5, max_items=1)]

    assert [aircraft.icao24 for aircraft in found] == ["aaa001"]
    with pytest.raises(ValueError):
        adsb.iter_aircraft(page_size=0)


async def test_async_statistics_and_health(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/adsb/aircraft/statistics", {**STATS_PAYLOAD, "altitude_stats": {}})
    _mock(respx_mock, "/adsb/health", {"status": "healthy", "connected": True})

    adsb = AsyncAdsb(async_client)

    stats = await adsb.statistics()
    health = await adsb.health()

    assert stats.altitude_stats.max_altitude is None
    assert health.status == "healthy"
