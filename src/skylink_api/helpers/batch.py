"""Bounded-concurrency fan-out, and the two ways of reading its results.

The API has no batch endpoint: "give me the METAR of these forty airports" is
forty requests. Doing them one at a time is slow, doing them all at once burns
through a RapidAPI quota and earns a 429 — so everything here runs a bounded
number of calls in parallel and, crucially, **keeps going after a failure**.
A single unknown ICAO must not lose the other thirty-nine answers.

Two layers:

* :func:`map_concurrent` / :func:`amap_concurrent` — the primitives. They apply a
  function to every item with at most ``concurrency`` in flight and return one
  entry per input, **in input order**, where a failed call is the exception
  object itself rather than a raised error.
* :func:`successes`, :func:`failures`, :func:`raise_for_errors` — for reading the
  ``dict[str, T | SkyLinkError]`` that :class:`~skylink_api.resources.batch.Batch`
  produces, without writing the same ``isinstance`` loop in every script.

``concurrency`` defaults to 5 everywhere: marketplace quotas are per-second as
well as per-month, and 5 is the value that has never provoked a 429 in testing.

Only :class:`Exception` is captured. ``KeyboardInterrupt`` and
``asyncio.CancelledError`` derive from :class:`BaseException` and still stop the
whole batch, which is what a Ctrl-C is supposed to do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from .._exceptions import SkyLinkError

__all__ = [
    "DEFAULT_CONCURRENCY",
    "amap_concurrent",
    "failures",
    "map_concurrent",
    "raise_for_errors",
    "successes",
]

#: How many requests are allowed in flight at once by default. Deliberately low —
#: RapidAPI quotas are strict and a burst is answered with 429, not with data.
DEFAULT_CONCURRENCY = 5

_ItemT = TypeVar("_ItemT")
_ResultT = TypeVar("_ResultT")
_ValueT = TypeVar("_ValueT")


def _check_concurrency(concurrency: int) -> None:
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")


def map_concurrent(
    func: Callable[[_ItemT], _ResultT],
    items: Iterable[_ItemT],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[_ResultT | Exception]:
    """Call ``func`` on every item from a thread pool, at most ``concurrency`` at a time.

    Results come back **in input order**, one per item, regardless of the order
    the calls finished in. A call that raised contributes its exception object
    instead of a result — nothing is raised out of this function, so one failure
    cannot cancel the rest of the batch::

        outcomes = map_concurrent(sky.weather.metar, ["KJFK", "EGLL", "ZZZZ"])

    Threads are the right tool here because the work is a blocking HTTP call:
    :class:`httpx.Client` is thread safe and holds a connection pool, so the SDK
    client can be shared across the pool as is.

    Args:
        func: What to run per item. Must be safe to call from several threads.
        items: Any iterable; it is materialised so the order can be restored.
        concurrency: Maximum simultaneous calls. Must be >= 1.

    Raises:
        ValueError: ``concurrency`` below 1.
    """

    _check_concurrency(concurrency)
    materialised = list(items)
    if not materialised:
        return []

    def run(item: _ItemT) -> _ResultT | Exception:
        try:
            return func(item)
        # Broad on purpose: the whole point is that one failure does not
        # cancel the other calls. BaseException still propagates.
        except Exception as err:
            return err

    with ThreadPoolExecutor(max_workers=min(concurrency, len(materialised))) as executor:
        return list(executor.map(run, materialised))


async def amap_concurrent(
    func: Callable[[_ItemT], Awaitable[_ResultT]],
    items: Iterable[_ItemT],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[_ResultT | Exception]:
    """Async twin of :func:`map_concurrent`, bounded by an :class:`asyncio.Semaphore`.

    Same contract: input order preserved, failures returned rather than raised,
    at most ``concurrency`` coroutines running at once::

        outcomes = await amap_concurrent(sky.weather.metar, ["KJFK", "EGLL"])

    Cancellation is *not* swallowed — ``asyncio.CancelledError`` is a
    ``BaseException`` and propagates, so cancelling the surrounding task still
    tears the batch down.

    Args:
        func: Coroutine function to run per item.
        items: Any iterable; materialised so the order can be restored.
        concurrency: Maximum simultaneous calls. Must be >= 1.

    Raises:
        ValueError: ``concurrency`` below 1.
    """

    _check_concurrency(concurrency)
    materialised = list(items)
    if not materialised:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def run(item: _ItemT) -> _ResultT | Exception:
        async with semaphore:
            try:
                return await func(item)
            # Broad on purpose — see map_concurrent.
            except Exception as err:
                return err

    return list(await asyncio.gather(*(run(item) for item in materialised)))


def successes(results: Mapping[str, _ValueT | SkyLinkError]) -> dict[str, _ValueT]:
    """The entries that worked, with the error type dropped from the annotation.

    ``sky.batch.metars()`` answers ``dict[str, Metar | SkyLinkError]``; this is
    the "just give me the data" view::

        reports = successes(sky.batch.metars(icaos))
        coldest = min(reports.values(), key=lambda m: m.parsed.temperature)

    Key order follows the input dict.
    """

    return {key: value for key, value in results.items() if not isinstance(value, SkyLinkError)}


def failures(results: Mapping[str, _ValueT | SkyLinkError]) -> dict[str, SkyLinkError]:
    """The entries that failed, keyed exactly as they were requested.

    Use it to report or retry the misses::

        for icao, error in failures(reports).items():
            log.warning("%s: %s", icao, error)
    """

    return {key: value for key, value in results.items() if isinstance(value, SkyLinkError)}


def raise_for_errors(results: Mapping[str, _ValueT | SkyLinkError]) -> dict[str, _ValueT]:
    """Return the results, or raise the **first** error in key order.

    For pipelines where a partial answer is not an answer. Because batch results
    keep their input order, "first" is the first failing item you asked for, not
    whichever call happened to finish first — the error is therefore reproducible.

    Raises:
        SkyLinkError: the first stored error, unmodified (so its ``status_code``
            and body survive).
    """

    for value in results.values():
        if isinstance(value, SkyLinkError):
            raise value
    return successes(results)
