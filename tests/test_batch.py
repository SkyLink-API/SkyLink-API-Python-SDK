"""``sky.batch`` — fan-out over many identifiers (contract §7).

Everything here goes through ``respx``: the point of the namespace is *which*
requests are made and *how the failures are packaged*, both of which are only
observable at the transport.

The load-bearing test is
:func:`test_metars_one_failure_does_not_lose_the_others` — one 404 among three
codes must still yield three keys.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import (
    NotFoundError,
    ServiceUnavailableError,
    SkyLinkError,
    UnprocessableEntityError,
)
from skylink_api.helpers.weather import flight_category
from skylink_api.models.airports import EnrichedAirport
from skylink_api.models.flight_status import FlightStatusResponse
from skylink_api.models.notams import NotamsResponse
from skylink_api.models.weather import Metar, MetarWithParsed, Taf, TafWithParsed
from skylink_api.resources.batch import AsyncBatch, Batch, _airport_spec, _unique


@pytest.fixture
def batch(client: SkyLink) -> Batch:
    return Batch(client)


@pytest.fixture
def async_batch(async_client: AsyncSkyLink) -> AsyncBatch:
    return AsyncBatch(async_client)


def _metar_route(respx_mock: respx.MockRouter, icao: str, **kwargs: Any) -> respx.Route:
    return respx_mock.get(f"{TEST_BASE_URL}/weather/metar/{icao}").mock(**kwargs)


# ── wiring ───────────────────────────────────────────────────────────────────


def test_batch_is_attached_to_both_clients(client: SkyLink, async_client: AsyncSkyLink) -> None:
    assert isinstance(client.batch, Batch)
    assert type(async_client.batch) is AsyncBatch
    # cached_property: the namespace is built once.
    assert client.batch is client.batch
    assert client.batch._client is client


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_unique_keeps_input_order() -> None:
    assert _unique(["KJFK", "EGLL", "KJFK", "UUEE", "EGLL"]) == ["KJFK", "EGLL", "UUEE"]


def test_airport_spec_picks_the_parameter_from_the_code_shape() -> None:
    assert _airport_spec("JFK").query == {"icao": None, "iata": "JFK"}
    assert _airport_spec("KJFK").query == {"icao": "KJFK", "iata": None}
    # A pseudo-code cannot be resolved at all; it goes out as icao= and 404s.
    assert _airport_spec("GB-0888").query == {"icao": "GB-0888", "iata": None}


# ── metars ───────────────────────────────────────────────────────────────────


def test_metars_one_failure_does_not_lose_the_others(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    """Three codes, one 404: three keys back, two models and one error."""

    metar = load_fixture("weather_metar")
    _metar_route(respx_mock, "KJFK", return_value=httpx.Response(200, json=metar))
    _metar_route(respx_mock, "EGLL", return_value=httpx.Response(200, json=metar))
    _metar_route(
        respx_mock, "ZZZZ", return_value=httpx.Response(404, json={"detail": "Airport not found"})
    )

    results = batch.metars(["KJFK", "ZZZZ", "EGLL"])

    assert list(results) == ["KJFK", "ZZZZ", "EGLL"]
    assert isinstance(results["KJFK"], Metar)
    assert isinstance(results["EGLL"], Metar)
    error = results["ZZZZ"]
    assert isinstance(error, NotFoundError)
    assert error.status_code == 404


def test_metars_collapse_duplicates(batch: Batch, respx_mock: respx.MockRouter) -> None:
    route = _metar_route(
        respx_mock, "KJFK", return_value=httpx.Response(200, json=load_fixture("weather_metar"))
    )

    results = batch.metars(["KJFK", "KJFK", "KJFK"])

    assert route.call_count == 1
    assert list(results) == ["KJFK"]


def test_metars_key_is_the_callers_string_verbatim(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    """No normalisation: the key you passed is the key you look up."""

    route = _metar_route(
        respx_mock, "kjfk", return_value=httpx.Response(200, json=load_fixture("weather_metar"))
    )

    results = batch.metars(["kjfk"])

    assert route.calls.last.request.url.path == "/v3.1/weather/metar/kjfk"
    assert list(results) == ["kjfk"]


def test_metars_empty_input_sends_nothing(batch: Batch, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar")

    assert batch.metars([]) == {}
    assert route.call_count == 0


def test_metars_rejects_a_useless_concurrency_before_any_request(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar")

    with pytest.raises(ValueError, match="concurrency"):
        batch.metars(["KJFK"], concurrency=0)

    assert route.call_count == 0


def test_metars_forwards_request_options(batch: Batch, respx_mock: respx.MockRouter) -> None:
    route = _metar_route(
        respx_mock, "KJFK", return_value=httpx.Response(200, json=load_fixture("weather_metar"))
    )

    batch.metars(["KJFK"], request_options={"headers": {"X-Trace": "abc"}})

    assert route.calls.last.request.headers["x-trace"] == "abc"


def test_metars_do_not_ask_for_the_decoded_block_by_default(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    route = _metar_route(
        respx_mock, "KJFK", return_value=httpx.Response(200, json=load_fixture("weather_metar"))
    )

    results = batch.metars(["KJFK"])

    assert route.calls.last.request.url.params["parsed"] == "false"
    assert type(results["KJFK"]) is Metar


def test_metars_parsed_true_returns_the_decoded_block(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    """``parsed=True`` is what makes a batch usable by ``helpers.weather``.

    Without it every derived value (flight category, ceiling, altimeter unit)
    can only answer ``None``, because those helpers read decoded fields and
    never re-parse the raw report.
    """

    route = _metar_route(
        respx_mock,
        "KJFK",
        return_value=httpx.Response(200, json=load_fixture("weather_metar_parsed")),
    )

    results = batch.metars(["KJFK"], parsed=True)

    assert route.calls.last.request.url.params["parsed"] == "true"
    report = results["KJFK"]
    assert isinstance(report, MetarWithParsed)
    assert report.parsed is not None
    assert flight_category(report) is not None


def test_tafs_parsed_true_returns_the_decoded_periods(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/taf/EGLL").mock(
        return_value=httpx.Response(200, json=load_fixture("weather_taf_parsed"))
    )

    results = batch.tafs(["EGLL"], parsed=True)

    assert route.calls.last.request.url.params["parsed"] == "true"
    assert isinstance(results["EGLL"], TafWithParsed)


def test_metars_all_failing_still_returns_every_key(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar").mock(
        return_value=httpx.Response(503, json={"detail": "weather feed down"})
    )

    results = batch.metars(["KJFK", "EGLL"], concurrency=2)

    assert list(results) == ["KJFK", "EGLL"]
    assert all(isinstance(value, ServiceUnavailableError) for value in results.values())


def test_unexpected_exceptions_are_not_filed_as_results(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """A bug in the SDK must surface, not hide behind a key."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise TypeError("this is a defect, not an API failure")

    client.execute = boom  # type: ignore[method-assign]

    with pytest.raises(TypeError, match="defect"):
        Batch(client).metars(["KJFK"])


