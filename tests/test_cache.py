"""Opt-in response cache: key/TTL rules, the store, and the single transport hook.

Time is never real here — :class:`MemoryCache` takes its clock, so an expiry test
is a variable assignment rather than a ``sleep``. Network is never real either:
every "did it hit the cache?" assertion is an ``respx`` ``call_count``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_API_KEY, TEST_BASE_URL, TEST_PROVIDER, SleepRecorder
from skylink_api import MemoryCache, Metar, SkyLink
from skylink_api._client import AsyncSkyLink
from skylink_api._types import RequestSpec
from skylink_api.helpers.cache import (
    CacheProtocol,
    cache_key,
    operation_name,
    resolve_ttl,
)

METAR = {"raw": "KJFK 271851Z 28014KT 10SM FEW045 22/09 A3002", "icao": "KJFK"}

HOOK_ID = "0a4f9c2e-7b31-4d85-9f60-31c8a2b7e410"
HOOK_URL = "https://example.com/hooks/skylink"


class Clock:
    """Monotonic clock under test control."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _client(sleeper: SleepRecorder, cache: Any = None, **kwargs: Any) -> SkyLink:
    return SkyLink(
        api_key=TEST_API_KEY,
        provider=TEST_PROVIDER,
        sleep=sleeper,
        environ={},
        cache=cache,
        **kwargs,
    )


# ── operation names ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/weather/metar/KJFK", "weather.metar"),
        ("/weather/taf/EGLL", "weather.taf"),
        ("/weather/winds-aloft", "weather.winds-aloft"),
        ("/airports/search", "airports.search"),
        ("/airports/search/text", "airports.search.text"),
        ("/airports/search/ip", "airports.search.ip"),
        ("/adsb/aircraft/statistics", "adsb.aircraft.statistics"),
        ("/aircraft/registration/G-STBA", "aircraft.registration"),
        # ICAO24 values are lower-case hex, but they start with a digit far more
        # often than not; either way the namespace prefix is what a TTL keys on.
        ("/aircraft/icao24/4ca7b3", "aircraft.icao24"),
        ("/charts/EGLL/approach", "charts"),
        ("/delays/faa/KJFK", "delays.faa"),
        ("/notams/KJFK", "notams"),
        ("/flight_status/BA123", "flight_status"),
        ("/ml/flight-time", "ml.flight-time"),
        ("/distance", "distance"),
        # /countries and /regions belong to the geo namespace.
        ("/countries", "geo.countries"),
        ("/countries/US", "geo.countries"),
        ("/regions/US-NY", "geo.regions"),
        # The history plan is a subscription detail, not a different operation.
        ("/ultra/history/flights", "history.flights"),
        ("/mega/history/flights", "history.flights"),
        ("/ultra/history/flight/12345/track", "history.flight"),
        ("/mega/history/positions/registration/G-STBA", "history.positions.registration"),
        ("", ""),
        ("/", ""),
    ],
)
def test_operation_name(path: str, expected: str) -> None:
    assert operation_name(path) == expected


def test_operation_name_keeps_lowercase_values_but_ttl_still_resolves() -> None:
    """A lower-case ICAO looks like a route segment — the prefix walk saves it."""

    name = operation_name("/weather/metar/kjfk")
    assert name == "weather.metar.kjfk"
    assert resolve_ttl(name, {"weather.metar": 60}, 0) == 60


# ── TTL rules ────────────────────────────────────────────────────────────────


def test_resolve_ttl_exact_then_prefix_then_default() -> None:
    ttls = {"weather.metar": 60, "weather.*": 300, "*": 5}
    assert resolve_ttl("weather.metar", ttls, 0) == 60
    assert resolve_ttl("weather.taf", ttls, 0) == 300
    assert resolve_ttl("geo.countries", ttls, 0) == 5
    assert resolve_ttl("geo.countries", {}, 42) == 42


def test_resolve_ttl_longest_match_wins() -> None:
    """A namespace-wide rule can be overridden for a single operation."""

    ttls = {"geo.*": 86_400, "geo.regions": 300}
    assert resolve_ttl("geo.regions", ttls, 0) == 300
    assert resolve_ttl("geo.countries", ttls, 0) == 86_400


def test_memory_cache_ttl_for_uses_its_own_rules() -> None:
    cache = MemoryCache(default_ttl=7, ttls={"weather.*": 60})
    assert cache.ttl_for("weather.metar") == 60
    assert cache.ttl_for("geo.countries") == 7
    assert MemoryCache().ttl_for("weather.metar") == 0


# ── cache keys ───────────────────────────────────────────────────────────────


def test_cache_key_sorts_the_query() -> None:
    first = cache_key("GET", "/weather/metar/KJFK", {"b": "2", "a": "1"})
    second = cache_key("GET", "/weather/metar/KJFK", {"a": "1", "b": "2"})
    assert first == second


