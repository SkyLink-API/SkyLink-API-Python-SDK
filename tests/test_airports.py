"""``sky.airports`` — enriched lookup plus the location/IP/text searches.

``airports_search.json`` is a recorded fixture (verbatim from the router's
OpenAPI example, hence the string-typed ``frequency_mhz``/``lighted``); the
``country``/``region``/``search_*`` keys the service adds on top are layered in
by the tests that exercise them. The three search envelopes are built inline
from the router example blocks documented in research/01.

The namespace is not attached to the client yet (task A8 does the wiring), so
the resource classes are instantiated directly here.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.airports import (
    AirportsByIPResponse,
    AirportsByLocationResponse,
    AirportsTextSearchResponse,
    EnrichedAirport,
)
from skylink_api.resources.airports import (
    Airports,
    AsyncAirports,
    _by_ip_spec,
    _nearby_spec,
    _search_spec,
    _search_text_spec,
)


@pytest.fixture
def airports(client: SkyLink) -> Airports:
    return Airports(client)


@pytest.fixture
def async_airports(async_client: AsyncSkyLink) -> AsyncAirports:
    return AsyncAirports(async_client)


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    assert _search_spec(icao="KJFK").path == "/airports/search"
    assert _search_spec(icao="KJFK").query == {"icao": "KJFK", "iata": None}
    assert _search_spec(iata="JFK").query == {"icao": None, "iata": "JFK"}
    assert _search_spec(icao="KJFK").cast_to is EnrichedAirport

    assert _nearby_spec(lat=40.64, lon=-73.78).path == "/airports/search/location"
    assert _nearby_spec(lat=40.64, lon=-73.78).query == {
        "lat": 40.64,
        "lon": -73.78,
        "radius": 50,
        "type": None,
        "limit": 50,
    }
    assert _by_ip_spec().path == "/airports/search/ip"
    assert _by_ip_spec().query == {"ip": None, "radius": 100, "type": None, "limit": 50}
    assert _search_text_spec(q="London").path == "/airports/search/text"
    assert _search_text_spec(q="London").query == {"q": "London", "limit": 20, "type": None}

    assert {
        spec.method
        for spec in (
            _search_spec(icao="KJFK"),
            _nearby_spec(lat=0, lon=0),
            _by_ip_spec(),
            _search_text_spec(q="x"),
        )
    } == {"GET"}


# ── enriched search ──────────────────────────────────────────────────────────


def test_search_rejects_zero_or_two_codes_before_any_request(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    """Exactly one of icao/iata — the API 400s either way, so we never send."""

    route = _mock(respx_mock, "/airports/search", load_fixture("airports_search"))

    with pytest.raises(ValueError, match="got neither"):
        airports.search()
    with pytest.raises(ValueError, match="got both"):
        airports.search(icao="KJFK", iata="JFK")

    assert route.call_count == 0


def test_search_by_icao_keeps_stringly_typed_numbers(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/airports/search", load_fixture("airports_search"))

    airport = airports.search(icao="KJFK")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/airports/search"
    assert request.url.params["icao"] == "KJFK"
    # None-valued query params are dropped, never sent empty.
    assert "iata" not in request.url.params
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(airport, EnrichedAirport)
    assert airport.ident == "KJFK"
    assert airport.iata_code == "JFK"
    assert airport.type == "large_airport"
    # "yes"/"no", not a bool.
    assert airport.scheduled_service == "yes"

    runway = airport.runways[0]
    assert runway.le_ident == "04L"
    assert runway.length_ft == 14511
    # Stringly typed booleans stay strings — no silent coercion.
    assert runway.lighted == "1"
    assert isinstance(runway.lighted, str)
    assert runway.closed == "0"

    frequency = airport.frequencies[0]
    assert frequency.type == "TWR"
    assert frequency.frequency_mhz == "119.1"
    assert isinstance(frequency.frequency_mhz, str)
    assert float(frequency.frequency_mhz) == 119.1

    navaid = airport.navaids[0]
    assert navaid.ident == "JFK"
    assert navaid.type == "VOR-DME"
    assert navaid.frequency_khz == "115900"
    assert isinstance(navaid.frequency_khz, str)


def test_search_by_iata_with_country_region_and_search_echo(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    """The service adds country{}/region{}/search_* on top of the CSV row."""

    payload = {
        **load_fixture("airports_search"),
        "home_link": "https://www.jfkairport.com/",
        "wikipedia_link": "https://en.wikipedia.org/wiki/John_F._Kennedy_International_Airport",
        "keywords": "New York, JFK, international",
        "country": {
            "id": 302791,
            "code": "US",
            "name": "United States",
            "continent": "NA",
            "wikipedia_link": "https://en.wikipedia.org/wiki/United_States",
            "keywords": None,
        },
        "region": {
            "id": 306091,
            "code": "US-NY",
            "local_code": "NY",
            "name": "New York",
            "continent": "NA",
            "iso_country": "US",
            "wikipedia_link": "https://en.wikipedia.org/wiki/New_York_(state)",
            "keywords": None,
        },
        "search_code": "JFK",
        "search_type": "IATA",
    }
    route = _mock(respx_mock, "/airports/search", payload)

    airport = airports.search(iata="JFK")

    params = route.calls.last.request.url.params
    assert params["iata"] == "JFK"
    assert "icao" not in params

    assert airport.country is not None
    assert airport.country.code == "US"
    assert airport.country.continent == "NA"
    assert airport.region is not None
    assert airport.region.code == "US-NY"
    assert airport.region.local_code == "NY"
    assert airport.region.iso_country == "US"
    # search_type is ICAO|IATA on the wire but stays a plain str on the model.
    assert airport.search_type == "IATA"
    assert airport.search_code == "JFK"


def test_search_survives_numeric_variants_and_unknown_fields(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    """Live rows send numbers where the documented example sends strings."""

    payload = {
        "id": 3682,
        "ident": "KJFK",
        "type": "large_airport",
        "name": "John F Kennedy International Airport",
        "icao_code": "KJFK",
        "runways": [
            {
                "id": 240441,
                "airport_ref": 3682,
                "airport_ident": "KJFK",
                "length_ft": 14511,
                "width_ft": 150,
                "surface": "ASP",
                "lighted": 1,
                "closed": 0,
                "le_ident": "04L",
                "le_heading_degT": 42.6,
                "he_ident": "22R",
                "he_heading_degT": 222.6,
            }
        ],
        "frequencies": [{"id": 1, "type": "TWR", "frequency_mhz": 119.1}],
        "navaids": [{"ident": "JFK", "frequency_khz": 115900, "usageType": "BOTH"}],
        "country": None,
        "region": None,
        "brand_new_field": {"nested": 1},
    }
    _mock(respx_mock, "/airports/search", payload)

    airport = airports.search(icao="KJFK")

    runway = airport.runways[0]
    assert runway.lighted == 1
    assert isinstance(runway.lighted, int)
    # degT is the wire spelling; the attribute is snake_case.
    assert runway.le_heading_deg_t == 42.6
    assert runway.he_heading_deg_t == 222.6
    assert airport.frequencies[0].frequency_mhz == 119.1
    assert airport.navaids[0].frequency_khz == 115900
    # usageType — the only camelCase key in the API.
    assert airport.navaids[0].usage_type == "BOTH"
    assert airport.country is None
    assert airport.region is None
    # extra="allow": backend additions never break a pinned SDK.
    assert airport.model_extra is not None
    assert airport.model_extra["brand_new_field"] == {"nested": 1}
    # Missing collections fall back to empty lists.
    assert airport.model_dump()["ident"] == "KJFK"


def test_search_404_raises(airports: Airports, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/airports/search").mock(
        return_value=httpx.Response(404, json={"detail": "Airport not found for ICAO code: ZZZZ"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        airports.search(icao="ZZZZ")

    assert excinfo.value.status_code == 404
    assert "ZZZZ" in str(excinfo.value)


def test_search_request_options_are_forwarded(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/airports/search", load_fixture("airports_search"))

    airports.search(icao="KJFK", request_options={"headers": {"X-Trace": "abc"}})

    assert route.calls.last.request.headers["X-Trace"] == "abc"


# ── nearby (location) ────────────────────────────────────────────────────────

LOCATION_PAYLOAD: dict[str, Any] = {
    "search_location": {
        "latitude": 40.64,
        "longitude": -73.78,
        "radius_km": 50,
        "type_filter": None,
    },
    "airports": [
        {
            "id": 3682,
            "ident": "KJFK",
            "type": "large_airport",
            "name": "John F Kennedy International Airport",
            "latitude_deg": 40.6398,
            "longitude_deg": -73.7789,
            "elevation_ft": 13,
            "municipality": "New York",
            "iso_country": "US",
            "iso_region": "US-NY",
            "iata_code": "JFK",
            "distance_km": 0.15,
        },
        {
            "id": 3697,
            "ident": "KLGA",
            "type": "large_airport",
            "name": "La Guardia Airport",
            "latitude_deg": 40.7772,
            "longitude_deg": -73.8726,
            "elevation_ft": 20,
            "municipality": "New York",
            "iso_country": "US",
            "iso_region": "US-NY",
            "iata_code": "LGA",
            "distance_km": 16.24,
        },
    ],
    "airports_found": 2,
}


def test_nearby_defaults(airports: Airports, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/airports/search/location", LOCATION_PAYLOAD)

    result = airports.nearby(lat=40.64, lon=-73.78)

    request = route.calls.last.request
    assert request.url.path == "/v3.1/airports/search/location"
    assert request.url.params["lat"] == "40.64"
    assert request.url.params["lon"] == "-73.78"
    assert request.url.params["radius"] == "50"
    assert request.url.params["limit"] == "50"
    assert "type" not in request.url.params

    assert isinstance(result, AirportsByLocationResponse)
    assert result.airports_found == 2
    assert result.search_location is not None
    assert result.search_location.radius_km == 50
    assert result.search_location.type_filter is None
    # Sorted by distance, nearest first.
    assert [a.ident for a in result.airports] == ["KJFK", "KLGA"]
    assert result.airports[0].distance_km == 0.15


def test_nearby_with_type_and_limit(airports: Airports, respx_mock: respx.MockRouter) -> None:
    payload = {
        **LOCATION_PAYLOAD,
        "search_location": {**LOCATION_PAYLOAD["search_location"], "type_filter": "large_airport"},
    }
    route = _mock(respx_mock, "/airports/search/location", payload)

    result = airports.nearby(lat=40.64, lon=-73.78, radius=120.5, type="large_airport", limit=5)

    params = route.calls.last.request.url.params
    assert params["radius"] == "120.5"
    assert params["type"] == "large_airport"
    assert params["limit"] == "5"
    assert result.search_location is not None
    assert result.search_location.type_filter == "large_airport"


def test_nearby_empty_area_is_a_normal_200(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    _mock(
        respx_mock,
        "/airports/search/location",
        {
            "search_location": {
                "latitude": 0.0,
                "longitude": 0.0,
                "radius_km": 50,
                "type_filter": None,
            },
            "airports": [],
            "airports_found": 0,
        },
    )

    result = airports.nearby(lat=0.0, lon=0.0)

    assert result.airports == []
    assert result.airports_found == 0


# ── by_ip ────────────────────────────────────────────────────────────────────

IP_PAYLOAD: dict[str, Any] = {
    "ip_address": "8.8.8.8",
    "location": {
        "latitude": 37.751,
        "longitude": -97.822,
        "city": "Wichita",
        "region": "Kansas",
        "country": "United States",
        "country_code": "US",
        "postal": "67202",
        "timezone": "America/Chicago",
        "ip": "8.8.8.8",
    },
    "airports": [],
    "search_radius_km": 100,
    "airports_found": 0,
    "error": None,
}


def test_by_ip_explicit_address(airports: Airports, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/airports/search/ip", IP_PAYLOAD)

    result = airports.by_ip(ip="8.8.8.8", radius=250, limit=10)

    request = route.calls.last.request
    assert request.url.path == "/v3.1/airports/search/ip"
    assert request.url.params["ip"] == "8.8.8.8"
    assert request.url.params["radius"] == "250"
    assert request.url.params["limit"] == "10"

    assert isinstance(result, AirportsByIPResponse)
    assert result.error is None
    assert result.location is not None
    assert result.location.city == "Wichita"
    # `region` here is a region *name*, not an ISO code.
    assert result.location.region == "Kansas"
    assert result.location.timezone == "America/Chicago"
    assert result.search_radius_km == 100


def test_by_ip_without_address_uses_the_callers_ip(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/airports/search/ip", IP_PAYLOAD)

    airports.by_ip()

    params = route.calls.last.request.url.params
    assert "ip" not in params
    assert params["radius"] == "100"


def test_by_ip_geolocation_failure_is_a_200_not_an_exception(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    """A geolocation failure ships inside the body — ``error`` is part of the model."""

    _mock(
        respx_mock,
        "/airports/search/ip",
        {
            "ip_address": "127.0.0.1",
            "location": None,
            "airports": [],
            "search_radius_km": 100,
            "airports_found": 0,
            "error": "Private IP address cannot be geolocated",
        },
    )

    result = airports.by_ip(ip="127.0.0.1")

    assert isinstance(result, AirportsByIPResponse)
    assert result.error == "Private IP address cannot be geolocated"
    assert result.location is None
    assert result.airports == []
    assert result.airports_found == 0


# ── text search ──────────────────────────────────────────────────────────────

TEXT_PAYLOAD: dict[str, Any] = {
    "query": "London",
    "airports": [
        {
            "id": None,
            "ident": "EGLL",
            "type": "large_airport",
            "name": "London Heathrow Airport",
            "latitude_deg": 51.4706,
            "longitude_deg": -0.4619,
            "municipality": "London",
            "iso_country": "GB",
            "iata_code": "LHR",
            "relevance_score": 80,
        },
        {
            "id": None,
            "ident": "EGKK",
            "type": "large_airport",
            "name": "London Gatwick Airport",
            "latitude_deg": 51.1481,
            "longitude_deg": -0.1903,
            "municipality": "London",
            "iso_country": "GB",
            "iata_code": "LGW",
            "relevance_score": 75,
        },
    ],
    "airports_found": 2,
}


def test_search_text(airports: Airports, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/airports/search/text", TEXT_PAYLOAD)

    result = airports.search_text(q="London", limit=50, type="large_airport")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/airports/search/text"
    assert request.url.params["q"] == "London"
    assert request.url.params["limit"] == "50"
    assert request.url.params["type"] == "large_airport"

    assert isinstance(result, AirportsTextSearchResponse)
    assert result.query == "London"
    assert [a.relevance_score for a in result.airports] == [80, 75]
    # The text index does not carry a row id — it is legitimately null.
    assert result.airports[0].id is None
    assert result.airports[0].iata_code == "LHR"
    # Slim variant: no elevation/iso_region on this route.
    assert result.airports[0].elevation_ft is None


def test_search_text_defaults_and_no_match(
    airports: Airports, respx_mock: respx.MockRouter
) -> None:
    route = _mock(
        respx_mock,
        "/airports/search/text",
        {"query": "zzzz", "airports": [], "airports_found": 0},
    )

    result = airports.search_text(q="zzzz")

    params = route.calls.last.request.url.params
    assert params["limit"] == "20"
    assert "type" not in params
    assert result.airports == []
    assert result.airports_found == 0


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_search(async_airports: AsyncAirports, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/airports/search", load_fixture("airports_search"))

    airport = await async_airports.search(icao="KJFK")

    assert route.calls.last.request.url.params["icao"] == "KJFK"
    assert isinstance(airport, EnrichedAirport)
    assert airport.ident == "KJFK"
    assert airport.frequencies[0].frequency_mhz == "119.1"


async def test_async_search_validates_before_sending(
    async_airports: AsyncAirports, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/airports/search", load_fixture("airports_search"))

    with pytest.raises(ValueError, match="got neither"):
        await async_airports.search()

    assert route.call_count == 0


async def test_async_by_ip_error_sentinel(
    async_airports: AsyncAirports, respx_mock: respx.MockRouter
) -> None:
    _mock(
        respx_mock,
        "/airports/search/ip",
        {**IP_PAYLOAD, "location": None, "error": "lookup failed"},
    )

    result = await async_airports.by_ip(ip="10.0.0.1")

    assert result.error == "lookup failed"
    assert result.location is None
