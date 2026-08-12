"""``sky.carbon`` — CO₂ estimates.

The namespace is not attached to the client yet (task A8 does the wiring), so
the resource classes are instantiated directly here.

Fixture: ``carbon.json`` — a callsign-supplied, ``include_rfi=false`` response,
i.e. the variant where the three conditional keys *are* present and the two
``co2_equivalent_*`` keys are present but null.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError, UnprocessableEntityError
from skylink_api.models.carbon import CarbonEstimate
from skylink_api.resources.carbon import AsyncCarbon, Carbon, _estimate_spec

PATH = "/carbon/estimate"


def _mock(respx_mock: respx.MockRouter, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}/carbon/estimate`` (query independent)."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{PATH}").mock(
        return_value=httpx.Response(200, json=payload)
    )


def _airport_payload() -> dict[str, Any]:
    """``carbon.json`` minus the three callsign-only keys — the airport variant."""

    payload = dict(load_fixture("carbon"))
    for key in ("callsign", "callsign_resolved", "route_confidence"):
        payload.pop(key)
    return payload


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_produces_the_documented_spec() -> None:
    spec = _estimate_spec(departure_icao="EGLL", arrival_icao="KJFK")

    assert spec.method == "GET"
    assert spec.path == PATH
    assert spec.cast_to is CarbonEstimate
    assert spec.query == {
        "departure_icao": "EGLL",
        "arrival_icao": "KJFK",
        "callsign": None,
        "aircraft_type": None,
        "passengers": None,
        "include_rfi": False,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"departure_icao": "EGLL"},
        {"arrival_icao": "KJFK"},
        {"aircraft_type": "B77W", "passengers": 200},
    ],
)
def test_builder_requires_callsign_or_both_airports(kwargs: dict[str, Any]) -> None:
    """Client-side precondition: the API would answer 422, so never send it."""

    with pytest.raises(ValueError, match="callsign"):
        _estimate_spec(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"callsign": "BAW117"},
        {"departure_icao": "EGLL", "arrival_icao": "KJFK"},
        {"callsign": "BAW117", "departure_icao": "EGLL"},
    ],
)
def test_builder_accepts_every_valid_combination(kwargs: dict[str, Any]) -> None:
    assert _estimate_spec(**kwargs).path == PATH


def test_precondition_fires_before_any_request(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, load_fixture("carbon"))

    with pytest.raises(ValueError):
        Carbon(client).estimate(departure_icao="EGLL")

    assert route.call_count == 0


# ── by airports ──────────────────────────────────────────────────────────────


def test_estimate_by_airports(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, _airport_payload())

    estimate = Carbon(client).estimate(departure_icao="EGLL", arrival_icao="KJFK")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/carbon/estimate"
    assert request.url.params["departure_icao"] == "EGLL"
    assert request.url.params["arrival_icao"] == "KJFK"
    assert request.url.params["include_rfi"] == "false"
    # None-valued optionals are dropped, never sent as empty strings.
    assert "callsign" not in request.url.params
    assert "aircraft_type" not in request.url.params
    assert "passengers" not in request.url.params

    assert isinstance(estimate, CarbonEstimate)
    assert estimate.departure_icao == "EGLL"
    assert estimate.arrival_icao == "KJFK"
    assert estimate.aircraft_category == "widebody"
    assert estimate.co2_kg_total == 205538.7
    assert estimate.passengers == 396
    assert estimate.passengers_source == "category_default"
    assert estimate.methodology == "ICAO-Doc9988"
    assert estimate.distance_source == "haversine"


def test_conditional_keys_are_absent_without_a_callsign(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """``callsign``/``callsign_resolved``/``route_confidence`` are *omitted*, not null."""

    payload = _airport_payload()
    assert "callsign" not in payload  # guard: the fixture edit did happen
    _mock(respx_mock, payload)

    estimate = Carbon(client).estimate(departure_icao="EGLL", arrival_icao="KJFK")

    # Attribute access still works — they are plain optionals.
    assert estimate.callsign is None
    assert estimate.callsign_resolved is None
    assert estimate.route_confidence is None
    # ...and pydantic can tell "absent" from "present but null".
    assert "callsign" not in estimate.model_fields_set
    assert "callsign_resolved" not in estimate.model_fields_set
    assert "route_confidence" not in estimate.model_fields_set
    assert "co2_equivalent_kg_total" in estimate.model_fields_set


# ── by callsign ──────────────────────────────────────────────────────────────


def test_estimate_by_callsign_adds_the_conditional_keys(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, load_fixture("carbon"))

    estimate = Carbon(client).estimate(callsign="BAW117")

    assert route.calls.last.request.url.params["callsign"] == "BAW117"
    assert "departure_icao" not in route.calls.last.request.url.params

    assert estimate.callsign == "BAW117"
    assert estimate.callsign_resolved is True
    assert estimate.route_confidence == "high"
    assert "callsign" in estimate.model_fields_set
    # The route was resolved even though the request named no airports.
    assert estimate.departure_icao == "EGLL"


def test_estimate_callsign_present_but_route_confidence_absent(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """``route_confidence`` is dropped when the VRS lookup was skipped."""

    payload = dict(load_fixture("carbon"))
    payload.pop("route_confidence")
    payload["callsign_resolved"] = False
    _mock(respx_mock, payload)

    estimate = Carbon(client).estimate(
        callsign="BAW117", departure_icao="EGLL", arrival_icao="KJFK"
    )

    assert "callsign" in estimate.model_fields_set
    assert estimate.callsign_resolved is False
    assert "route_confidence" not in estimate.model_fields_set


# ── RFI ──────────────────────────────────────────────────────────────────────


def test_co2_equivalent_is_null_without_include_rfi(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, load_fixture("carbon"))

    estimate = Carbon(client).estimate(callsign="BAW117")

    assert estimate.rfi_applied is False
    # rfi_factor is reported regardless — it is not a flag.
    assert estimate.rfi_factor == 1.9
    assert estimate.co2_equivalent_kg_total is None
    assert estimate.co2_equivalent_kg_per_passenger is None


def test_include_rfi_true(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    payload = {
        **load_fixture("carbon"),
        "rfi_applied": True,
        "co2_equivalent_kg_total": 390523.5,
        "co2_equivalent_kg_per_passenger": 986.1,
        "notes": "RFI (Radiative Forcing Index) accounts for non-CO2 climate effects.",
    }
    route = _mock(respx_mock, payload)

    estimate = Carbon(client).estimate(callsign="BAW117", include_rfi=True)

    assert route.calls.last.request.url.params["include_rfi"] == "true"
    assert estimate.rfi_applied is True
    assert estimate.co2_equivalent_kg_total == 390523.5
    assert estimate.co2_equivalent_kg_per_passenger == 986.1


# ── misc ─────────────────────────────────────────────────────────────────────


def test_all_query_parameters_are_forwarded(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, load_fixture("carbon"))

    Carbon(client).estimate(
        departure_icao="EGLL",
        arrival_icao="KJFK",
        callsign="BAW117",
        aircraft_type="B77W",
        passengers=200,
        include_rfi=True,
        request_options={"headers": {"X-Trace": "abc"}},
    )

    request = route.calls.last.request
    assert request.url.params["aircraft_type"] == "B77W"
    assert request.url.params["passengers"] == "200"
    assert request.headers["X-Trace"] == "abc"


def test_unknown_fields_and_integer_widths_survive(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    payload = {**load_fixture("carbon"), "co2_kg_total": 205538, "sustainable_fuel_pct": 3}
    _mock(respx_mock, payload)

    estimate = Carbon(client).estimate(callsign="BAW117")

    assert estimate.model_extra is not None
    assert estimate.model_extra["sustainable_fuel_pct"] == 3
    # Number = int | float — a whole number is not widened to float.
    assert isinstance(estimate.co2_kg_total, int)


def test_unknown_airport_raises_404(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}{PATH}").mock(
        return_value=httpx.Response(404, json={"detail": "Departure airport not found: ZZZZ"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        Carbon(client).estimate(departure_icao="ZZZZ", arrival_icao="KJFK")

    assert excinfo.value.status_code == 404


def test_identical_airports_raise_422(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    """The API — not the SDK — rejects a same-airport pair."""

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}{PATH}").mock(
        return_value=httpx.Response(
            422, json={"detail": "Departure and arrival airports must be different"}
        )
    )

    with pytest.raises(UnprocessableEntityError):
        Carbon(client).estimate(departure_icao="EGLL", arrival_icao="EGLL")


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_estimate(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, load_fixture("carbon"))

    estimate = await AsyncCarbon(async_client).estimate(callsign="BAW117", passengers=200)

    assert route.calls.last.request.url.params["passengers"] == "200"
    assert isinstance(estimate, CarbonEstimate)
    assert estimate.callsign == "BAW117"


async def test_async_precondition_matches_sync(async_client: AsyncSkyLink) -> None:
    with pytest.raises(ValueError, match="callsign"):
        await AsyncCarbon(async_client).estimate(arrival_icao="KJFK")
