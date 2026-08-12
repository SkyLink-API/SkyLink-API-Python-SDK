"""``sky.ml`` — model-backed flight time prediction.

The namespace is not attached to the client yet (task A8 does the wiring), so the
resource is constructed directly against the client here.

The trap: the endpoint's query keys are the reserved words ``from`` and ``to``,
so the SDK's ``origin``/``destination`` arguments are renamed by the builder. The
payload has no recorded fixture upstream (no OpenAPI example block), so it is
built inline from the backend's ``FlightTimePrediction`` response model.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.ml import FlightTimePrediction
from skylink_api.resources.ml import AsyncMl, Ml, _flight_time_spec

PREDICTION: dict[str, Any] = {
    "origin": "KJFK",
    "destination": "EGLL",
    "aircraft_type": "B738",
    "distance_nm": 3007.4,
    "estimated_minutes": 443,
    "estimated_hours_display": "7h 23m",
    "min_minutes": 421,
    "max_minutes": 465,
    "model_version": "flight-time-v2.1",
}


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_renames_origin_and_destination_to_from_and_to() -> None:
    """``from`` is a Python keyword, so the rename lives in the builder."""

    spec = _flight_time_spec(origin="KJFK", destination="EGLL", aircraft="B738")

    assert spec.method == "GET"
    assert spec.path == "/ml/flight-time"
    assert spec.cast_to is FlightTimePrediction
    assert spec.query == {"from": "KJFK", "to": "EGLL", "aircraft": "B738"}
    # The Python-side names never reach the wire.
    assert "origin" not in spec.query
    assert "destination" not in spec.query


def test_builder_omits_aircraft_by_default() -> None:
    spec = _flight_time_spec(origin="JFK", destination="LHR")

    assert spec.query is not None
    assert spec.query["aircraft"] is None


# ── flight time ──────────────────────────────────────────────────────────────


def test_flight_time_sends_from_and_to_on_the_wire(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """The headline assertion: the query string really is ``from=...&to=...``."""

    route = _mock(respx_mock, "/ml/flight-time", PREDICTION)

    Ml(client).flight_time(origin="KJFK", destination="EGLL", aircraft="B738")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/ml/flight-time"
    assert request.url.params["from"] == "KJFK"
    assert request.url.params["to"] == "EGLL"
    assert request.url.params["aircraft"] == "B738"
    assert "origin" not in request.url.params
    assert "destination" not in request.url.params
    # And in the raw query string, not just the parsed mapping.
    assert "from=KJFK" in str(request.url)
    assert "to=EGLL" in str(request.url)


def test_flight_time_happy_path(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/ml/flight-time", PREDICTION)

    prediction = Ml(client).flight_time(origin="KJFK", destination="EGLL", aircraft="B738")

    assert isinstance(prediction, FlightTimePrediction)
    assert prediction.origin == "KJFK"
    assert prediction.destination == "EGLL"
    assert prediction.aircraft_type == "B738"
    assert prediction.distance_nm == 3007.4
    assert prediction.estimated_minutes == 443
    assert prediction.min_minutes == 421
    assert prediction.max_minutes == 465
    assert prediction.model_version == "flight-time-v2.1"
    # A display string, not a number — arithmetic goes through estimated_minutes.
    assert prediction.estimated_hours_display == "7h 23m"
    assert isinstance(prediction.estimated_hours_display, str)


def test_aircraft_is_the_only_optional_field(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    payload = {**PREDICTION, "aircraft_type": None}
    payload.pop("aircraft_type")
    route = _mock(respx_mock, "/ml/flight-time", payload)

    prediction = Ml(client).flight_time(origin="JFK", destination="LHR")

    assert "aircraft" not in route.calls.last.request.url.params
    assert prediction.aircraft_type is None
    # Everything else is guaranteed by the backend's response_model.
    assert prediction.estimated_minutes == 443


def test_missing_required_field_is_a_validation_error(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """Unlike the scraped endpoints, a missing key here means a broken contract."""

    from skylink_api._exceptions import APIResponseValidationError

    payload = {key: value for key, value in PREDICTION.items() if key != "estimated_minutes"}
    _mock(respx_mock, "/ml/flight-time", payload)

    with pytest.raises(APIResponseValidationError):
        Ml(client).flight_time(origin="KJFK", destination="EGLL")


def test_unknown_fields_survive(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/ml/flight-time", {**PREDICTION, "confidence": 0.82})

    prediction = Ml(client).flight_time(origin="KJFK", destination="EGLL")

    assert prediction.model_extra is not None
    assert prediction.model_extra["confidence"] == 0.82


def test_unknown_airport_raises(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/ml/flight-time").mock(
        return_value=httpx.Response(404, json={"detail": "Unknown airport: ZZZZ"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        Ml(client).flight_time(origin="ZZZZ", destination="EGLL")

    assert excinfo.value.status_code == 404


def test_request_options_are_forwarded(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/ml/flight-time", PREDICTION)

    Ml(client).flight_time(
        origin="KJFK",
        destination="EGLL",
        request_options={"headers": {"X-Trace": "abc"}},
    )

    assert route.calls.last.request.headers["X-Trace"] == "abc"


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_flight_time(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/ml/flight-time", PREDICTION)

    prediction = await AsyncMl(async_client).flight_time(origin="KJFK", destination="EGLL")

    params = route.calls.last.request.url.params
    assert params["from"] == "KJFK"
    assert params["to"] == "EGLL"
    assert prediction.estimated_hours_display == "7h 23m"
