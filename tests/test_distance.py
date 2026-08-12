"""``sky.distance`` — great-circle distance and bearing.

No recorded fixture: the payload below is the router's OpenAPI example
(``routers/v3/distance.py``) reproduced inline.

The namespace has a single method, so the client exposes it as the
``sky.distance(...)`` shortcut rather than as a ``sky.distance.calculate(...)``
namespace (that wiring is covered by ``test_exports.py``); these tests drive
``Distance``/``AsyncDistance`` directly to keep the endpoint contract isolated
from the shortcut.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import BadRequestError, NotFoundError
from skylink_api.models.distance import DistanceResponse
from skylink_api.resources.distance import AsyncDistance, Distance, _calculate_spec

PAYLOAD: dict[str, Any] = {
    "from_point": {
        "latitude": 40.639751,
        "longitude": -73.778925,
        "icao_code": "KJFK",
        "iata_code": "JFK",
        "name": "John F Kennedy International Airport",
    },
    "to_point": {
        "latitude": 51.4706,
        "longitude": -0.461941,
        "icao_code": "EGLL",
        "iata_code": "LHR",
        "name": "London Heathrow Airport",
    },
    "distance": 2991.01,
    "unit": "nm",
    "bearing": 51.35,
    "bearing_cardinal": "NE",
    "midpoint": {
        "latitude": 52.216674,
        "longitude": -41.302671,
        "icao_code": None,
        "iata_code": None,
        "name": None,
    },
}


def _mock(respx_mock: respx.MockRouter, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}/distance`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}/distance").mock(
        return_value=httpx.Response(200, json=payload)
    )


@pytest.fixture
def distance(client: SkyLink) -> Distance:
    return Distance(client)


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_produces_the_documented_spec() -> None:
    spec = _calculate_spec(from_icao="KJFK", to_icao="EGLL")

    # No trailing slash on this path (unlike /adsb/).
    assert (spec.method, spec.path) == ("GET", "/distance")
    assert spec.cast_to is DistanceResponse
    assert spec.query == {
        "from_icao": "KJFK",
        "to_icao": "EGLL",
        "from_lat": None,
        "from_lon": None,
        "to_lat": None,
        "to_lon": None,
        "unit": "nm",
    }

    mixed = _calculate_spec(from_lat=40.64, from_lon=-73.78, to_icao="EGLL", unit="km")
    assert mixed.query is not None
    assert mixed.query["from_lat"] == 40.64
    assert mixed.query["from_icao"] is None
    assert mixed.query["unit"] == "km"


# ── calculate ────────────────────────────────────────────────────────────────


def test_calculate_by_airport_codes(distance: Distance, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, PAYLOAD)

    result = distance.calculate(from_icao="KJFK", to_icao="EGLL")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/distance"
    assert request.url.params["from_icao"] == "KJFK"
    assert request.url.params["to_icao"] == "EGLL"
    assert request.url.params["unit"] == "nm"
    # Unset coordinate params are dropped, not sent empty.
    for dropped in ("from_lat", "from_lon", "to_lat", "to_lon"):
        assert dropped not in request.url.params
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, DistanceResponse)
    assert result.distance == 2991.01
    assert result.unit == "nm"
    assert result.bearing == 51.35
    assert result.bearing_cardinal == "NE"
    assert result.from_point is not None
    assert result.from_point.icao_code == "KJFK"
    assert result.from_point.iata_code == "JFK"
    assert result.to_point is not None
    assert result.to_point.name == "London Heathrow Airport"
    # The midpoint is bare coordinates — never resolved to an airport.
    assert result.midpoint is not None
    assert result.midpoint.latitude == 52.216674
    assert result.midpoint.icao_code is None


def test_calculate_mixes_coordinates_and_codes(
    distance: Distance, respx_mock: respx.MockRouter
) -> None:
    route = _mock(
        respx_mock,
        {
            **PAYLOAD,
            "unit": "km",
            "distance": 5539.4,
            "from_point": {
                "latitude": 40.64,
                "longitude": -73.78,
                "icao_code": None,
                "iata_code": None,
                "name": None,
            },
        },
    )

    result = distance.calculate(from_lat=40.64, from_lon=-73.78, to_icao="EGLL", unit="km")

    params = route.calls.last.request.url.params
    assert params["from_lat"] == "40.64"
    assert params["from_lon"] == "-73.78"
    assert params["to_icao"] == "EGLL"
    assert params["unit"] == "km"
    assert "from_icao" not in params

    assert result.unit == "km"
    assert result.from_point is not None
    # A point given as raw coordinates carries no identifiers.
    assert result.from_point.icao_code is None
    assert result.from_point.name is None


def test_calculate_all_four_coordinates(distance: Distance, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, PAYLOAD)

    distance.calculate(from_lat=40.64, from_lon=-73.78, to_lat=51.47, to_lon=-0.46, unit="mi")

    params = route.calls.last.request.url.params
    assert params["to_lat"] == "51.47"
    assert params["to_lon"] == "-0.46"
    assert params["unit"] == "mi"


def test_calculate_missing_endpoint_raises(
    distance: Distance, respx_mock: respx.MockRouter
) -> None:
    """Only ``from_lat`` given: the API rejects an incomplete point spec."""

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/distance").mock(
        return_value=httpx.Response(
            400, json={"detail": "Provide either to_icao or both to_lat and to_lon."}
        )
    )

    with pytest.raises(BadRequestError) as excinfo:
        distance.calculate(from_icao="KJFK")

    assert excinfo.value.status_code == 400


def test_calculate_unknown_airport_raises(distance: Distance, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/distance").mock(
        return_value=httpx.Response(404, json={"detail": "Airport not found: ZZZZ"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        distance.calculate(from_icao="ZZZZ", to_icao="EGLL")

    assert excinfo.value.status_code == 404


def test_calculate_unknown_fields_survive(distance: Distance, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, {**PAYLOAD, "route_type": "great_circle"})

    result = distance.calculate(from_icao="KJFK", to_icao="EGLL")

    assert result.model_extra is not None
    assert result.model_extra["route_type"] == "great_circle"


def test_calculate_request_options_are_forwarded(
    distance: Distance, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, PAYLOAD)

    distance.calculate(
        from_icao="KJFK",
        to_icao="EGLL",
        request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}},
    )

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_calculate(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, PAYLOAD)

    result = await AsyncDistance(async_client).calculate(from_icao="JFK", to_icao="LHR")

    params = route.calls.last.request.url.params
    assert params["from_icao"] == "JFK"
    assert params["unit"] == "nm"
    assert isinstance(result, DistanceResponse)
    assert result.bearing_cardinal == "NE"