def test_cache_key_separates_providers_and_base_urls() -> None:
    direct = cache_key("GET", "/countries", provider="direct", base_url="https://a")
    rapid = cache_key("GET", "/countries", provider="rapidapi", base_url="https://a")
    staging = cache_key("GET", "/countries", provider="direct", base_url="https://b")
    assert len({direct, rapid, staging}) == 3


# ── the store ────────────────────────────────────────────────────────────────


def test_memory_cache_round_trip_and_expiry() -> None:
    clock = Clock()
    cache = MemoryCache(monotonic=clock)
    cache.set("k", {"v": 1}, 30)
    assert cache.get("k") == {"v": 1}

    clock.advance(29.9)
    assert cache.get("k") == {"v": 1}

    clock.advance(0.2)
    assert cache.get("k") is None
    assert len(cache) == 0  # expired entries are dropped on read


def test_memory_cache_ignores_non_positive_ttl() -> None:
    cache = MemoryCache()
    cache.set("k", "v", 0)
    cache.set("j", "v", -5)
    assert cache.get("k") is None
    assert len(cache) == 0


def test_memory_cache_evicts_least_recently_used() -> None:
    cache = MemoryCache(max_entries=2, monotonic=Clock())
    cache.set("a", 1, 60)
    cache.set("b", 2, 60)
    assert cache.get("a") == 1  # "a" is now the most recently used
    cache.set("c", 3, 60)

    assert len(cache) == 2
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_memory_cache_clear_and_repr() -> None:
    cache = MemoryCache(default_ttl=10, ttls={"geo.*": 60})
    cache.set("a", 1, 60)
    assert "entries=1" in repr(cache)
    cache.clear()
    assert len(cache) == 0


def test_memory_cache_rejects_a_useless_size() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        MemoryCache(max_entries=0)


def test_memory_cache_satisfies_the_protocol() -> None:
    assert isinstance(MemoryCache(), CacheProtocol)
    assert not isinstance(object(), CacheProtocol)


# ── transport integration ────────────────────────────────────────────────────


