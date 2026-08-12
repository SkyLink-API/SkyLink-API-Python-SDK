"""``sky.schedules`` — departure and arrival boards.

The namespace is not attached to the client yet (task A8 does the wiring), so the
resource is constructed directly against the client here.

Traps under test: PascalCase keys inside ``flights[]`` (including the all-caps
``IATA``), ``Destination`` vs ``Origin`` being two different row types, the
``DD-MM-YYYY`` date format, ``ts`` in milliseconds, and the client-side
"exactly one of icao/iata" check.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import BadRequestError
from skylink_api.models.schedules import (
    ArrivalFlight,
    ArrivalsResponse,
    DepartureFlight,
    DeparturesResponse,
)
from skylink_api.resources.schedules import (
    AsyncSchedules,
    Schedules,
    _arrivals_spec,
    _departures_spec,
)

ARRIVALS_PAYLOAD: dict[str, Any] = {
    "iata": "MXP",
    "direction": "arrivals",
    "airport_code": "LIMC",
    "flights": [
        {
            "Time": "16:20",
            "Date": "11 Feb",
            "IATA": "RAK",
            "Origin": "Marrakech",
            "Flight": "EC3929",
            "Airline": "easyJet Europe",
            "Status": "Landed 16:15",
        }
    ],
    "total_flights": 72,
    "pages_fetched": 3,
}


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    departures = _departures_spec(icao="LIMC")
    assert departures.method == "GET"
    assert departures.path == "/schedules/departures"
    assert departures.cast_to is DeparturesResponse
    assert departures.query == {
        "icao": "LIMC",
        "iata": None,
        "date": None,
        "time": None,
        "ts": None,
    }

    arrivals = _arrivals_spec(iata="MXP")
    assert arrivals.path == "/schedules/arrivals"
    assert arrivals.cast_to is ArrivalsResponse
    assert arrivals.query is not None
    assert arrivals.query["iata"] == "MXP"


def test_dates_are_formatted_by_the_builder() -> None:
    """The endpoint wants DD-MM-YYYY; conversion happens in the builder."""

    assert _departures_spec(icao="LIMC", date=date(2026, 2, 11)).query["date"] == "11-02-2026"  # type: ignore[index]
    assert (
        _departures_spec(icao="LIMC", date=datetime(2026, 2, 11, 14, 30)).query["date"]  # type: ignore[index]
        == "11-02-2026"
    )
    # ISO strings are reformatted…
    assert _departures_spec(icao="LIMC", date="2026-02-11").query["date"] == "11-02-2026"  # type: ignore[index]
    # …and an already-formatted string is passed through untouched.
    assert _departures_spec(icao="LIMC", date="11-02-2026").query["date"] == "11-02-2026"  # type: ignore[index]


def test_exactly_one_airport_code_is_required() -> None:
    """Client-side check — neither and both are 400s, so never spend a request."""

    with pytest.raises(ValueError, match="either icao= or iata="):
        _departures_spec()
    with pytest.raises(ValueError, match="not both"):
        _departures_spec(icao="LIMC", iata="MXP")
    with pytest.raises(ValueError, match="either icao= or iata="):
        _arrivals_spec()
    with pytest.raises(ValueError, match="not both"):
        _arrivals_spec(icao="LIMC", iata="MXP")


def test_selector_check_runs_before_any_request(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/schedules/departures", load_fixture("schedules_departures"))

    with pytest.raises(ValueError):
        client_schedules = Schedules(client)
        client_schedules.departures()

    assert route.call_count == 0


# ── departures ───────────────────────────────────────────────────────────────


def test_departures_happy_path(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/schedules/departures", load_fixture("schedules_departures"))

    board = Schedules(client).departures(icao="LIMC")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/schedules/departures"
    assert request.url.params["icao"] == "LIMC"
    # Unset optional params are dropped, never sent empty.
    assert "iata" not in request.url.params
    assert "date" not in request.url.params
    assert "ts" not in request.url.params

    assert isinstance(board, DeparturesResponse)
    assert board.iata == "MXP"
    assert board.direction == "departures"
    # airport_code echoes what *you* asked with, iata is what was fetched.
    assert board.airport_code == "LIMC"
    assert board.total_flights == 85
    assert board.pages_fetched == 3


def test_pascal_case_row_keys_map_to_snake_case_attributes(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """``flights[]`` keeps the scraped table's PascalCase headers on the wire."""

    _mock(respx_mock, "/schedules/departures", load_fixture("schedules_departures"))

    board = Schedules(client).departures(icao="LIMC")
    flight = board.flights[0]

    assert isinstance(flight, DepartureFlight)
    # The headline assertion: `destination` is read from the `Destination` key.
    assert flight.destination == "Seoul"
    assert flight.time == "16:05"
    assert flight.date == "11 Feb"
    # All-caps IATA — the alias generator would emit "Iata", so it is explicit.
    assert flight.iata == "ICN"
    assert flight.flight == "C84093"
    assert flight.airline == "Federal Airlines"
    # Status is prose, not an enum.
    assert flight.status == "Estimated 16:39"

    # Nothing was silently swallowed into `extra` by a failed alias.
    assert not flight.model_extra

    # And the aliases really are the PascalCase names.
    assert flight.model_dump(by_alias=True)["Destination"] == "Seoul"
    assert flight.model_dump(by_alias=True)["IATA"] == "ICN"


