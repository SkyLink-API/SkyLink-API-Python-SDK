"""Quota hooks: ``on_rate_limit`` and ``on_quota_low``.

RapidAPI sends ``X-RateLimit-Requests-{Limit,Remaining,Reset}`` and the direct
gateway sends ``X-RateLimit-{Limit,Remaining,Reset}``; these hooks turn either
spelling into callbacks so a caller can log, throttle or alert without polling
``last_rate_limit`` (which is last-writer-wins under concurrency — the callbacks
are not).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_API_KEY, TEST_BASE_URL, TEST_PROVIDER, SleepRecorder
from skylink_api import AsyncSkyLink, RateLimitInfo, SkyLink

QUOTA_PATH = "/weather/metar/KJFK"
METAR = {"raw": "KJFK 271851Z", "icao": "KJFK"}


def _client(sleeper: SleepRecorder, **kwargs: Any) -> SkyLink:
    return SkyLink(
        api_key=TEST_API_KEY,
        provider=TEST_PROVIDER,
        sleep=sleeper,
        environ={},
        **kwargs,
    )


def _quota(limit: int | None, remaining: int | None, reset: int = 1200) -> dict[str, str]:
    headers = {"X-RateLimit-Requests-Reset": str(reset)}
    if limit is not None:
        headers["X-RateLimit-Requests-Limit"] = str(limit)
    if remaining is not None:
        headers["X-RateLimit-Requests-Remaining"] = str(remaining)
    return headers


def _responses(respx_mock: respx.MockRouter, *quotas: dict[str, str] | None) -> respx.Route:
    """Mock one GET returning ``len(quotas)`` responses with the given headers."""

    return respx_mock.get(f"{TEST_BASE_URL}{QUOTA_PATH}").mock(
        side_effect=[httpx.Response(200, json=METAR, headers=quota or {}) for quota in quotas]
    )


# ── on_rate_limit ────────────────────────────────────────────────────────────


def test_on_rate_limit_fires_per_response(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(respx_mock, _quota(1000, 999), _quota(1000, 998))
    seen: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_rate_limit(seen.append)
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")

    assert [info.remaining for info in seen] == [999, 998]
    assert seen[0].limit == 1000
    assert seen[0].reset == 1200


def test_hook_argument_belongs_to_its_own_response(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """The callback gets the snapshot of *that* response, never ``last_rate_limit``.

    ``last_rate_limit`` is a single attribute: with requests in flight in parallel
    it ends up holding whichever response landed last. The hook argument is passed
    down from the response being processed, so it cannot be overwritten.
    """

    _responses(respx_mock, _quota(1000, 900), _quota(1000, 100))
    pairs: list[tuple[int | None, int | None]] = []

    with _client(sleeper) as sky:
        sky.on_rate_limit(
            lambda info: pairs.append((info.remaining, sky.last_rate_limit.remaining))
        )
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")

    assert pairs == [(900, 900), (100, 100)]


def test_on_rate_limit_stays_silent_without_headers(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(respx_mock, None)
    seen: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_rate_limit(seen.append)
        sky.weather.metar("KJFK")

    assert seen == []


def test_on_rate_limit_unsubscribe(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    _responses(respx_mock, _quota(1000, 999), _quota(1000, 998))
    seen: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        stop = sky.on_rate_limit(seen.append)
        sky.weather.metar("KJFK")
        stop()
        stop()  # idempotent
        sky.weather.metar("KJFK")

    assert len(seen) == 1


def test_on_rate_limit_fires_for_error_responses(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """A 429 carries the most interesting quota numbers of all — do not drop it."""

    respx_mock.get(f"{TEST_BASE_URL}{QUOTA_PATH}").mock(
        return_value=httpx.Response(429, json={"detail": "slow down"}, headers=_quota(1000, 0))
    )
    seen: list[RateLimitInfo] = []

    with _client(sleeper, max_retries=0) as sky:
        sky.on_rate_limit(seen.append)
        with pytest.raises(Exception, match="slow down"):
            sky.weather.metar("KJFK")

    assert [info.remaining for info in seen] == [0]


def test_a_raising_callback_warns_and_the_request_survives(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(respx_mock, _quota(1000, 999))

    def boom(info: RateLimitInfo) -> None:
        raise ValueError("hook exploded")

    with _client(sleeper) as sky:
        sky.on_rate_limit(boom)
        with pytest.warns(RuntimeWarning, match="hook exploded"):
            metar = sky.weather.metar("KJFK")

    assert metar.icao == "KJFK"


def test_several_hooks_all_run_even_if_one_raises(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(respx_mock, _quota(1000, 999))
    seen: list[RateLimitInfo] = []

    def boom(info: RateLimitInfo) -> None:
        raise RuntimeError("nope")

    with _client(sleeper) as sky:
        sky.on_rate_limit(boom)
        sky.on_rate_limit(seen.append)
        with pytest.warns(RuntimeWarning):
            sky.weather.metar("KJFK")

    assert len(seen) == 1


# ── both channels ────────────────────────────────────────────────────────────
#
# The two gateways spell the quota differently. Getting this wrong is invisible
# offline and total in production: the whole quota feature — ``last_rate_limit``
# and both hooks — silently does nothing on the channel that is not understood.


def _direct_quota(limit: int, remaining: int, reset: int = 86385) -> dict[str, str]:
    """Headers as the direct gateway (APISIX) sends them — mixed case included."""

    return {
        "X-Ratelimit-Limit": str(limit),
        "X-Ratelimit-Remaining": str(remaining),
        "X-Ratelimit-Reset": str(reset),
    }


def test_direct_channel_headers_drive_the_attribute_and_both_hooks(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(respx_mock, _direct_quota(750000, 749996), _direct_quota(750000, 1000))
    seen: list[RateLimitInfo] = []
    alerts: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_rate_limit(seen.append)
        sky.on_quota_low(alerts.append)
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")
        last = sky.last_rate_limit

    assert [info.remaining for info in seen] == [749996, 1000]
    assert [info.limit for info in seen] == [750000, 750000]
    assert seen[0].reset == 86385
    assert [info.remaining for info in alerts] == [1000]
    assert last == RateLimitInfo(limit=750000, remaining=1000, reset=86385)


def test_rapidapi_noise_never_shadows_the_plan_quota(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """A verbatim RapidAPI header set: plan quota, free-plan ceiling, generic names.

    Only the plan quota describes *this* key's subscription, so it is the one the
    hooks must report. Picking the free-plan ceiling would claim 499 of 500 calls
    left on a 10 000 call plan — and would fire ``on_quota_low`` at random.
    """

    _responses(
        respx_mock,
        {
            "X-RateLimit-Requests-Limit": "10000",
            "X-RateLimit-Requests-Remaining": "8938",
            "X-RateLimit-Requests-Reset": "319216",
            "X-RateLimit-rapid-free-plans-hard-limit-limit": "500",
            "X-RateLimit-rapid-free-plans-hard-limit-remaining": "10",
            "X-RateLimit-rapid-free-plans-hard-limit-reset": "60",
            **_direct_quota(750000, 749996),
        },
    )
    seen: list[RateLimitInfo] = []
    alerts: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_rate_limit(seen.append)
        sky.on_quota_low(alerts.append)
        sky.weather.metar("KJFK")
        last = sky.last_rate_limit

    assert last == RateLimitInfo(limit=10000, remaining=8938, reset=319216)
    assert [info.remaining for info in seen] == [8938]
    assert alerts == []  # 89% left: the free-plan ceiling's 2% must not fire this


# ── on_quota_low ─────────────────────────────────────────────────────────────


def test_on_quota_low_fires_once_per_crossing(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """Edge triggered: four responses under the threshold, one callback."""

    _responses(
        respx_mock,
        _quota(1000, 500),  # 50% — quiet
        _quota(1000, 100),  # 10% — crossing, fires
        _quota(1000, 50),  # still low — quiet
        _quota(1000, 1),  # still low — quiet
    )
    alerts: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_quota_low(alerts.append)
        for _ in range(4):
            sky.weather.metar("KJFK")

    assert [info.remaining for info in alerts] == [100]


def test_on_quota_low_rearms_after_the_quota_resets(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(
        respx_mock,
        _quota(1000, 50),  # low — fires
        _quota(1000, 1000),  # new window — re-arms
        _quota(1000, 20),  # low again — fires again
    )
    alerts: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_quota_low(alerts.append)
        for _ in range(3):
            sky.weather.metar("KJFK")

    assert [info.remaining for info in alerts] == [50, 20]


def test_on_quota_low_honours_the_threshold(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(respx_mock, _quota(1000, 400), _quota(1000, 400))
    strict: list[RateLimitInfo] = []
    relaxed: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_quota_low(strict.append)  # default 10%
        sky.on_quota_low(relaxed.append, threshold=0.5)
        sky.weather.metar("KJFK")
        sky.weather.metar("KJFK")

    assert strict == []
    assert len(relaxed) == 1


def test_on_quota_low_ignores_responses_it_cannot_rate(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """No ``limit``, no ``remaining`` or a zero ``limit`` → no ratio, no alert."""

    _responses(respx_mock, _quota(None, 5), _quota(1000, None), _quota(0, 0))
    alerts: list[RateLimitInfo] = []
    seen: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        sky.on_quota_low(alerts.append)
        sky.on_rate_limit(seen.append)
        for _ in range(3):
            sky.weather.metar("KJFK")

    assert alerts == []
    assert len(seen) == 3  # on_rate_limit still sees every partial snapshot


def test_on_quota_low_unsubscribe(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    _responses(respx_mock, _quota(1000, 1000), _quota(1000, 10))
    alerts: list[RateLimitInfo] = []

    with _client(sleeper) as sky:
        stop = sky.on_quota_low(alerts.append)
        sky.weather.metar("KJFK")
        stop()
        sky.weather.metar("KJFK")

    assert alerts == []


def test_on_quota_low_rejects_a_nonsense_threshold(sleeper: SleepRecorder) -> None:
    with _client(sleeper) as sky:
        for bad in (0, -0.5, 1.5):
            with pytest.raises(ValueError, match="threshold"):
                sky.on_quota_low(lambda info: None, threshold=bad)


def test_a_raising_quota_callback_warns_and_the_request_survives(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    _responses(respx_mock, _quota(1000, 5))

    def boom(info: RateLimitInfo) -> None:
        raise ValueError("alerting is down")

    with _client(sleeper) as sky:
        sky.on_quota_low(boom)
        with pytest.warns(RuntimeWarning, match="alerting is down"):
            metar = sky.weather.metar("KJFK")

    assert metar.icao == "KJFK"


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_hooks_fire(respx_mock: respx.MockRouter, async_sleeper: Any) -> None:
    _responses(respx_mock, _quota(1000, 900), _quota(1000, 20))
    seen: list[RateLimitInfo] = []
    alerts: list[RateLimitInfo] = []

    async with AsyncSkyLink(
        api_key=TEST_API_KEY, provider=TEST_PROVIDER, sleep=async_sleeper, environ={}
    ) as sky:
        sky.on_rate_limit(seen.append)
        sky.on_quota_low(alerts.append)
        await sky.weather.metar("KJFK")
        await sky.weather.metar("KJFK")

    assert [info.remaining for info in seen] == [900, 20]
    assert [info.remaining for info in alerts] == [20]


async def test_async_callbacks_are_rejected_at_registration(async_sleeper: Any) -> None:
    """The dispatcher never awaits — an ``async def`` hook would be created,
    dropped unawaited, and its body would never run. Fail loudly instead."""

    async def hook(info: RateLimitInfo) -> None:  # pragma: no cover - must never run
        raise AssertionError("never awaited")

    async with AsyncSkyLink(
        api_key=TEST_API_KEY, provider=TEST_PROVIDER, sleep=async_sleeper, environ={}
    ) as sky:
        with pytest.raises(TypeError, match="synchronous"):
            sky.on_rate_limit(hook)
        with pytest.raises(TypeError, match="synchronous"):
            sky.on_quota_low(hook)


def test_sync_client_also_rejects_async_callbacks(sleeper: SleepRecorder) -> None:
    async def hook(info: RateLimitInfo) -> None:  # pragma: no cover - must never run
        raise AssertionError("never awaited")

    with _client(sleeper) as sky, pytest.raises(TypeError, match="synchronous"):
        sky.on_rate_limit(hook)
