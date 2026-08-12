"""``sky.flight_status`` — live flight status.

The namespace is not attached to the client yet (task A8 does the wiring), so the
resource is constructed directly against the client here.

The payload is scraped and full of traps; each of them gets a test:
asymmetric departure/arrival objects, empty strings instead of ``null``, empty
``{}`` legs, and year-less local dates.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.flight_status import (
    FlightStatusArrival,
    FlightStatusDeparture,
    FlightStatusResponse,
)
from skylink_api.resources.flight_status import (
    AsyncFlightStatus,
    FlightStatus,
    _flight_status_spec,
)


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_produces_the_documented_spec() -> None:
    spec = _flight_status_spec("BA123")

    assert spec.method == "GET"
    assert spec.path == "/flight_status/BA123"
    assert spec.cast_to is FlightStatusResponse
    # No query parameters at all on this endpoint.
    assert spec.query is None

    # ICAO form goes through untouched — the API converts it server-side.
    assert _flight_status_spec("BAW123").path == "/flight_status/BAW123"


# ── happy path ───────────────────────────────────────────────────────────────


def test_get_happy_path(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/flight_status/BA123", load_fixture("flight_status"))

    status = FlightStatus(client).get("BA123")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/flight_status/BA123"
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(status, FlightStatusResponse)
    assert status.flight_number == "BA 123"
    assert status.airline == "British Airways"
    # Status is prose from the source, not an enum.
    assert status.status == "En Route"


def test_departure_and_arrival_are_different_classes(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """The two legs share four fields and differ in three — hence two models."""

    _mock(respx_mock, "/flight_status/BA123", load_fixture("flight_status"))

    status = FlightStatus(client).get("BA123")

    assert isinstance(status.departure, FlightStatusDeparture)
    assert isinstance(status.arrival, FlightStatusArrival)

    # Departure half: actual_* + checkin, and no estimated_*/baggage attribute.
    assert status.departure.actual_time == "10:35"
    assert status.departure.actual_date == "11 Feb"
    assert status.departure.checkin == ""
    assert not hasattr(status.departure, "estimated_time")
    assert not hasattr(status.departure, "baggage")

    # Arrival half: estimated_* + baggage, and no actual_*/checkin attribute.
    assert status.arrival.estimated_time == "14:50"
    assert status.arrival.estimated_date == "11 Feb"
    assert status.arrival.baggage == ""
    assert not hasattr(status.arrival, "actual_time")
    assert not hasattr(status.arrival, "checkin")

    # Shared half.
    assert status.departure.airport == "EGLL"
    assert status.arrival.airport_full == "John F Kennedy International Airport"
    assert (status.departure.terminal, status.departure.gate) == ("5", "A12")
    assert (status.arrival.terminal, status.arrival.gate) == ("7", "B15")


def test_times_and_dates_stay_opaque_strings(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    """Airport-local times and year-less dates are never coerced to datetime."""

    _mock(respx_mock, "/flight_status/BA123", load_fixture("flight_status"))

    status = FlightStatus(client).get("BA123")

    assert status.departure is not None
    assert status.arrival is not None
    for value in (
        status.departure.scheduled_time,
        status.departure.scheduled_date,
        status.departure.actual_time,
        status.arrival.estimated_time,
        status.arrival.scheduled_date,
    ):
        assert isinstance(value, str)

    assert status.departure.scheduled_time == "10:30"
    # Day and month only: no year to parse, on purpose.
    assert status.departure.scheduled_date == "11 Feb"


def test_empty_values_are_empty_strings_not_none(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """Missing scraped values arrive as "" (or the "--" presentation sentinel)."""

    payload = {
        "flight_number": "XX 1",
        "airline": "Unknown",
        "status": "Unknown",
        "departure": {
            "airport": "LHR • London",
            "airport_full": "",
            "scheduled_time": "--:--",
            "scheduled_date": "",
            "actual_time": "--:--",
            "actual_date": "",
            "terminal": "--",
            "gate": "--",
            "checkin": "--",
        },
        "arrival": {
            "airport": "JFK • New York",
            "airport_full": "",
            "scheduled_time": "07:15",
            "scheduled_date": "12 Feb",
            "estimated_time": "--:--",
            "estimated_date": "",
            "terminal": "--",
            "gate": "--",
            "baggage": "--",
        },
    }
    _mock(respx_mock, "/flight_status/XX1", payload)

    status = FlightStatus(client).get("XX1")

    assert status.departure is not None
    assert status.arrival is not None
    # Emphatically not None — testing `is None` would miss every unknown value.
    assert status.departure.airport_full == ""
    assert status.departure.airport_full is not None
    assert status.departure.checkin == "--"
    assert status.departure.actual_time == "--:--"
    assert status.arrival.baggage == "--"
    assert status.arrival.estimated_date == ""
    # The city-decorated airport form.
    assert status.departure.airport == "LHR • London"


def test_empty_legs_survive(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    """Both legs can come back as ``{}`` when the source page had no cards."""

    _mock(
        respx_mock,
        "/flight_status/BA123",
        {
            "flight_number": "BA 123",
            "airline": "Unknown",
            "status": "Unknown",
            "departure": {},
            "arrival": {},
        },
    )

    status = FlightStatus(client).get("BA123")

    assert isinstance(status.departure, FlightStatusDeparture)
    assert isinstance(status.arrival, FlightStatusArrival)
    assert status.departure.airport is None
    assert status.arrival.estimated_time is None


def test_missing_legs_and_unknown_fields_survive(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    _mock(
        respx_mock,
        "/flight_status/BA123",
        {"flight_number": "BA 123", "codeshares": ["AA 6141"]},
    )

    status = FlightStatus(client).get("BA123")

    assert status.departure is None
    assert status.arrival is None
    # extra="allow": a new backend field never breaks a pinned SDK.
    assert status.model_extra is not None
    assert status.model_extra["codeshares"] == ["AA 6141"]


def test_request_options_are_forwarded(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/flight_status/BA123", load_fixture("flight_status"))

    FlightStatus(client).get("BA123", request_options={"headers": {"X-Trace": "abc"}})

    assert route.calls.last.request.headers["X-Trace"] == "abc"


def test_unknown_flight_raises(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/flight_status/ZZ9999").mock(
        return_value=httpx.Response(404, json={"detail": "Flight ZZ9999 not found"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        FlightStatus(client).get("ZZ9999")

    assert excinfo.value.status_code == 404
    assert "ZZ9999" in str(excinfo.value)


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_get(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/flight_status/BA123", load_fixture("flight_status"))

    status = await AsyncFlightStatus(async_client).get("BA123")

    assert route.calls.last.request.url.path == "/v3.1/flight_status/BA123"
    assert isinstance(status, FlightStatusResponse)
    assert status.departure is not None
    assert status.departure.checkin == ""