def test_departure_rows_have_no_origin(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/schedules/departures", load_fixture("schedules_departures"))

    flight = Schedules(client).departures(icao="LIMC").flights[0]

    assert not hasattr(flight, "origin")


def test_departures_date_time_and_ts(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/schedules/departures", load_fixture("schedules_departures"))

    Schedules(client).departures(
        iata="MXP",
        date=date(2026, 2, 11),
        time="14:30",
        # Unix milliseconds, not seconds — 13 digits.
        ts=1770818400000,
    )

    params = route.calls.last.request.url.params
    assert params["iata"] == "MXP"
    assert params["date"] == "11-02-2026"
    assert params["time"] == "14:30"
    assert params["ts"] == "1770818400000"
    assert len(params["ts"]) == 13


def test_departures_envelope_defaults(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/schedules/departures", {"iata": "MXP", "direction": "departures"})

    board = Schedules(client).departures(iata="MXP")

    assert board.flights == []
    assert board.total_flights is None
    assert board.pages_fetched is None


def test_departures_bad_date_raises(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/departures").mock(
        return_value=httpx.Response(
            400, json={"detail": "Invalid date format. Use DD-MM-YYYY (e.g. 11-02-2026)."}
        )
    )

    with pytest.raises(BadRequestError) as excinfo:
        Schedules(client).departures(icao="LIMC", date="nonsense")

    assert excinfo.value.status_code == 400


# ── arrivals ─────────────────────────────────────────────────────────────────


def test_arrivals_rows_have_origin_not_destination(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/schedules/arrivals", ARRIVALS_PAYLOAD)

    board = Schedules(client).arrivals(iata="MXP")

    assert route.calls.last.request.url.path == "/v3.1/schedules/arrivals"
    assert isinstance(board, ArrivalsResponse)
    assert board.direction == "arrivals"

    flight = board.flights[0]
    assert isinstance(flight, ArrivalFlight)
    assert flight.origin == "Marrakech"
    assert flight.iata == "RAK"
    assert flight.status == "Landed 16:15"
    assert not hasattr(flight, "destination")
    assert flight.model_dump(by_alias=True)["Origin"] == "Marrakech"


def test_rows_accept_snake_case_in_user_code() -> None:
    """populate_by_name: your own construction does not have to use the wire names."""

    row = DepartureFlight(time="16:05", iata="ICN", destination="Seoul")

    assert row.time == "16:05"
    assert row.iata == "ICN"
    assert row.destination == "Seoul"


def test_unknown_row_keys_survive(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    payload = {
        **load_fixture("schedules_departures"),
        "flights": [{"Time": "16:05", "Destination": "Seoul", "Aircraft": "B77W"}],
    }
    _mock(respx_mock, "/schedules/departures", payload)

    flight = Schedules(client).departures(icao="LIMC").flights[0]

    assert flight.model_extra is not None
    assert flight.model_extra["Aircraft"] == "B77W"
    assert flight.iata is None


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_departures(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/schedules/departures", load_fixture("schedules_departures"))

    board = await AsyncSchedules(async_client).departures(icao="LIMC", date=date(2026, 2, 11))

    assert route.calls.last.request.url.params["date"] == "11-02-2026"
    assert board.flights[0].destination == "Seoul"


async def test_async_arrivals(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/schedules/arrivals", ARRIVALS_PAYLOAD)

    board = await AsyncSchedules(async_client).arrivals(iata="MXP")

    assert board.flights[0].origin == "Marrakech"


async def test_async_selector_check(async_client: AsyncSkyLink) -> None:
    with pytest.raises(ValueError, match="either icao= or iata="):
        await AsyncSchedules(async_client).arrivals()
