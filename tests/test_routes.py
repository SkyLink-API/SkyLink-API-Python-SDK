"""``sky.routes`` — callsign resolution and route network data.

The namespace is not attached to the client yet (task A8 does the wiring), so
the resource classes are instantiated directly here.

Fixtures: ``routes_vrs.json`` and ``routes_airline.json`` — the two shapes
``GET /routes/callsign/{cs}`` alternates between. Both are covered, including
the fact that the airline variant carries **no ``callsign`` key**.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from pydantic import TypeAdapter, ValidationError

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import (
    APIResponseValidationError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from skylink_api.models.routes import (
    AirlineRoutesResult,
    AirportRoutesResponse,
    CallsignRoute,
    RoutePairsResponse,
    VrsRouteResult,
)
from skylink_api.resources.routes import (
    AsyncRoutes,
    Routes,
    _by_airport_spec,
    _by_callsign_spec,
    _pairs_spec,
)

AIRPORT_ROUTES_PAYLOAD: dict[str, Any] = {
    "code": "LHR",
    "direction": "both",
    "count": 2,
    "routes": [
        {
            "departure": "LHR",
            "arrival": "JFK",
            "airlines": ["British Airways", "American Airlines", "Virgin Atlantic"],
            "km": 5555,
            "duration_min": 445,
        },
        {
            "departure": "CDG",
            "arrival": "LHR",
            "airlines": ["Air France"],
            "km": 348,
            "duration_min": 80,
        },
    ],
}

PAIRS_PAYLOAD: dict[str, Any] = {
    "count": 1,
    "routes": [
        {
            "departure": "LHR",
            "arrival": "JFK",
            "airlines": ["British Airways", "American Airlines"],
            "km": 5555,
            "duration_min": 445,
        }
    ],
}


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    callsign_spec = _by_callsign_spec("BAW117")
    assert callsign_spec.method == "GET"
    assert callsign_spec.path == "/routes/callsign/BAW117"
    assert callsign_spec.query is None
    assert callsign_spec.cast_to is CallsignRoute

    airport_spec = _by_airport_spec("LHR")
    assert airport_spec.path == "/routes/airport/LHR"
    assert airport_spec.query == {"direction": "both", "limit": 100}
    assert airport_spec.cast_to is AirportRoutesResponse

    pairs_spec = _pairs_spec()
    assert pairs_spec.path == "/routes/pairs"
    assert pairs_spec.query == {"departure": None, "arrival": None, "limit": 50}
    assert pairs_spec.cast_to is RoutePairsResponse


# ── by_callsign: the discriminated union ─────────────────────────────────────


def test_by_callsign_vrs_variant(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/routes/callsign/BAW117", load_fixture("routes_vrs"))

    result = Routes(client).by_callsign("BAW117")

    assert route.calls.last.request.url.path == "/v3.1/routes/callsign/BAW117"

    assert isinstance(result, VrsRouteResult)
    assert result.source == "vrs"
    assert result.callsign == "BAW117"
    assert result.callsign_prefix == "BAW"
    assert result.airline_code == "BA"
    # The VRS variant speaks ICAO.
    assert result.departure_icao == "EGLL"
    assert result.arrival_icao == "KJFK"
    assert result.airports == ["EGLL", "KJFK"]
    assert result.confidence == "high"


def test_by_callsign_airline_fallback_variant(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """The fallback has a different shape and **no ``callsign`` key**."""

    payload = load_fixture("routes_airline")
    assert "callsign" not in payload  # guard: this is the whole point of the union
    _mock(respx_mock, "/routes/callsign/BAW999", payload)

    result = Routes(client).by_callsign("BAW999")

    assert isinstance(result, AirlineRoutesResult)
    assert result.source == "airline_routes"
    assert not hasattr(result, "callsign")
    assert result.callsign_prefix == "BAW"
    assert result.airline_name == "British Airways"
    assert result.confidence == "low"
    # routes[] is truncated to 20 by the API; total_routes is the real figure.
    assert result.total_routes == 412
    assert len(result.routes) == 3
    # The fallback variant speaks IATA, not ICAO.
    assert result.routes[0].src == "LHR"
    assert result.routes[0].dst == "JFK"
    assert result.routes[0].km == 5555
    assert result.routes[0].duration_min == 445


def test_by_callsign_narrows_on_the_source_discriminant(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """The documented usage pattern: branch on ``source``, then read fields."""

    for fixture_name, expected in (("routes_vrs", "vrs"), ("routes_airline", "airline_routes")):
        respx_mock.reset()
        _mock(respx_mock, "/routes/callsign/BAW117", load_fixture(fixture_name))

        result = Routes(client).by_callsign("BAW117")

        assert result.source == expected
        if result.source == "vrs":
            assert result.departure_icao is not None
        else:
            assert result.total_routes is not None


def test_union_is_resolved_by_source_not_by_field_order() -> None:
    """A VRS-shaped body tagged ``airline_routes`` parses as the airline model."""

    adapter: TypeAdapter[Any] = TypeAdapter(CallsignRoute)

    mislabelled = {**load_fixture("routes_vrs"), "source": "airline_routes"}
    parsed = adapter.validate_python(mislabelled)

    assert isinstance(parsed, AirlineRoutesResult)
    # The VRS-only keys survive as extras rather than being dropped.
    assert parsed.model_extra is not None
    assert parsed.model_extra["departure_icao"] == "EGLL"


def test_unknown_source_value_is_rejected() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(CallsignRoute)

    with pytest.raises(ValidationError):
        adapter.validate_python({**load_fixture("routes_vrs"), "source": "opensky"})


def test_missing_source_key_surfaces_as_a_validation_error(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """No discriminator → the SDK cannot pick a variant and says so loudly."""

    payload = dict(load_fixture("routes_vrs"))
    payload.pop("source")
    _mock(respx_mock, "/routes/callsign/BAW117", payload)

    with pytest.raises(APIResponseValidationError):
        Routes(client).by_callsign("BAW117")


def test_by_callsign_unknown_prefix_raises_404(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/routes/callsign/XXX1").mock(
        return_value=httpx.Response(
            404, json={"detail": "No routes found for callsign prefix in 'XXX1'."}
        )
    )

    with pytest.raises(NotFoundError) as excinfo:
        Routes(client).by_callsign("XXX1")

    assert excinfo.value.status_code == 404


def test_by_callsign_dataset_loading_raises_503(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/routes/callsign/BAW117").mock(
        return_value=httpx.Response(503, json={"detail": "Route data not yet loaded."})
    )

    with pytest.raises(ServiceUnavailableError):
        Routes(client).by_callsign("BAW117", request_options={"max_retries": 0})


# ── by_airport ───────────────────────────────────────────────────────────────


def test_by_airport_defaults(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/routes/airport/LHR", AIRPORT_ROUTES_PAYLOAD)

    result = Routes(client).by_airport("LHR")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/routes/airport/LHR"
    assert request.url.params["direction"] == "both"
    assert request.url.params["limit"] == "100"

    assert isinstance(result, AirportRoutesResponse)
    assert result.code == "LHR"
    assert result.direction == "both"
    assert result.count == 2
    first = result.routes[0]
    assert first.departure == "LHR"
    assert first.arrival == "JFK"
    # airlines[] holds full names, not codes.
    assert first.airlines == ["British Airways", "American Airlines", "Virgin Atlantic"]
    assert first.km == 5555
    assert first.duration_min == 445


def test_by_airport_direction_and_limit(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(
        respx_mock,
        "/routes/airport/EGLL",
        {**AIRPORT_ROUTES_PAYLOAD, "code": "EGLL", "direction": "dep"},
    )

    result = Routes(client).by_airport("EGLL", direction="dep", limit=5)

    params = route.calls.last.request.url.params
    assert params["direction"] == "dep"
    assert params["limit"] == "5"
    # An ICAO input is echoed as sent even though the rows carry IATA codes.
    assert result.code == "EGLL"
    assert result.routes[0].departure == "LHR"


def test_by_airport_empty_result(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/routes/airport/ZZZ", {"code": "ZZZ", "direction": "both", "count": 0})

    result = Routes(client).by_airport("ZZZ")

    assert result.count == 0
    assert result.routes == []


def test_by_airport_bad_direction_raises_422(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    """``direction`` is a ``Literal`` at type-check time; the API enforces it at runtime."""

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/routes/airport/LHR").mock(
        return_value=httpx.Response(
            422, json={"detail": "direction must be 'dep', 'arr', or 'both'"}
        )
    )

    with pytest.raises(UnprocessableEntityError):
        Routes(client).by_airport("LHR", request_options={"query": {"direction": "sideways"}})


# ── pairs ────────────────────────────────────────────────────────────────────


def test_pairs_with_both_filters(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/routes/pairs", PAIRS_PAYLOAD)

    result = Routes(client).pairs(departure="LHR", arrival="JFK", limit=10)

    request = route.calls.last.request
    assert request.url.path == "/v3.1/routes/pairs"
    assert request.url.params["departure"] == "LHR"
    assert request.url.params["arrival"] == "JFK"
    assert request.url.params["limit"] == "10"

    assert isinstance(result, RoutePairsResponse)
    assert result.count == 1
    assert result.routes[0].airlines == ["British Airways", "American Airlines"]


def test_pairs_omits_unset_filters(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/routes/pairs", PAIRS_PAYLOAD)

    Routes(client).pairs(arrival="JFK")

    params = route.calls.last.request.url.params
    assert "departure" not in params
    assert params["arrival"] == "JFK"
    assert params["limit"] == "50"


def test_pairs_keeps_unknown_fields(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    payload = {
        "count": 1,
        "routes": [{"departure": "LHR", "arrival": "JFK", "seats_per_week": 12000}],
        "generated_at": "2026-08-12T00:00:00Z",
    }
    _mock(respx_mock, "/routes/pairs", payload)

    result = Routes(client).pairs(departure="LHR")

    assert result.model_extra is not None
    assert result.model_extra["generated_at"] == "2026-08-12T00:00:00Z"
    assert result.routes[0].model_extra is not None
    assert result.routes[0].model_extra["seats_per_week"] == 12000
    # Missing collections fall back to empty lists.
    assert result.routes[0].airlines == []
    assert result.routes[0].km is None


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_by_callsign_both_variants(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/routes/callsign/BAW117", load_fixture("routes_vrs"))
    vrs = await AsyncRoutes(async_client).by_callsign("BAW117")
    assert isinstance(vrs, VrsRouteResult)
    assert vrs.arrival_icao == "KJFK"

    respx_mock.reset()
    _mock(respx_mock, "/routes/callsign/BAW999", load_fixture("routes_airline"))
    airline = await AsyncRoutes(async_client).by_callsign("BAW999")
    assert isinstance(airline, AirlineRoutesResult)
    assert airline.total_routes == 412


async def test_async_by_airport_and_pairs(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    airport_route = _mock(respx_mock, "/routes/airport/LHR", AIRPORT_ROUTES_PAYLOAD)
    pairs_route = _mock(respx_mock, "/routes/pairs", PAIRS_PAYLOAD)

    by_airport = await AsyncRoutes(async_client).by_airport("LHR", direction="arr", limit=3)
    pairs = await AsyncRoutes(async_client).pairs(departure="LHR", limit=3)

    assert airport_route.calls.last.request.url.params["direction"] == "arr"
    assert pairs_route.calls.last.request.url.params["limit"] == "3"
    assert isinstance(by_airport, AirportRoutesResponse)
    assert isinstance(pairs, RoutePairsResponse)
