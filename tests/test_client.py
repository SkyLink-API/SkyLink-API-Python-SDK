"""Client-level DX: ``from_env``, ``with_options`` and the reprs.

Everything here is about the client object itself rather than an endpoint, so
the mocked routes are deliberately dull — what is asserted is which client sent
the request, with which settings, over which connection pool.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_API_KEY, TEST_BASE_URL, TEST_PROVIDER, SleepRecorder
from skylink_api import (
    AdsbAircraft,
    Airport,
    AsyncSkyLink,
    AuthenticationError,
    FlightStatusResponse,
    MemoryCache,
    Metar,
    RateLimitInfo,
    SkyLink,
)
from skylink_api._config import mask_api_key
from skylink_api._constants import DEFAULT_MAX_RETRIES, DIRECT_BASE_URL, RAPIDAPI_BASE_URL
from skylink_api.models.geo import Country

METAR = {"raw": "KJFK 271851Z 28014KT 10SM FEW045 22/09 A3002", "icao": "KJFK"}


def _client(sleeper: SleepRecorder, **kwargs: Any) -> SkyLink:
    return SkyLink(
        api_key=TEST_API_KEY,
        provider=TEST_PROVIDER,
        sleep=sleeper,
        environ={},
        **kwargs,
    )


# ── from_env ─────────────────────────────────────────────────────────────────


def test_from_env_reads_the_rapidapi_key() -> None:
    with SkyLink.from_env(environ={"RAPIDAPI_KEY": "from-marketplace"}) as sky:
        assert sky.api_key == "from-marketplace"
        assert sky.base_url == RAPIDAPI_BASE_URL


def test_from_env_reads_the_direct_key() -> None:
    with SkyLink.from_env(provider="direct", environ={"SKYLINK_API_KEY": "from-direct"}) as sky:
        assert sky.api_key == "from-direct"
        assert sky.base_url == DIRECT_BASE_URL


def test_from_env_without_a_key_is_an_authentication_error() -> None:
    with pytest.raises(AuthenticationError, match="RAPIDAPI_KEY"):
        SkyLink.from_env(environ={})


def test_from_env_takes_the_same_overrides_as_the_constructor() -> None:
    cache = MemoryCache(default_ttl=60)
    with SkyLink.from_env(
        environ={"SKYLINK_API_KEY": "k"},
        provider="direct",
        max_retries=0,
        history_plan="mega",
        timeout=12.0,
        default_headers={"X-Trace": "abc"},
        cache=cache,
    ) as sky:
        assert (sky.provider, sky.max_retries, sky.history_plan) == ("direct", 0, "mega")
        assert sky.config.default_headers == {"X-Trace": "abc"}
        assert sky.cache is cache


def test_from_env_has_no_api_key_argument() -> None:
    """The point of the constructor is that the credential comes from the env."""

    with pytest.raises(TypeError):
        SkyLink.from_env(api_key="explicit")  # type: ignore[call-arg]


def test_from_env_reaches_the_network(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{DIRECT_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    with SkyLink.from_env(provider="direct", environ={"SKYLINK_API_KEY": "k"}) as sky:
        assert sky.weather.metar("KJFK").icao == "KJFK"

    assert route.calls.last.request.headers["x-api-key"] == "k"


async def test_async_from_env(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{DIRECT_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    async with AsyncSkyLink.from_env(provider="direct", environ={"SKYLINK_API_KEY": "k"}) as sky:
        assert isinstance(sky, AsyncSkyLink)
        assert (await sky.weather.metar("KJFK")).icao == "KJFK"


# ── with_options ─────────────────────────────────────────────────────────────


def test_with_options_overrides_and_leaves_the_original_alone(sleeper: SleepRecorder) -> None:
    with _client(sleeper) as sky:
        clone = sky.with_options(max_retries=0, history_plan="mega", timeout=90.0)

        assert (clone.max_retries, clone.history_plan) == (0, "mega")
        assert clone.config.timeout == httpx.Timeout(90.0)
        assert (sky.max_retries, sky.history_plan) == (DEFAULT_MAX_RETRIES, "ultra")
        assert clone.api_key == sky.api_key
        assert clone.provider == sky.provider


def test_with_options_reuses_the_transport(sleeper: SleepRecorder) -> None:
    """No second connection pool — that is the whole point of the clone."""

    with _client(sleeper) as sky:
        clone = sky.with_options(timeout=1.0)
        assert clone.http_client is sky.http_client


def test_with_options_does_not_inherit_namespaces(sleeper: SleepRecorder) -> None:
    with _client(sleeper) as sky:
        warm = sky.weather
        clone = sky.with_options(max_retries=1)

        assert clone.weather is not warm
        assert clone.weather._client is clone


def test_closing_a_clone_leaves_the_original_usable(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """The clone borrows the transport; ownership stays with the original."""

    respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    with _client(sleeper) as sky:
        with sky.with_options(max_retries=0) as clone:
            assert clone.weather.metar("KJFK").icao == "KJFK"

        assert not sky.http_client.is_closed
        assert sky.weather.metar("KJFK").icao == "KJFK"


def test_closing_the_original_closes_the_shared_pool(sleeper: SleepRecorder) -> None:
    sky = _client(sleeper)
    clone = sky.with_options(max_retries=0)
    sky.close()

    assert sky.http_client.is_closed
    assert clone.http_client.is_closed  # same pool: keep the original alive


def test_with_options_max_retries_takes_effect(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(503, json={"detail": "upstream down"})
    )
    with _client(sleeper) as sky:
        patient = sky.with_options(max_retries=0)
        with pytest.raises(Exception, match="upstream down"):
            patient.weather.metar("KJFK")

    assert route.call_count == 1  # no retries at all


def test_with_options_history_plan_changes_the_path(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/mega/history/flights").mock(
        return_value=httpx.Response(200, json={"flights": [], "count": 0})
    )
    with _client(sleeper) as sky:
        sky.with_options(history_plan="mega").history.flights(icao24="4ca7b3")

    assert route.call_count == 1


def test_with_options_replaces_default_headers(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    with _client(sleeper, default_headers={"X-Team": "ops", "X-Trace": "1"}) as sky:
        sky.with_options(default_headers={"X-Team": "sre"}).weather.metar("KJFK")

    request = route.calls.last.request
    assert request.headers["x-team"] == "sre"
    assert "x-trace" not in request.headers  # replaced, not merged


def test_with_options_cache_override(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    cache = MemoryCache(ttls={"weather.*": 300})
    with _client(sleeper, cache=cache) as sky:
        uncached = sky.with_options(cache=None)
        assert uncached.cache is None
        uncached.weather.metar("KJFK")
        uncached.weather.metar("KJFK")
        assert route.call_count == 2

        # Omitting the argument keeps (shares) the original's cache.
        assert sky.with_options(max_retries=1).cache is cache


def test_with_options_copies_the_hooks(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(
            200,
            json=METAR,
            headers={
                "X-RateLimit-Requests-Limit": "1000",
                "X-RateLimit-Requests-Remaining": "999",
            },
        )
    )
    seen: list[RateLimitInfo] = []
    later: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_rate_limit(seen.append)
        clone = sky.with_options(max_retries=0)
        sky.on_rate_limit(later.append)  # registered after the clone was taken

        clone.weather.metar("KJFK")

    assert len(seen) == 1
    assert later == []


def test_with_options_validates_its_overrides(sleeper: SleepRecorder) -> None:
    with _client(sleeper) as sky:
        with pytest.raises(ValueError, match="max_retries"):
            sky.with_options(max_retries=-1)
        with pytest.raises(ValueError, match="history_plan"):
            sky.with_options(history_plan="platinum")  # type: ignore[arg-type]


async def test_async_with_options(respx_mock: respx.MockRouter, async_sleeper: Any) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    async with AsyncSkyLink(
        api_key=TEST_API_KEY, provider=TEST_PROVIDER, sleep=async_sleeper, environ={}
    ) as sky:
        clone = sky.with_options(timeout=5.0, max_retries=0)
        assert isinstance(clone, AsyncSkyLink)
        assert clone.http_client is sky.http_client
        assert (await clone.weather.metar("KJFK")).icao == "KJFK"

        await clone.aclose()
        assert not sky.http_client.is_closed
        assert (await sky.weather.metar("KJFK")).icao == "KJFK"


# ── reprs ────────────────────────────────────────────────────────────────────


def test_mask_api_key() -> None:
    assert mask_api_key(None) is None
    assert mask_api_key("short") == "***"
    assert mask_api_key("0123456789abcdef") == "0123…cdef"


def test_client_repr_never_leaks_the_key(sleeper: SleepRecorder) -> None:
    secret = "d41d8cd98f00b204e9800998ecf8427e"
    with SkyLink(api_key=secret, provider="direct", sleep=sleeper, environ={}) as sky:
        text = repr(sky)

    assert secret not in text
    assert "d41d…427e" in text
    assert "SkyLink(" in text
    assert "provider='direct'" in text
    assert "cache=off" in text


def test_client_repr_marks_a_cache(sleeper: SleepRecorder) -> None:
    with _client(sleeper, cache=MemoryCache(default_ttl=30)) as sky:
        assert "cache=on" in repr(sky)


async def test_async_client_repr(async_sleeper: Any) -> None:
    async with AsyncSkyLink(
        api_key="d41d8cd98f00b204e9800998ecf8427e", sleep=async_sleeper, environ={}
    ) as sky:
        text = repr(sky)

    assert text.startswith("AsyncSkyLink(")
    assert "d41d8cd98f00b204e9800998ecf8427e" not in text


def test_metar_repr_is_short() -> None:
    metar = Metar.model_validate(
        {
            "icao": "KJFK",
            "raw": "KJFK 271851Z 28014KT 10SM FEW045 SCT250 22/09 A3002 RMK AO2 SLP164 T02220089",
            "airport_name": "John F Kennedy International Airport",
            "timestamp": "2026-08-12T18:55:00Z",
        }
    )
    text = repr(metar)

    assert text.startswith("Metar(icao='KJFK'")
    assert "…" in text  # the raw report is cut short
    assert len(text) < 160
    assert "airport_name" not in text


def test_repr_skips_missing_fields() -> None:
    assert repr(Metar()) == "Metar()"
    assert repr(Metar(icao="EGLL")) == "Metar(icao='EGLL')"


def test_key_model_reprs() -> None:
    aircraft = AdsbAircraft(icao24="4CA7B3", callsign="RYR1AB", latitude=51.5, longitude=-0.4)
    assert repr(aircraft) == (
        "AdsbAircraft(icao24='4CA7B3', callsign='RYR1AB', latitude=51.5, longitude=-0.4)"
    )

    airport = Airport(ident="EGLL", iata_code="LHR", name="London Heathrow", iso_country="GB")
    assert repr(airport) == (
        "Airport(ident='EGLL', iata_code='LHR', name='London Heathrow', iso_country='GB')"
    )

    status = FlightStatusResponse(
        flight_number="BA 117", airline="British Airways", status="Landed"
    )
    assert repr(status) == (
        "FlightStatusResponse(flight_number='BA 117', airline='British Airways', status='Landed')"
    )


def test_models_without_repr_fields_keep_the_pydantic_repr() -> None:
    """Opt-in: a model that declares nothing keeps pydantic's exhaustive repr."""

    text = repr(Country(code="GB", name="United Kingdom"))
    assert text.startswith("Country(")
    assert "name='United Kingdom'" in text
    assert "continent" in text  # unset fields included, i.e. the pydantic default