# ── tafs ─────────────────────────────────────────────────────────────────────


def test_tafs_returns_models_and_errors(batch: Batch, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/weather/taf/EGLL").mock(
        return_value=httpx.Response(200, json=load_fixture("weather_taf_parsed"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/weather/taf/KABC").mock(
        return_value=httpx.Response(404, json={"detail": "No TAF for this station"})
    )

    results = batch.tafs(["EGLL", "KABC"])

    assert isinstance(results["EGLL"], Taf)
    # A field that issues METAR only is a normal 404 — one key, not an exception.
    assert isinstance(results["KABC"], NotFoundError)


# ── notams ───────────────────────────────────────────────────────────────────


def test_notams_hits_the_per_airport_endpoint(batch: Batch, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/notams/KJFK").mock(
        return_value=httpx.Response(200, json=load_fixture("notams"))
    )

    results = batch.notams(["KJFK"])

    assert route.calls.last.request.url.path == "/v3.1/notams/KJFK"
    assert isinstance(results["KJFK"], NotamsResponse)


# ── airports ─────────────────────────────────────────────────────────────────


def test_airports_dispatches_icao_and_iata_codes(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/airports/search").mock(
        return_value=httpx.Response(200, json=load_fixture("airports_search"))
    )

    results = batch.airports(["KJFK", "LHR"], concurrency=1)

    parameters = [dict(call.request.url.params) for call in route.calls]
    assert {"icao": "KJFK"} in parameters
    assert {"iata": "LHR"} in parameters
    assert isinstance(results["KJFK"], EnrichedAirport)
    assert isinstance(results["LHR"], EnrichedAirport)


def test_airports_pseudocode_comes_back_as_an_error(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    """``GB-0888`` from a location search is unresolvable — a 422, not a crash."""

    respx_mock.get(f"{TEST_BASE_URL}/airports/search").mock(
        return_value=httpx.Response(422, json={"detail": "ICAO code must be 4 characters"})
    )

    results = batch.airports(["GB-0888"])

    assert isinstance(results["GB-0888"], UnprocessableEntityError)


# ── flight statuses ──────────────────────────────────────────────────────────


def test_flight_statuses_key_survives_the_backends_upper_casing(
    batch: Batch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/flight_status/ba123").mock(
        return_value=httpx.Response(200, json=load_fixture("flight_status"))
    )

    results = batch.flight_statuses(["ba123"])

    # The API echoes "BA123"; the dict key stays what the caller passed.
    assert list(results) == ["ba123"]
    assert isinstance(results["ba123"], FlightStatusResponse)


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_metars_one_failure_does_not_lose_the_others(
    async_batch: AsyncBatch, respx_mock: respx.MockRouter
) -> None:
    metar = load_fixture("weather_metar")
    _metar_route(respx_mock, "KJFK", return_value=httpx.Response(200, json=metar))
    _metar_route(respx_mock, "EGLL", return_value=httpx.Response(200, json=metar))
    _metar_route(
        respx_mock, "ZZZZ", return_value=httpx.Response(404, json={"detail": "Airport not found"})
    )

    results = await async_batch.metars(["KJFK", "ZZZZ", "EGLL"])

    assert list(results) == ["KJFK", "ZZZZ", "EGLL"]
    assert isinstance(results["KJFK"], Metar)
    assert isinstance(results["ZZZZ"], SkyLinkError)


async def test_async_collapses_duplicates_and_validates_concurrency(
    async_batch: AsyncBatch, respx_mock: respx.MockRouter
) -> None:
    route = _metar_route(
        respx_mock, "KJFK", return_value=httpx.Response(200, json=load_fixture("weather_metar"))
    )

    assert list(await async_batch.metars(["KJFK", "KJFK"])) == ["KJFK"]
    assert route.call_count == 1

    with pytest.raises(ValueError, match="concurrency"):
        await async_batch.metars(["KJFK"], concurrency=-1)


async def test_async_tafs_notams_airports_and_flight_statuses(
    async_batch: AsyncBatch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/weather/taf/EGLL").mock(
        return_value=httpx.Response(200, json=load_fixture("weather_taf_parsed"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/notams/KJFK").mock(
        return_value=httpx.Response(200, json=load_fixture("notams"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/airports/search").mock(
        return_value=httpx.Response(200, json=load_fixture("airports_search"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/flight_status/BA123").mock(
        return_value=httpx.Response(200, json=load_fixture("flight_status"))
    )

    assert isinstance((await async_batch.tafs(["EGLL"]))["EGLL"], Taf)
    assert isinstance((await async_batch.notams(["KJFK"]))["KJFK"], NotamsResponse)
    assert isinstance((await async_batch.airports(["KJFK"]))["KJFK"], EnrichedAirport)
    statuses = await async_batch.flight_statuses(["BA123"])
    assert isinstance(statuses["BA123"], FlightStatusResponse)
