"""Bounded-concurrency primitives and the batch-result readers (helpers §7).

Pure functions: no client, no network. The concurrency bound is asserted by
watching how many calls are in flight at once, not by timing — a timing based
test on CI is a flake generator.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from skylink_api._exceptions import NotFoundError, RateLimitError, SkyLinkError
from skylink_api.helpers.batch import (
    DEFAULT_CONCURRENCY,
    amap_concurrent,
    failures,
    map_concurrent,
    raise_for_errors,
    successes,
)


class InFlightTracker:
    """Counts how many calls overlap, so a concurrency bound can be asserted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def leave(self) -> None:
        with self._lock:
            self.current -= 1


# ── map_concurrent ───────────────────────────────────────────────────────────


def test_default_concurrency_is_five() -> None:
    """RapidAPI quotas are strict; both SDKs default to 5."""

    assert DEFAULT_CONCURRENCY == 5


def test_map_concurrent_keeps_input_order() -> None:
    """Slowest first: the result must still line up with the input."""

    def work(item: int) -> int:
        time.sleep(0.02 / (item + 1))
        return item * 10

    assert map_concurrent(work, [0, 1, 2, 3, 4], concurrency=5) == [0, 10, 20, 30, 40]


def test_map_concurrent_returns_exceptions_instead_of_raising() -> None:
    def work(item: str) -> str:
        if item == "bad":
            raise NotFoundError("no such thing")
        return item.upper()

    results = map_concurrent(work, ["a", "bad", "b"])

    assert results[0] == "A"
    assert isinstance(results[1], NotFoundError)
    assert results[2] == "B"


def test_map_concurrent_respects_the_bound() -> None:
    tracker = InFlightTracker()

    def work(item: int) -> int:
        tracker.enter()
        time.sleep(0.01)
        tracker.leave()
        return item

    map_concurrent(work, list(range(12)), concurrency=3)

    assert tracker.peak <= 3


def test_map_concurrent_on_an_empty_input_starts_no_pool() -> None:
    calls: list[object] = []

    assert map_concurrent(calls.append, []) == []
    assert calls == []


@pytest.mark.parametrize("concurrency", [0, -1])
def test_map_concurrent_rejects_a_useless_bound(concurrency: int) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        map_concurrent(str, ["a"], concurrency=concurrency)


def test_map_concurrent_does_not_swallow_base_exceptions() -> None:
    """Ctrl-C must still stop a batch."""

    def work(item: int) -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        map_concurrent(work, [1], concurrency=1)


# ── amap_concurrent ──────────────────────────────────────────────────────────


async def test_amap_concurrent_keeps_input_order() -> None:
    async def work(item: int) -> int:
        await asyncio.sleep(0.02 / (item + 1))
        return item * 10

    assert await amap_concurrent(work, [0, 1, 2, 3, 4]) == [0, 10, 20, 30, 40]


async def test_amap_concurrent_returns_exceptions_instead_of_raising() -> None:
    async def work(item: str) -> str:
        if item == "bad":
            raise RateLimitError("slow down")
        return item.upper()

    results = await amap_concurrent(work, ["a", "bad"])

    assert results[0] == "A"
    assert isinstance(results[1], RateLimitError)


async def test_amap_concurrent_respects_the_bound() -> None:
    tracker = InFlightTracker()

    async def work(item: int) -> int:
        tracker.enter()
        await asyncio.sleep(0.01)
        tracker.leave()
        return item

    await amap_concurrent(work, list(range(12)), concurrency=3)

    assert tracker.peak <= 3


async def test_amap_concurrent_on_an_empty_input() -> None:
    async def work(item: int) -> int:  # pragma: no cover - never called
        return item

    assert await amap_concurrent(work, []) == []


@pytest.mark.parametrize("concurrency", [0, -1])
async def test_amap_concurrent_rejects_a_useless_bound(concurrency: int) -> None:
    async def work(item: int) -> int:  # pragma: no cover - never called
        return item

    with pytest.raises(ValueError, match="concurrency"):
        await amap_concurrent(work, [1], concurrency=concurrency)


async def test_amap_concurrent_does_not_swallow_cancellation() -> None:
    async def work(item: int) -> int:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await amap_concurrent(work, [1], concurrency=1)


# ── result readers ───────────────────────────────────────────────────────────


RESULTS: dict[str, Any] = {
    "KJFK": "metar-kjfk",
    "ZZZZ": NotFoundError("Airport not found"),
    "EGLL": "metar-egll",
}


def test_successes_drops_the_errors_and_keeps_order() -> None:
    assert successes(RESULTS) == {"KJFK": "metar-kjfk", "EGLL": "metar-egll"}
    assert list(successes(RESULTS)) == ["KJFK", "EGLL"]


def test_failures_keeps_the_requested_keys() -> None:
    misses = failures(RESULTS)
    assert list(misses) == ["ZZZZ"]
    assert isinstance(misses["ZZZZ"], NotFoundError)


def test_raise_for_errors_raises_the_first_error_in_key_order() -> None:
    results: dict[str, Any] = {
        "A": "ok",
        "B": NotFoundError("first"),
        "C": RateLimitError("second"),
    }

    with pytest.raises(SkyLinkError) as excinfo:
        raise_for_errors(results)

    # Key order, not completion order — the failure is reproducible.
    assert str(excinfo.value) == "first"


def test_raise_for_errors_passes_a_clean_batch_through() -> None:
    results: dict[str, Any] = {"A": 1, "B": 2}
    assert raise_for_errors(results) == {"A": 1, "B": 2}
