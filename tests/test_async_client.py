"""End-to-end smoke tests for :class:`~skylink_api.AsyncSkyLink`.

The per-namespace files already mirror every async method against its sync twin
(``test_weather.py::test_async_metar`` and friends), so this file deliberately
does **not** re-test method-by-method behaviour. It exercises the things that
only exist at the *client* level:

* one client instance driving several namespaces, including the endpoints with
  non-JSON or non-GET mechanics (PDF bytes, ``POST``/``PATCH``/``DELETE``);
* concurrency — the shared :class:`httpx.AsyncClient` serving an
  ``asyncio.gather`` fan-out;
* the async retry loop reached through a typed namespace method, plus proof that
  the default backoff really awaits :func:`asyncio.sleep`;
* ``async with`` lifecycle and ``last_rate_limit`` bookkeeping.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_API_KEY, TEST_BASE_URL, AsyncSleepRecorder, load_fixture
from skylink_api import (
    AdsbAircraftList,
    AircraftLookup,
    AsyncSkyLink,
    HistoryFlightsResponse,
    Metar,
    RateLimitInfo,
    ServiceUnavailableError,
    Webhook,
)

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"

HOOK_ID = "0a4f9c2e-7b31-4d85-9f60-31c8a2b7e410"


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── representative endpoints over one client ─────────────────────────────────


async def test_smoke_six_endpoints_over_one_client(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    """A single client walks JSON, bytes and the webhook write path."""

    _mock(respx_mock, "/weather/metar/KJFK", load_fixture("weather_metar"))
    _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))
    _mock(respx_mock, "/aircraft/registration/G-STBA", load_fixture("aircraft_found"))
    _mock(respx_mock, "/ultra/history/flights", load_fixture("history_flights"))
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/pdf").mock(
        return_value=httpx.Response(
            200, content=PDF_BYTES, headers={"Content-Type": "application/pdf"}
        )
    )
    respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json=load_fixture("webhook_created"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(200, json=load_fixture("webhooks_list"))
    )
    respx_mock.delete(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(return_value=httpx.Response(204))

    metar = await async_client.weather.metar("KJFK")
    live = await async_client.adsb.aircraft(lat=51.47, lon=-0.45, radius=50)
    lookup = await async_client.aircraft.by_registration("G-STBA")
    history = await async_client.history.flights(registration="G-STBA")
    pdf = await async_client.briefing.pdf(departure_icao="EGLL", arrival_icao="KJFK")
    hook = await async_client.webhooks.create(
        url="https://hooks.example.com/skylink", event_types=["status_changed"]
    )
    rows = await async_client.webhooks.list()
    deleted = await async_client.webhooks.delete(HOOK_ID)

    assert isinstance(metar, Metar)
    assert metar.icao == "KJFK"
    assert isinstance(live, AdsbAircraftList)
    assert live.aircraft[0].callsign == "BAW117"
    assert isinstance(lookup, AircraftLookup)
    assert lookup.found is True
    assert isinstance(history, HistoryFlightsResponse)
    assert history.count == 2
    # The one binary endpoint: bytes straight through, never decoded.
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert isinstance(hook, Webhook)
    assert hook.id == HOOK_ID
    assert [row.id for row in rows] == [HOOK_ID, "b7d1e0f3-5a68-4c19-83b2-6d90f4c1a552"]
    # 204 has no body to parse.
    assert deleted is None

    # Every call went out authenticated over the single shared connection pool.
    assert len(respx_mock.calls) == 8
    assert all(call.request.headers["x-api-key"] == TEST_API_KEY for call in respx_mock.calls)


async def test_namespaces_are_awaited_concurrently(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    """``asyncio.gather`` over one client — no per-request client construction."""

    _mock(respx_mock, "/weather/metar/EGLL", load_fixture("weather_metar"))
    _mock(respx_mock, "/adsb/aircraft", load_fixture("adsb_aircraft"))
    _mock(respx_mock, "/aircraft/registration/G-STBA", load_fixture("aircraft_found"))

    metar, live, lookup = await asyncio.gather(
        async_client.weather.metar("EGLL"),
        async_client.adsb.aircraft(callsign="BAW117"),
        async_client.aircraft.by_registration("G-STBA"),
    )

    assert metar.raw is not None
    assert live.total_count == 2
    assert lookup.aircraft is not None
    assert lookup.aircraft.icao_type == "B77W"
    assert len(respx_mock.calls) == 3


async def test_last_rate_limit_is_recorded_on_async_success(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(
            200,
            json=load_fixture("weather_metar"),
            headers={
                "X-RateLimit-Requests-Limit": "10000",
                "X-RateLimit-Requests-Remaining": "9987",
                "X-RateLimit-Requests-Reset": "1200",
            },
        )
    )

    assert async_client.last_rate_limit is None
    await async_client.weather.metar("KJFK")

    assert async_client.last_rate_limit == RateLimitInfo(limit=10000, remaining=9987, reset=1200)


# ── retries ──────────────────────────────────────────────────────────────────


async def test_503_is_retried_through_a_namespace_method(
    async_client: AsyncSkyLink, async_sleeper: AsyncSleepRecorder, respx_mock: respx.MockRouter
) -> None:
    """Retries are transport-level, so typed methods get them for free."""

    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        side_effect=[
            httpx.Response(503, json={"detail": "weather feed warming up"}),
            httpx.Response(200, json=load_fixture("weather_metar")),
        ]
    )

    metar = await async_client.weather.metar("KJFK")

    assert metar.icao == "KJFK"
    assert route.call_count == 2
    # One awaited backoff, drawn from full jitter over [0, 0.5).
    assert async_sleeper.count == 1
    assert 0.0 <= async_sleeper.calls[0] < 0.5


async def test_503_gives_up_after_max_retries(
    async_client: AsyncSkyLink, async_sleeper: AsyncSleepRecorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(503, json={"detail": "weather feed warming up"})
    )

    with pytest.raises(ServiceUnavailableError) as excinfo:
        await async_client.weather.metar("KJFK")

    assert excinfo.value.status_code == 503
    assert excinfo.value.message == "weather feed warming up"
    assert route.call_count == 4  # initial + 3 retries
    assert async_sleeper.count == 3


async def test_default_backoff_awaits_asyncio_sleep(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an injected ``sleep`` the client awaits :func:`asyncio.sleep`.

    Every other test hands the client a recorder, so this is the one place that
    proves the production default is a real non-blocking sleep.
    """

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        side_effect=[
            httpx.Response(503, json={"detail": "warming up"}),
            httpx.Response(200, json=load_fixture("weather_metar")),
        ]
    )

    # No sleep= argument: the constructor default is _default_async_sleep.
    async with AsyncSkyLink(api_key=TEST_API_KEY, environ={}) as sky:
        await sky.weather.metar("KJFK")

    assert route.call_count == 2
    assert len(slept) == 1
    assert 0.0 <= slept[0] < 0.5


