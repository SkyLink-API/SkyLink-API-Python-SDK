"""``sky.tickets`` — fare search.

The namespace is not attached to the client yet (task A8 does the wiring), so
the resource classes are instantiated directly here.

Fixture: ``tickets_search.json``, verbatim from the router's OpenAPI example —
offer 1 is a direct flight with ``layovers: null``, offer 2 is a one-stop.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import ServiceUnavailableError, UnprocessableEntityError
from skylink_api.models.tickets import TicketSearchResponse
from skylink_api.resources.tickets import AsyncTickets, Tickets, _search_spec

PATH = "/tickets/search"


def _mock(respx_mock: respx.MockRouter, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}/tickets/search`` (query independent)."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{PATH}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_produces_the_documented_spec() -> None:
    spec = _search_spec(origin="LHR", destination="JFK")

    assert spec.method == "GET"
    assert spec.path == PATH
    assert spec.cast_to is TicketSearchResponse
    # date omitted → dropped from the query; the API defaults to today + 7 days.
    assert spec.query == {
        "origin": "LHR",
        "destination": "JFK",
        "date": None,
        "passengers": 1,
    }


@pytest.mark.parametrize(
    "value",
    [
        date(2026, 5, 1),
        datetime(2026, 5, 1, 18, 30),
        "2026-05-01",
        "2026-05-01T18:30:00",
    ],
)
def test_builder_normalises_dates_to_yyyy_mm_dd(value: Any) -> None:
    """``date``/``datetime``/ISO string all land on the wire format the API wants."""

    assert _search_spec(origin="LHR", destination="JFK", date=value).query["date"] == "2026-05-01"


def test_builder_passes_unparseable_date_strings_through() -> None:
    """An already-formatted or exotic string is not mangled — the API can judge it."""

    assert (
        _search_spec(origin="LHR", destination="JFK", date="tomorrow").query["date"] == "tomorrow"
    )


# ── search ───────────────────────────────────────────────────────────────────


def test_search_happy_path(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, load_fixture("tickets_search"))

    result = Tickets(client).search(origin="LHR", destination="JFK", date=date(2026, 5, 1))

    request = route.calls.last.request
    assert request.url.path == "/v3.1/tickets/search"
    assert request.url.params["origin"] == "LHR"
    assert request.url.params["destination"] == "JFK"
    assert request.url.params["date"] == "2026-05-01"
    assert request.url.params["passengers"] == "1"
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, TicketSearchResponse)
    assert result.origin == "LHR"
    assert result.destination == "JFK"
    assert result.passengers == 1
    assert result.count == 3
    assert len(result.flights) == 2  # count is the API's own, not len(flights)
    # The echoed travel date stays a string, not a date object.
    assert result.date == "2026-05-01"
    assert isinstance(result.date, str)


def test_search_date_is_omitted_when_none(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, load_fixture("tickets_search"))

    Tickets(client).search(origin="LHR", destination="JFK", passengers=3)

    params = route.calls.last.request.url.params
    assert "date" not in params
    assert params["passengers"] == "3"


def test_layovers_is_none_for_a_direct_flight(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """The nonstop signal is ``None``, **not** an empty list."""

    _mock(respx_mock, load_fixture("tickets_search"))

    result = Tickets(client).search(origin="LHR", destination="JFK")

    direct, one_stop = result.flights
    assert direct.stops == 0
    assert direct.layovers is None
    assert direct.layovers != []
    assert len(direct.legs) == 1

    assert one_stop.stops == 1
    assert one_stop.layovers is not None
    assert len(one_stop.layovers) == 1
    assert one_stop.layovers[0].airport == "MAD"
    assert one_stop.layovers[0].duration_min == 155
    assert len(one_stop.legs) == 2


def test_datetimes_stay_naive_strings(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    """``*_datetime`` is local wall-clock time with no offset — never a ``datetime``."""

    _mock(respx_mock, load_fixture("tickets_search"))

    leg = Tickets(client).search(origin="LHR", destination="JFK").flights[0].legs[0]

    assert leg.departure_datetime == "2026-05-01T09:00:00"
    assert isinstance(leg.departure_datetime, str)
    assert isinstance(leg.arrival_datetime, str)
    # No timezone designator anywhere in the value.
    assert leg.departure_datetime is not None
    assert "Z" not in leg.departure_datetime
    assert "+" not in leg.departure_datetime
    # The short forms are strings too.
    assert leg.departure_time == "09:00"
    assert leg.arrival_time == "11:45"


def test_leg_fields(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, load_fixture("tickets_search"))

    leg = Tickets(client).search(origin="LHR", destination="JFK").flights[0].legs[0]

    assert leg.flight_number == "BA117"
    assert leg.airline == "British Airways"
    assert leg.airline_code == "BA"
    assert leg.departure_airport == "LHR"
    assert leg.arrival_airport == "JFK"
    assert leg.arrival_airport_name == "John F Kennedy International Airport"
    assert leg.duration_min == 465


def test_price_may_still_be_in_the_original_currency(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """FX failure leaves ``price_usd`` holding the original amount, unflagged."""

    _mock(respx_mock, load_fixture("tickets_search"))

    offer = Tickets(client).search(origin="LHR", destination="JFK").flights[0]

    assert offer.price_usd == 542.0
    assert offer.original_price == 498.0
    assert offer.original_currency == "EUR"
    assert offer.total_duration_min == 465


def test_offer_without_the_original_price_keys(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """The live service emits neither key — both must be optional."""

    payload = {
        "origin": "LHR",
        "destination": "JFK",
        "date": "2026-05-01",
        "passengers": 1,
        "count": 1,
        "flights": [
            {
                "price_usd": 542.0,
                "total_duration_min": 465,
                "stops": 0,
                "legs": [{"flight_number": "BA117"}],
                "layovers": None,
                "fare_brand": "ECONOMY_BASIC",
            }
        ],
    }
    _mock(respx_mock, payload)

    offer = Tickets(client).search(origin="LHR", destination="JFK").flights[0]

    assert offer.original_price is None
    assert offer.original_currency is None
    # extra="allow": a new backend key is kept, not rejected.
    assert offer.model_extra is not None
    assert offer.model_extra["fare_brand"] == "ECONOMY_BASIC"


def test_no_fares_is_a_normal_200(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(
        respx_mock,
        {
            "origin": "LHR",
            "destination": "JFK",
            "date": "2026-05-01",
            "passengers": 1,
            "count": 0,
            "flights": [],
        },
    )

    result = Tickets(client).search(origin="LHR", destination="JFK")

    assert result.count == 0
    assert result.flights == []


def test_request_options_are_forwarded(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, load_fixture("tickets_search"))

    Tickets(client).search(
        origin="LHR",
        destination="JFK",
        request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}},
    )

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


def test_same_airport_raises_422(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}{PATH}").mock(
        return_value=httpx.Response(
            422, json={"detail": "Origin and destination must be different"}
        )
    )

    with pytest.raises(UnprocessableEntityError) as excinfo:
        Tickets(client).search(origin="LHR", destination="LHR")

    assert excinfo.value.status_code == 422


def test_upstream_down_raises_503(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}{PATH}").mock(
        return_value=httpx.Response(503, json={"detail": "Ticket search failed"})
    )

    with pytest.raises(ServiceUnavailableError):
        Tickets(client).search(origin="LHR", destination="JFK", request_options={"max_retries": 0})


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_search(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, load_fixture("tickets_search"))

    result = await AsyncTickets(async_client).search(
        origin="LHR", destination="JFK", date=datetime(2026, 5, 1, 6, 0), passengers=2
    )

    params = route.calls.last.request.url.params
    assert params["date"] == "2026-05-01"
    assert params["passengers"] == "2"
    assert isinstance(result, TicketSearchResponse)
    assert result.flights[0].layovers is None