def test_second_get_is_served_from_cache(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    cache = MemoryCache(ttls={"weather.metar": 300}, monotonic=Clock())
    with _client(sleeper, cache) as sky:
        first = sky.weather.metar("KJFK")
        second = sky.weather.metar("KJFK")

    assert route.call_count == 1
    assert isinstance(second, Metar)
    assert second.raw == first.raw


def test_cache_hit_returns_a_fresh_model_each_time(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """The raw body is cached, not the model — a mutating caller cannot poison it."""

    respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    with _client(sleeper, MemoryCache(ttls={"weather.*": 300}, monotonic=Clock())) as sky:
        first = sky.weather.metar("KJFK")
        first.icao = "TAMPERED"
        second = sky.weather.metar("KJFK")

    assert second.icao == "KJFK"
    assert second is not first


def test_expired_entry_goes_back_to_the_network(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    clock = Clock()
    cache = MemoryCache(ttls={"weather.metar": 60}, monotonic=clock)
    with _client(sleeper, cache) as sky:
        sky.weather.metar("KJFK")
        clock.advance(59)
        sky.weather.metar("KJFK")
        assert route.call_count == 1

        clock.advance(2)
        sky.weather.metar("KJFK")
        assert route.call_count == 2


def test_prefix_rule_covers_a_whole_namespace(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    metar = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    taf = respx_mock.get(f"{TEST_BASE_URL}/weather/taf/KJFK").mock(
        return_value=httpx.Response(200, json={"raw": "TAF KJFK", "icao": "KJFK"})
    )
    countries = respx_mock.get(f"{TEST_BASE_URL}/countries").mock(
        return_value=httpx.Response(200, json={"countries": [], "total": 0})
    )
    cache = MemoryCache(ttls={"weather.*": 300}, monotonic=Clock())
    with _client(sleeper, cache) as sky:
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")
        sky.weather.taf("KJFK")
        sky.weather.taf("KJFK")
        sky.geo.countries()
        sky.geo.countries()

    assert metar.call_count == 1
    assert taf.call_count == 1
    assert countries.call_count == 2  # no rule matches geo.countries


def test_different_query_is_a_different_entry(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    cache = MemoryCache(ttls={"weather.metar": 300}, monotonic=Clock())
    with _client(sleeper, cache) as sky:
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK", parsed=True)

    assert route.call_count == 2


def test_post_is_never_cached(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    route = respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json={"id": HOOK_ID, "url": HOOK_URL})
    )
    # ``default_ttl`` matches every operation — POST is excluded by method, not by rule.
    with _client(sleeper, MemoryCache(default_ttl=600, monotonic=Clock())) as sky:
        sky.webhooks.create(url=HOOK_URL, event_types=["status_changed"])
        sky.webhooks.create(url=HOOK_URL, event_types=["status_changed"])

    assert route.call_count == 2


def test_errors_are_never_cached(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KZZZ").mock(
        side_effect=[
            httpx.Response(404, json={"detail": "Station not found"}),
            httpx.Response(200, json=METAR),
        ]
    )
    cache = MemoryCache(default_ttl=600, monotonic=Clock())
    with _client(sleeper, cache, max_retries=0) as sky:
        with pytest.raises(Exception, match="Station not found"):
            sky.weather.metar("KZZZ")
        sky.weather.metar("KZZZ")

    assert route.call_count == 2


def test_no_cache_by_default_changes_nothing(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    with _client(sleeper) as sky:
        assert sky.cache is None
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")

    assert route.call_count == 2


def test_unconfigured_memory_cache_changes_nothing(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """``default_ttl=0`` is the default: opting in still opts in to nothing."""

    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    cache = MemoryCache()
    with _client(sleeper, cache) as sky:
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")

    assert route.call_count == 2
    assert len(cache) == 0


def test_text_and_bytes_payloads_are_cached_too(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/pdf").mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.7\n", headers={"content-type": "application/pdf"}
        )
    )
    cache = MemoryCache(ttls={"briefing.*": 300}, monotonic=Clock())
    with _client(sleeper, cache) as sky:
        first = sky.briefing.pdf(departure_icao="KJFK", arrival_icao="EGLL")
        second = sky.briefing.pdf(departure_icao="KJFK", arrival_icao="EGLL")

    assert route.call_count == 1
    assert first == second == b"%PDF-1.7\n"


def test_entry_stored_for_another_response_kind_is_a_miss(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """Same URL fetched as text and as JSON: the stored kind must match."""

    route = respx_mock.get(f"{TEST_BASE_URL}/anything").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    cache = MemoryCache(default_ttl=600, monotonic=Clock())
    with _client(sleeper, cache) as sky:
        assert sky.request("GET", "/anything", response_kind="text") == '{"ok":true}'
        assert sky.request("GET", "/anything") == {"ok": True}

    assert route.call_count == 2


def test_explicit_operation_on_the_spec_wins(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/anything").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    cache = MemoryCache(ttls={"custom.op": 300}, monotonic=Clock())
    with _client(sleeper, cache) as sky:
        spec = RequestSpec(method="GET", path="/anything", operation="custom.op")
        sky.execute(spec)
        sky.execute(spec)

    assert route.call_count == 1


# ── foreign caches ───────────────────────────────────────────────────────────


class DictCache:
    """A minimal third-party store: get/set only, expiry is its own business."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.ttls: list[float] = []

    def get(self, key: str) -> Any | None:
        return self.data.get(key)

    def set(self, key: str, value: Any, ttl: float) -> None:
        self.ttls.append(ttl)
        self.data[key] = value


def test_cache_without_ttl_for_caches_every_get(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    cache = DictCache()
    with _client(sleeper, cache) as sky:
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")

    assert route.call_count == 1
    assert cache.ttls == [0.0]  # "no SDK-side expiry — the store decides"


class BrokenCache:
    def get(self, key: str) -> Any | None:
        raise RuntimeError("redis is down")

    def set(self, key: str, value: Any, ttl: float) -> None:
        raise RuntimeError("redis is still down")


def test_broken_cache_warns_but_serves_the_request(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    with _client(sleeper, BrokenCache()) as sky, pytest.warns(RuntimeWarning, match="redis"):
        metar = sky.weather.metar("KJFK")

    assert metar.icao == "KJFK"


def test_a_non_cache_object_is_rejected_at_construction() -> None:
    with pytest.raises(TypeError, match="CacheProtocol"):
        SkyLink(api_key="k", environ={}, cache=object())  # type: ignore[arg-type]


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_second_get_is_served_from_cache(
    respx_mock: respx.MockRouter, async_sleeper: Any
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/weather/metar/KJFK").mock(
        return_value=httpx.Response(200, json=METAR)
    )
    cache = MemoryCache(ttls={"weather.metar": 300}, monotonic=Clock())
    async with AsyncSkyLink(
        api_key=TEST_API_KEY,
        provider=TEST_PROVIDER,
        sleep=async_sleeper,
        environ={},
        cache=cache,
    ) as sky:
        first = await sky.weather.metar("KJFK")
        second = await sky.weather.metar("KJFK")

    assert route.call_count == 1
    assert first.raw == second.raw


async def test_async_post_is_not_cached(respx_mock: respx.MockRouter, async_sleeper: Any) -> None:
    route = respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json={"id": HOOK_ID, "url": HOOK_URL})
    )
    async with AsyncSkyLink(
        api_key=TEST_API_KEY,
        provider=TEST_PROVIDER,
        sleep=async_sleeper,
        environ={},
        cache=MemoryCache(default_ttl=600, monotonic=Clock()),
    ) as sky:
        await sky.webhooks.create(url=HOOK_URL, event_types=["status_changed"])
        await sky.webhooks.create(url=HOOK_URL, event_types=["status_changed"])

    assert route.call_count == 2