async def test_retry_after_header_wins_over_jitter(
    async_client: AsyncSkyLink, async_sleeper: AsyncSleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/adsb/aircraft").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}, json={"detail": "slow down"}),
            httpx.Response(200, json=load_fixture("adsb_aircraft")),
        ]
    )

    result = await async_client.adsb.aircraft(lat=51.47, lon=-0.45)

    assert result.total_count == 2
    assert async_sleeper.calls == [3.0]


# ── lifecycle ────────────────────────────────────────────────────────────────


async def test_async_context_manager_yields_the_client_and_closes_it(
    respx_mock: respx.MockRouter,
) -> None:
    _mock(respx_mock, "/weather/metar/KJFK", load_fixture("weather_metar"))

    sky = AsyncSkyLink(api_key=TEST_API_KEY, environ={})
    async with sky as entered:
        assert entered is sky
        assert sky.http_client.is_closed is False
        await sky.weather.metar("KJFK")

    assert sky.http_client.is_closed


async def test_async_context_manager_closes_on_error(respx_mock: respx.MockRouter) -> None:
    """The pool is released even when the body raises — including SDK errors."""

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar/ZZZZ").mock(
        return_value=httpx.Response(404, json={"detail": "Airport not found"})
    )

    sky = AsyncSkyLink(api_key=TEST_API_KEY, environ={}, max_retries=0)
    with pytest.raises(Exception, match="Airport not found"):
        async with sky:
            await sky.weather.metar("ZZZZ")

    assert sky.http_client.is_closed


async def test_aclose_without_context_manager(respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/adsb/health", {"status": "healthy", "active_aircraft_count": 12})

    sky = AsyncSkyLink(api_key=TEST_API_KEY, environ={})
    health = await sky.adsb.health()
    await sky.aclose()

    assert health.status == "healthy"
    assert sky.http_client.is_closed
