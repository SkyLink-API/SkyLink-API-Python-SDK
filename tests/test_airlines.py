"""``sky.airlines`` — code lookup returning a bare JSON array.

Payloads are built inline from the router's OpenAPI example
(``routers/airlines.py``); there is no recorded fixture for this endpoint.

The namespace is not attached to the client yet (task A8 does the wiring), so
the resource classes are instantiated directly here.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.airlines import Airline
from skylink_api.resources.airlines import Airlines, AsyncAirlines, _search_spec

BA: dict[str, Any] = {
    "id": 1355,
    "name": "British Airways",
    "alias": None,
    "iata": "BA",
    "icao": "BAW",
    "callsign": "SPEEDBIRD",
    "country": "United Kingdom",
    "active": "Y",
    "logo": "https://media.skylinkapi.com/logos/BA.png",
}


@pytest.fixture
def airlines(client: SkyLink) -> Airlines:
    return Airlines(client)


@pytest.fixture
def async_airlines(async_client: AsyncSkyLink) -> AsyncAirlines:
    return AsyncAirlines(async_client)


def _mock(respx_mock: respx.MockRouter, payload: Any) -> respx.Route:
    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}/airlines/search").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_produces_the_documented_spec() -> None:
    spec = _search_spec(icao="BAW")
    assert spec.method == "GET"
    assert spec.path == "/airlines/search"
    assert spec.query == {"icao": "BAW", "iata": None}
    # A bare array, not an envelope.
    assert spec.cast_to == list[Airline]

    assert _search_spec(iata="BA").query == {"icao": None, "iata": "BA"}
    assert _search_spec(icao="BAW", iata="BA").query == {"icao": "BAW", "iata": "BA"}


def test_builder_requires_a_code() -> None:
    with pytest.raises(ValueError, match="at least one of icao= or iata="):
        _search_spec()


# ── search ───────────────────────────────────────────────────────────────────


def test_search_requires_a_code_before_any_request(
    airlines: Airlines, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, [BA])

    with pytest.raises(ValueError, match="at least one of icao= or iata="):
        airlines.search()

    assert route.call_count == 0


def test_search_by_iata_returns_a_plain_list(
    airlines: Airlines, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, [BA])

    result = airlines.search(iata="BA")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/airlines/search"
    assert request.url.params["iata"] == "BA"
    assert "icao" not in request.url.params
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, list)
    assert len(result) == 1
    airline = result[0]
    assert isinstance(airline, Airline)
    assert airline.id == 1355
    assert airline.name == "British Airways"
    assert airline.icao == "BAW"
    assert airline.callsign == "SPEEDBIRD"
    assert airline.alias is None
    assert airline.logo == "https://media.skylinkapi.com/logos/BA.png"


def test_active_is_the_string_y_or_n_not_a_bool(
    airlines: Airlines, respx_mock: respx.MockRouter
) -> None:
    """``"N"`` is truthy — the flag must never be coerced to a bool."""

    _mock(respx_mock, [BA, {**BA, "id": 2, "name": "Defunct Air", "active": "N", "logo": None}])

    live, defunct = airlines.search(icao="BAW")

    assert live.active == "Y"
    assert defunct.active == "N"
    assert isinstance(defunct.active, str)
    assert not isinstance(defunct.active, bool)
    # The dataset keeps dead carriers, so a code can match several rows.
    assert [a.name for a in (live, defunct)] == ["British Airways", "Defunct Air"]
    # No IATA code ⇒ no generated logo URL.
    assert defunct.logo is None


def test_search_by_icao_and_unknown_fields_survive(
    airlines: Airlines, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, [{**BA, "brand_new_field": ["x"]}])

    result = airlines.search(icao="BAW")

    assert route.calls.last.request.url.params["icao"] == "BAW"
    assert result[0].model_extra is not None
    assert result[0].model_extra["brand_new_field"] == ["x"]


def test_both_codes_are_sent_together(airlines: Airlines, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, [BA])

    airlines.search(icao="BAW", iata="BA")

    params = route.calls.last.request.url.params
    assert params["icao"] == "BAW"
    assert params["iata"] == "BA"


def test_no_match_is_a_404(airlines: Airlines, respx_mock: respx.MockRouter) -> None:
    """An empty result is an error on this route, not an empty list."""

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/airlines/search").mock(
        return_value=httpx.Response(404, json={"detail": "No airlines found for code 'ZZZ'"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        airlines.search(icao="ZZZ")

    assert excinfo.value.status_code == 404
    assert "ZZZ" in str(excinfo.value)


def test_request_options_are_forwarded(airlines: Airlines, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, [BA])

    airlines.search(iata="BA", request_options={"headers": {"X-Trace": "abc"}})

    assert route.calls.last.request.headers["X-Trace"] == "abc"


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_search(async_airlines: AsyncAirlines, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, [BA])

    result = await async_airlines.search(iata="BA")

    assert route.calls.last.request.url.params["iata"] == "BA"
    assert [a.icao for a in result] == ["BAW"]
    assert result[0].active == "Y"


async def test_async_search_validates_before_sending(
    async_airlines: AsyncAirlines, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, [BA])

    with pytest.raises(ValueError, match="at least one of icao= or iata="):
        await async_airlines.search()

    assert route.call_count == 0
