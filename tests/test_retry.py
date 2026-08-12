"""Retry policy: which statuses, which methods, how long we wait."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
import respx

from conftest import AsyncSleepRecorder, SleepRecorder
from skylink_api._base_client import BaseClient
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._config import resolve_config
from skylink_api._constants import INITIAL_RETRY_DELAY, MAX_RETRY_DELAY
from skylink_api._exceptions import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

RETRYABLE = [429, 500, 502, 503, 504]
NON_RETRYABLE = [400, 401, 403, 404, 422]


def _client(sleeper: SleepRecorder, **kwargs: object) -> SkyLink:
    return SkyLink(api_key="k", sleep=sleeper, environ={}, **kwargs)  # type: ignore[arg-type]


# ── which statuses are retried ───────────────────────────────────────────────


@pytest.mark.parametrize("status", RETRYABLE)
def test_retryable_statuses_are_retried_then_succeed(
    status: int, respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(
        side_effect=[
            httpx.Response(status, json={"detail": "later"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(sleeper) as sky:
        assert sky.request("GET", "/health") == {"ok": True}

    assert route.call_count == 2
    assert sleeper.count == 1


@pytest.mark.parametrize("status", NON_RETRYABLE)
def test_non_retryable_statuses_fail_immediately(
    status: int, respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(status, json={"detail": "no"}))
    with _client(sleeper) as sky, pytest.raises(Exception) as excinfo:
        sky.request("GET", "/health")

    assert route.call_count == 1
    assert sleeper.count == 0
    assert getattr(excinfo.value, "status_code", None) == status


def test_retries_are_exhausted_and_the_last_error_is_raised(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(503, json={"detail": "down"}))
    with _client(sleeper) as sky, pytest.raises(InternalServerError) as excinfo:
        sky.request("GET", "/routes/pairs")

    assert route.call_count == 4  # initial + 3 retries
    assert sleeper.count == 3
    assert excinfo.value.status_code == 503
    assert excinfo.value.message == "down"


def test_max_retries_zero_disables_retrying(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(500, json={"detail": "boom"}))
    with _client(sleeper, max_retries=0) as sky, pytest.raises(InternalServerError):
        sky.request("GET", "/health")

    assert route.call_count == 1
    assert sleeper.count == 0


def test_per_request_max_retries_override(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(500, json={"detail": "boom"}))
    with _client(sleeper) as sky, pytest.raises(InternalServerError):
        sky.request("GET", "/health", options={"max_retries": 1})

    assert route.call_count == 2
    assert sleeper.count == 1


def test_negative_per_request_max_retries_rejected(sleeper: SleepRecorder) -> None:
    with _client(sleeper) as sky, pytest.raises(ValueError, match="max_retries must be >= 0"):
        sky.request("GET", "/health", options={"max_retries": -1})


# ── unsafe methods ───────────────────────────────────────────────────────────


def test_post_is_not_retried_on_500(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    """Replaying POST /webhooks after a 5xx could create a duplicate subscription."""

    route = respx_mock.route().mock(return_value=httpx.Response(500, json={"detail": "boom"}))
    with _client(sleeper) as sky, pytest.raises(InternalServerError):
        sky.request("POST", "/webhooks", json_body={"url": "https://example.com/h"})

    assert route.call_count == 1
    assert sleeper.count == 0


def test_post_is_retried_on_429(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    """A 429 never reached the handler, so replaying is safe."""

    route = respx_mock.route().mock(
        side_effect=[
            httpx.Response(429, json={"detail": "slow down"}),
            httpx.Response(201, json={"id": "wh_1"}),
        ]
    )
    with _client(sleeper) as sky:
        created = sky.request("POST", "/webhooks", json_body={"url": "https://example.com/h"})

    assert created == {"id": "wh_1"}
    assert route.call_count == 2
    assert sleeper.count == 1


def test_post_is_not_retried_on_transport_error(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(side_effect=httpx.ConnectError)
    with _client(sleeper) as sky, pytest.raises(APIConnectionError):
        sky.request("POST", "/webhooks", json_body={"url": "https://example.com/h"})

    assert route.call_count == 1
    assert sleeper.count == 0


# ── transport failures ───────────────────────────────────────────────────────


def test_connection_errors_are_retried_then_wrapped(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(side_effect=httpx.ConnectError)
    with _client(sleeper) as sky, pytest.raises(APIConnectionError) as excinfo:
        sky.request("GET", "/health")

    assert route.call_count == 4
    assert sleeper.count == 3
    assert not isinstance(excinfo.value, APITimeoutError)


def test_timeouts_raise_api_timeout_error(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(side_effect=httpx.ReadTimeout)
    with _client(sleeper, max_retries=1) as sky, pytest.raises(APITimeoutError):
        sky.request("GET", "/health")

    assert route.call_count == 2


def test_connection_error_recovers_on_retry(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(
        side_effect=[httpx.ConnectError("reset"), httpx.Response(200, json={"ok": True})]
    )
    with _client(sleeper) as sky:
        assert sky.request("GET", "/health") == {"ok": True}

    assert route.call_count == 2


# ── backoff ──────────────────────────────────────────────────────────────────


def _base_client() -> BaseClient:
    return BaseClient(resolve_config(api_key="k", environ={}))


def test_full_jitter_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(random, "random", lambda: 1.0)
    client = _base_client()
    delays = [client._retry_delay(attempt) for attempt in range(6)]
    assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert max(delays) == MAX_RETRY_DELAY


def test_full_jitter_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(random, "random", lambda: 0.0)
    client = _base_client()
    assert [client._retry_delay(attempt) for attempt in range(4)] == [0.0, 0.0, 0.0, 0.0]


def test_full_jitter_stays_within_bounds() -> None:
    client = _base_client()
    for attempt in range(8):
        ceiling = min(MAX_RETRY_DELAY, INITIAL_RETRY_DELAY * (2**attempt))
        for _ in range(50):
            delay = client._retry_delay(attempt)
            assert 0.0 <= delay <= ceiling


def test_backoff_is_used_between_attempts(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(random, "random", lambda: 1.0)
    respx_mock.route().mock(return_value=httpx.Response(500, json={"detail": "boom"}))
    with _client(sleeper) as sky, pytest.raises(InternalServerError):
        sky.request("GET", "/health")

    assert sleeper.calls == [0.5, 1.0, 2.0]


# ── Retry-After ──────────────────────────────────────────────────────────────


def test_retry_after_seconds_wins_over_backoff(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(random, "random", lambda: 1.0)
    respx_mock.route().mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": "slow"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(sleeper) as sky:
        sky.request("GET", "/health")

    assert sleeper.calls == [7.0]


def test_retry_after_http_date(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=5)
    respx_mock.route().mock(
        side_effect=[
            httpx.Response(
                503, headers={"Retry-After": format_datetime(when, usegmt=True)}, json={}
            ),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(sleeper) as sky:
        sky.request("GET", "/health")

    assert sleeper.count == 1
    assert 3.0 <= sleeper.calls[0] <= 5.0


def test_retry_after_is_capped_at_60_seconds(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    respx_mock.route().mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3600"}, json={}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(sleeper) as sky:
        sky.request("GET", "/health")

    assert sleeper.calls == [60.0]


def test_retry_after_past_date_is_clamped_to_zero(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    when = datetime.now(timezone.utc) - timedelta(seconds=30)
    respx_mock.route().mock(
        side_effect=[
            httpx.Response(
                429, headers={"Retry-After": format_datetime(when, usegmt=True)}, json={}
            ),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(sleeper) as sky:
        sky.request("GET", "/health")

    assert sleeper.calls == [0.0]


def test_garbage_retry_after_falls_back_to_backoff(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(random, "random", lambda: 1.0)
    respx_mock.route().mock(
        side_effect=[
            httpx.Response(500, headers={"Retry-After": "soon-ish"}, json={}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(sleeper) as sky:
        sky.request("GET", "/health")

    assert sleeper.calls == [0.5]


def test_rate_limit_error_after_exhausted_retries_keeps_retry_after(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    respx_mock.route().mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"}, json={"detail": "quota"})
    )
    with _client(sleeper, max_retries=1) as sky, pytest.raises(RateLimitError) as excinfo:
        sky.request("GET", "/health")

    assert excinfo.value.retry_after == 12.0
    assert sleeper.calls == [12.0]


# ── error classes survive the retry loop ─────────────────────────────────────


def test_bad_request_is_not_retried_even_with_retry_after(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(
        return_value=httpx.Response(400, headers={"Retry-After": "1"}, json={"detail": "bad icao"})
    )
    with _client(sleeper) as sky, pytest.raises(BadRequestError):
        sky.request("GET", "/airports/search")

    assert route.call_count == 1


# ── async mirror ─────────────────────────────────────────────────────────────


async def test_async_retry_loop(
    respx_mock: respx.MockRouter, async_sleeper: AsyncSleepRecorder
) -> None:
    route = respx_mock.route().mock(
        side_effect=[
            httpx.Response(503, json={"detail": "warming up"}),
            httpx.Response(503, json={"detail": "warming up"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with AsyncSkyLink(api_key="k", sleep=async_sleeper, environ={}) as sky:
        assert await sky.request("GET", "/routes/pairs") == {"ok": True}

    assert route.call_count == 3
    assert async_sleeper.count == 2


async def test_async_retries_exhausted(
    respx_mock: respx.MockRouter, async_sleeper: AsyncSleepRecorder
) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(429, json={"detail": "quota"}))
    async with AsyncSkyLink(api_key="k", sleep=async_sleeper, environ={}, max_retries=2) as sky:
        with pytest.raises(RateLimitError):
            await sky.request("GET", "/health")

    assert route.call_count == 3
    assert async_sleeper.count == 2


async def test_async_post_not_retried_on_500(
    respx_mock: respx.MockRouter, async_sleeper: AsyncSleepRecorder
) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(500, json={"detail": "boom"}))
    async with AsyncSkyLink(api_key="k", sleep=async_sleeper, environ={}) as sky:
        with pytest.raises(InternalServerError):
            await sky.request("POST", "/webhooks", json_body={"url": "https://e.com/h"})

    assert route.call_count == 1
    assert async_sleeper.count == 0
