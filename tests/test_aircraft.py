"""``sky.aircraft`` — registry lookup, performance profiles, database stats.

The central trap: the lookup endpoints **never 404**. ``aircraft_found.json``
and ``aircraft_not_found.json`` pin both halves of that 200-with-a-sentinel
contract, and ``performance.json`` is verbatim from the router's OpenAPI
example.

The namespace is not attached to the client yet (task A8 does the wiring), so
the tests instantiate ``Aircraft``/``AsyncAircraft`` directly.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.aircraft import (
    AircraftDatabaseStats,
    AircraftLookup,
    AircraftPerformance,
)
from skylink_api.resources.aircraft import (
    Aircraft,
    AsyncAircraft,
    _by_icao24_spec,
    _by_registration_spec,
    _database_stats_spec,
    _performance_spec,
)


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


@pytest.fixture
def aircraft(client: SkyLink) -> Aircraft:
    return Aircraft(client)


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    reg = _by_registration_spec("G-STBA")
    assert (reg.method, reg.path) == ("GET", "/aircraft/registration/G-STBA")
    assert reg.cast_to is AircraftLookup
    # photos defaults to True here — the opposite of sky.adsb.aircraft().
    assert reg.query == {"photos": True}
    assert _by_registration_spec("G-STBA", photos=False).query == {"photos": False}

    hexa = _by_icao24_spec("4CA1FB")
    assert hexa.path == "/aircraft/icao24/4CA1FB"
    assert hexa.query == {"photos": True}
    assert hexa.cast_to is AircraftLookup

    perf = _performance_spec("B77W")
    assert perf.path == "/aircraft/performance/B77W"
    assert perf.query is None
    assert perf.cast_to is AircraftPerformance

    stats = _database_stats_spec()
    assert stats.path == "/aircraft/database/stats"
    assert stats.cast_to is AircraftDatabaseStats


# ── lookup ───────────────────────────────────────────────────────────────────


def test_by_registration_found(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/aircraft/registration/G-STBA", load_fixture("aircraft_found"))

    result = aircraft.by_registration("G-STBA")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/aircraft/registration/G-STBA"
    # Default is photos=true, matching the backend.
    assert request.url.params["photos"] == "true"
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, AircraftLookup)
    assert result.found is True
    assert result.query == "G-STBA"
    details = result.aircraft
    assert details is not None
    assert details.icao_type == "B77W"
    assert details.manufacturer_and_model == "Boeing 777-336(ER)"
    assert details.airline_code == "BAW"
    assert details.is_private_operator is False
    assert details.serial_number == "38593"
    # year_built is a STRING — the registry value is forwarded verbatim.
    assert details.year_built == "2010"
    assert isinstance(details.year_built, str)
    assert len(details.photos) == 1
    assert details.photos[0].photographer == "Jane Doe"


def test_by_registration_not_found_is_a_200_sentinel(
    aircraft: Aircraft, respx_mock: respx.MockRouter
) -> None:
    """A miss is ``200 {found: false, aircraft: null}``, never a 404."""

    _mock(respx_mock, "/aircraft/registration/ZZ-ZZZ", load_fixture("aircraft_not_found"))

    result = aircraft.by_registration("ZZ-ZZZ")

    assert isinstance(result, AircraftLookup)
    assert result.found is False
    assert result.aircraft is None
    assert result.query == "ZZ-ZZZ"


def test_by_registration_photos_false(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    payload = {
        **load_fixture("aircraft_found"),
        "aircraft": {**load_fixture("aircraft_found")["aircraft"], "photos": []},
    }
    route = _mock(respx_mock, "/aircraft/registration/G-STBA", payload)

    result = aircraft.by_registration("G-STBA", photos=False)

    assert route.calls.last.request.url.params["photos"] == "false"
    assert result.aircraft is not None
    assert result.aircraft.photos == []


def test_by_icao24_found(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    route = _mock(
        respx_mock,
        "/aircraft/icao24/4CA1FB",
        {**load_fixture("aircraft_found"), "query": "4CA1FB"},
    )

    result = aircraft.by_icao24("4CA1FB")

    assert route.calls.last.request.url.path == "/v3.1/aircraft/icao24/4CA1FB"
    assert route.calls.last.request.url.params["photos"] == "true"
    assert result.query == "4CA1FB"
    assert result.aircraft is not None
    assert result.aircraft.registration == "G-STBA"


def test_by_icao24_not_found(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    _mock(
        respx_mock,
        "/aircraft/icao24/ffffff",
        {"query": "FFFFFF", "found": False, "aircraft": None},
    )

    result = aircraft.by_icao24("ffffff")

    assert result.found is False
    assert result.aircraft is None


def test_lookup_sparse_row_and_unknown_fields(
    aircraft: Aircraft, respx_mock: respx.MockRouter
) -> None:
    """Registry rows are sparse; every detail scalar is optional."""

    _mock(
        respx_mock,
        "/aircraft/registration/N12345",
        {
            "query": "N12345",
            "found": True,
            "aircraft": {"registration": "N12345", "operator_country": "US"},
        },
    )

    result = aircraft.by_registration("N12345")

    details = result.aircraft
    assert details is not None
    assert details.registration == "N12345"
    assert details.icao24 is None
    assert details.year_built is None
    assert details.photos == []
    # extra="allow": backend additions never break a pinned SDK.
    assert details.model_extra is not None
    assert details.model_extra["operator_country"] == "US"


def test_lookup_request_options_are_forwarded(
    aircraft: Aircraft, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/aircraft/registration/G-STBA", load_fixture("aircraft_found"))

    aircraft.by_registration(
        "G-STBA", request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}}
    )

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


# ── performance ──────────────────────────────────────────────────────────────


def test_performance_happy_path(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/aircraft/performance/B77W", load_fixture("performance"))

    perf = aircraft.performance("B77W")

    assert route.calls.last.request.url.path == "/v3.1/aircraft/performance/B77W"
    assert isinstance(perf, AircraftPerformance)
    assert perf.icao_type == "B77W"
    assert perf.name == "BOEING 777-300ER"
    assert perf.engine_code == "L2J"
    assert perf.wake_category == "H"
    assert perf.cruise_speed_ktas == 490
    assert perf.max_range_nm == 7370
    # int stays int, float stays float.
    assert isinstance(perf.service_ceiling_ft, int)
    assert isinstance(perf.mtow_t, float)
    assert perf.max_passengers == 396


def test_performance_every_field_is_optional(
    aircraft: Aircraft, respx_mock: respx.MockRouter
) -> None:
    """Scraped reference data: thin rows for rare designators are normal."""

    _mock(respx_mock, "/aircraft/performance/BE20", {"icao_type": "BE20"})

    perf = aircraft.performance("BE20")

    assert perf.icao_type == "BE20"
    for field in (
        perf.name,
        perf.engine_type,
        perf.engine_code,
        perf.wake_category,
        perf.cruise_speed_ktas,
        perf.service_ceiling_ft,
        perf.max_range_nm,
        perf.wing_span_m,
        perf.length_m,
        perf.mtow_t,
        perf.max_passengers,
    ):
        assert field is None


def test_performance_404_raises(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    """Unlike the lookups, performance *does* 404 on an unknown type."""

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/aircraft/performance/ZZZZ").mock(
        return_value=httpx.Response(
            404, json={"detail": "No performance data found for aircraft type 'ZZZZ'."}
        )
    )

    with pytest.raises(NotFoundError) as excinfo:
        aircraft.performance("ZZZZ")

    assert excinfo.value.status_code == 404
    assert "ZZZZ" in str(excinfo.value)


# ── database stats ───────────────────────────────────────────────────────────


def test_database_stats(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    route = _mock(
        respx_mock,
        "/aircraft/database/stats",
        {
            "loaded": True,
            "total_icao_entries": 615656,
            "total_registration_entries": 613252,
            "source_url": "http://10.0.1.15:8090/aircraft.json",
        },
    )

    stats = aircraft.database_stats()

    assert route.calls.last.request.url.path == "/v3.1/aircraft/database/stats"
    assert isinstance(stats, AircraftDatabaseStats)
    assert stats.loaded is True
    assert stats.total_icao_entries == 615656
    assert stats.total_registration_entries == 613252
    assert stats.source_url is not None


def test_database_stats_not_loaded(aircraft: Aircraft, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/aircraft/database/stats", {"loaded": False})

    stats = aircraft.database_stats()

    assert stats.loaded is False
    assert stats.total_icao_entries == 0
    assert stats.source_url is None


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_lookup_found_and_missing(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/aircraft/registration/G-STBA", load_fixture("aircraft_found"))
    _mock(respx_mock, "/aircraft/registration/ZZ-ZZZ", load_fixture("aircraft_not_found"))

    aircraft = AsyncAircraft(async_client)

    found = await aircraft.by_registration("G-STBA")
    missing = await aircraft.by_registration("ZZ-ZZZ")

    assert found.aircraft is not None
    assert found.aircraft.year_built == "2010"
    assert missing.found is False
    assert missing.aircraft is None


async def test_async_performance(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/aircraft/performance/B77W", load_fixture("performance"))

    perf = await AsyncAircraft(async_client).performance("B77W")

    assert route.calls.last.request.url.path == "/v3.1/aircraft/performance/B77W"
    assert perf.wake_category == "H"
