"""``sky.poll`` — the same endpoint on a timer, as an iterator.

The API has no streaming and no long polling: watching a flight means asking for
it again every minute, and a live map means re-fetching the ADS-B feed every ten
seconds. Written by hand that loop always ends up the same, and always gets two
things wrong: it dies on the first 429, and it re-renders everything on every
tick because nobody diffed the snapshots. This namespace is that loop, done
once::

    for status in sky.poll.flight_status("BA123"):
        print(status.status)          # only when it actually changed
                                      # stops on its own when the flight lands

    for diff in sky.poll.adsb(bbox="51,-1,52,0.5"):
        for aircraft in diff.appeared:
            add_marker(aircraft)

Rules that hold for both pollers:

* **The first request happens immediately**, with no initial wait — an iterator
  that sleeps a minute before its first value is unusable in a UI.
* ``interval`` is the pause **between** requests, not a schedule: a slow
  response pushes the next one out. No catching up, no bursts.
* **429 and 5xx do not end the loop.** A rate limit waits for ``retry_after``
  (or ``interval`` when the header is missing) and tries again; a 5xx waits
  ``interval``. Both count against ``max_iterations``, so a broken backend
  cannot turn a bounded poll into an unbounded one.
* **401/403/422 and client-side errors propagate.** A wrong key or a malformed
  bbox will not fix itself, and a poller silently retrying it forever is how
  quotas die.
* ``sleep`` is injectable. The default is :func:`time.sleep` (and
  :func:`asyncio.sleep` on the async class); tests pass a recorder and run the
  whole suite in milliseconds.

Structure follows :mod:`skylink_api.resources.weather`: the request builders of
the underlying namespaces are reused verbatim, the pure comparison helpers are
module-level functions shared by both classes, and ``Poll``/``AsyncPoll`` stay
method-for-method identical.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any, cast

from .._exceptions import InternalServerError, RateLimitError
from .._types import BBoxLike, RequestOptions, RequestSpec
from ..models.adsb import AdsbAircraft, AdsbAircraftList
from ..models.flight_status import FlightStatusResponse
from ..models.poll import AdsbDiff
from .adsb import _aircraft_spec
from .flight_status import _flight_status_spec

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = [
    "TERMINAL_FLIGHT_STATUSES",
    "AsyncPoll",
    "Poll",
    "is_terminal_status",
]

#: Substrings that mark a flight as finished for ``until_terminal``.
#:
#: Matching is case-insensitive and **by containment**, not equality, because the
#: status is scraped prose rather than an enum: ``"Landed 14:32"``,
#: ``"landed - on time"`` and ``"Cancelled"`` all occur in the wild. The cost of
#: containment is a theoretical false positive (``"Diverted flight resumed"``);
#: the cost of equality is a poller that never stops, which is worse.
#:
#: Both spellings of "cancelled" are listed: the status is scraped from several
#: sources and the American ``"Canceled"`` (one ``l``) is not a substring of the
#: British ``"Cancelled"``, so one entry would not cover the other.
TERMINAL_FLIGHT_STATUSES: tuple[str, ...] = (
    "landed",
    "arrived",
    "cancelled",
    "canceled",
    "diverted",
)

#: Values the scraper uses for "unknown": the empty string, and the ``"--"`` /
#: ``"--:--"`` placeholders the presentation layer substitutes for it. They are
#: folded to ``None`` before comparing, so a field flapping between two spellings
#: of "no data" is not reported as a change.
_EMPTY_MARKERS = frozenset({"", "-", "--", "---", "--:--", "n/a", "none", "unknown"})


# ── builders / pure helpers ──────────────────────────────────────────────────


def is_terminal_status(status: str | None) -> bool:
    """True when a scraped flight status means the flight is over.

    Case-insensitive containment against :data:`TERMINAL_FLIGHT_STATUSES`, so
    ``"Landed 14:32"`` counts and ``"En Route"`` does not. ``None`` and the
    scraper's empty markers are never terminal — an unknown status is not a
    finished flight.
    """

    if not status:
        return False
    lowered = status.strip().lower()
    return any(marker in lowered for marker in TERMINAL_FLIGHT_STATUSES)


def _clean(value: str | None) -> str | None:
    """Fold the scraper's three spellings of "unknown" into ``None``."""

    if value is None:
        return None
    stripped = value.strip()
    if stripped.lower() in _EMPTY_MARKERS:
        return None
    return stripped


def _status_fingerprint(response: FlightStatusResponse) -> tuple[str | None, ...]:
    """The fields ``changes_only`` compares, normalised.

    Deliberately **not** the whole model. ``FlightStatusResponse`` allows extra
    fields, and the scraped payload carries book-keeping the caller does not care
    about (retrieval timestamps, cache markers); comparing everything would
    report a "change" on every single poll and make ``changes_only`` useless.

    What is compared is what a passenger or an operations screen would react to:

    * ``status`` — the prose state;
    * departure: ``actual_time``, ``actual_date``, ``scheduled_time``,
      ``terminal``, ``gate``;
    * arrival: ``estimated_time``, ``estimated_date``, ``scheduled_time``,
      ``terminal``, ``gate``, ``baggage``.

    Airport names are excluded (they never change for a given flight number, and
    the ``"LHR • London"`` rendering is not stable). Every value is trimmed and
    the ``""``/``"--"``/``"--:--"`` placeholders are folded to ``None``, so the
    presentation layer swapping one for another is not a change either.
    """

    departure = response.departure
    arrival = response.arrival
    return (
        _clean(response.status),
        _clean(departure.actual_time) if departure else None,
        _clean(departure.actual_date) if departure else None,
        _clean(departure.scheduled_time) if departure else None,
        _clean(departure.terminal) if departure else None,
        _clean(departure.gate) if departure else None,
        _clean(arrival.estimated_time) if arrival else None,
        _clean(arrival.estimated_date) if arrival else None,
        _clean(arrival.scheduled_time) if arrival else None,
        _clean(arrival.terminal) if arrival else None,
        _clean(arrival.gate) if arrival else None,
        _clean(arrival.baggage) if arrival else None,
    )


def _snapshot(page: AdsbAircraftList) -> dict[str, AdsbAircraft]:
    """Index a feed page by lower-cased ``icao24``.

    Aircraft without an address are dropped: there is no way to recognise them in
    the next snapshot, so keeping them would report the same aircraft as
    ``appeared`` on every poll. Duplicate addresses (rare, but the feed is a
    union of receivers) keep the last row.
    """

    indexed: dict[str, AdsbAircraft] = {}
    for aircraft in page.aircraft:
        address = aircraft.icao24
        if not address:
            continue
        indexed[address.strip().lower()] = aircraft
    return indexed


def _has_moved(before: AdsbAircraft, after: AdsbAircraft) -> bool:
    """Whether an aircraft counts as "updated" between two snapshots.

    Only **position (latitude/longitude), altitude and ground speed** are
    compared — the four fields a map has to redraw. ``last_seen`` is excluded on
    purpose: it advances with every received message, so comparing it would put
    the entire feed in ``updated`` every ten seconds and make the diff pointless.
    ``track`` and ``vertical_rate`` are excluded as derived jitter, and the
    registry columns (``registration``, ``aircraft_type``) because they describe
    the airframe, not the flight.
    """

    return (
        before.latitude != after.latitude
        or before.longitude != after.longitude
        or before.altitude != after.altitude
        or before.ground_speed != after.ground_speed
    )


def _diff(previous: dict[str, AdsbAircraft] | None, page: AdsbAircraftList) -> AdsbDiff:
    """Build the :class:`AdsbDiff` between the previous snapshot and a fresh page."""

    current = _snapshot(page)
    if previous is None:
        return AdsbDiff(
            appeared=list(current.values()),
            disappeared=[],
            updated=[],
            snapshot=current,
            is_first=True,
        )

    appeared = [aircraft for key, aircraft in current.items() if key not in previous]
    disappeared = [key for key in previous if key not in current]
    updated = [
        aircraft
        for key, aircraft in current.items()
        if key in previous and _has_moved(previous[key], aircraft)
    ]
    return AdsbDiff(
        appeared=appeared,
        disappeared=disappeared,
        updated=updated,
        snapshot=current,
        is_first=False,
    )


def _check_interval(interval: float, max_iterations: int | None) -> None:
    """Reject loop settings that would spin (before any request is sent)."""

    if interval < 0:
        raise ValueError(f"interval must not be negative, got {interval}")
    if max_iterations is not None and max_iterations < 1:
        raise ValueError(f"max_iterations must be at least 1, got {max_iterations}")


def _recover_delay(error: Exception, *, interval: float) -> float | None:
    """How long to wait after ``error``, or ``None`` when it must propagate.

    Survivable: ``429`` (wait for the server's ``Retry-After``, falling back to
    ``interval``) and any ``5xx`` (wait ``interval``). Everything else — 401,
    403, 404, 422, connection failures the transport already retried — is
    returned as "propagate", because retrying it on a timer would only burn
    quota.
    """

    if isinstance(error, RateLimitError):
        return error.retry_after if error.retry_after is not None else interval
    if isinstance(error, InternalServerError):
        return interval
    return None


# ── sync ─────────────────────────────────────────────────────────────────────


class Poll:
    """``sky.poll`` — endpoints on a timer, as blocking iterators.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            for status in sky.poll.flight_status("BA123", interval=60):
                notify(status)

    Both methods return generators, so nothing happens until you iterate and
    ``break`` stops the polling immediately. Argument validation, however, is
    eager: a bad ``interval`` raises straight from the call.
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def flight_status(
        self,
        flight_number: str,
        *,
        interval: float = 60.0,
        changes_only: bool = True,
        until_terminal: bool = True,
        max_iterations: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        request_options: RequestOptions | None = None,
    ) -> Iterator[FlightStatusResponse]:
        """Watch one flight — ``GET /flight_status/{flight_number}`` on a timer.

        The first response is yielded immediately, then one per ``interval``
        until the flight reaches a terminal status (or ``max_iterations``
        requests have been made, or you stop iterating)::

            for status in sky.poll.flight_status("BA123"):
                print(status.status, status.arrival.gate if status.arrival else "")

        Args:
            flight_number: IATA (``"BA123"``) or ICAO (``"BAW123"``) designator.
            interval: Seconds between requests. Sixty is a sensible floor — the
                upstream page this is scraped from does not update faster, and
                shorter intervals only spend quota.
            changes_only: Yield only when something meaningful changed. The
                comparison covers the status prose plus the times, dates,
                terminals, gates and baggage belt of both legs, with ``""`` and
                ``"--"`` folded to "unknown" — see :func:`_status_fingerprint`
                for why the whole object is not compared. The **first** response
                is always yielded.
            until_terminal: Stop once the status reads landed/arrived/cancelled/
                diverted (case-insensitive substring match). The terminal
                response is yielded **before** the loop ends, so the caller
                always sees the final state.
            max_iterations: Hard cap on **requests**, including those that failed
                with 429/5xx. ``None`` (default) means "until terminal or until
                the caller stops".
            sleep: Injection point for the wait. Defaults to :func:`time.sleep`.
            request_options: Per-request overrides applied to every poll.

        Yields:
            :class:`~skylink_api.models.flight_status.FlightStatusResponse`, the
            same shape ``sky.flight_status()`` returns.

        Note:
            With ``until_terminal=True`` and an unknown flight number the status
            is usually ``"Unknown"`` forever, which is *not* terminal — pair it
            with ``max_iterations`` when the number is not trusted.

        Raises:
            ValueError: negative ``interval`` or ``max_iterations`` below 1
                (before any request).
            AuthenticationError: bad key (401) — never retried.
            PermissionDeniedError: plan does not allow the call (403).
            UnprocessableEntityError: malformed flight number (422).
        """

        _check_interval(interval, max_iterations)
        return self._iter_flight_status(
            flight_number,
            interval=interval,
            changes_only=changes_only,
            until_terminal=until_terminal,
            max_iterations=max_iterations,
            sleep=sleep,
            request_options=request_options,
        )

    def _iter_flight_status(
        self,
        flight_number: str,
        *,
        interval: float,
        changes_only: bool,
        until_terminal: bool,
        max_iterations: int | None,
        sleep: Callable[[float], None],
        request_options: RequestOptions | None,
    ) -> Iterator[FlightStatusResponse]:
        spec = _flight_status_spec(flight_number)
        seen: tuple[str | None, ...] | None = None
        iterations = 0
        delay: float | None = None  # None on the first pass: no initial wait.

        while max_iterations is None or iterations < max_iterations:
            if delay is not None:
                sleep(delay)
            delay = interval
            iterations += 1

            try:
                response = cast(FlightStatusResponse, self._client.execute(spec, request_options))
            except Exception as error:
                recovery = _recover_delay(error, interval=interval)
                if recovery is None:
                    raise
                delay = recovery
                continue

            fingerprint = _status_fingerprint(response)
            if not changes_only or fingerprint != seen:
                yield response
            seen = fingerprint

            if until_terminal and is_terminal_status(response.status):
                return

    def adsb(
        self,
        *,
        interval: float = 10.0,
        max_iterations: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        icao24: str | None = None,
        callsign: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius: float | None = None,
        bbox: BBoxLike | None = None,
        min_alt: float | None = None,
        max_alt: float | None = None,
        min_speed: float | None = None,
        max_speed: float | None = None,
        registration: str | None = None,
        airline: str | None = None,
        photos: bool = False,
        limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> Iterator[AdsbDiff]:
        """Watch the live feed — ``GET /adsb/aircraft`` on a timer, as diffs.

        Yields what *changed* rather than the whole feed, which is what a map
        needs: a marker to add, a marker to remove, a marker to move::

            for diff in sky.poll.adsb(bbox="51.0,-1.0,52.0,0.5", interval=10):
                if diff.is_first:
                    draw_all(diff.snapshot.values())
                    continue
                for aircraft in diff.appeared:
                    add_marker(aircraft)
                for address in diff.disappeared:
                    remove_marker(address)
                for aircraft in diff.updated:
                    move_marker(aircraft)

        The first iteration reports the whole feed as ``appeared`` with
        ``is_first=True``. Every later iteration yields a diff **even when it is
        empty** — a tick with nothing in it is information too (the feed is
        alive and unchanged).

        Args:
            interval: Seconds between requests. Ten matches the feed's own
                refresh; below ~5 s you pay quota for duplicate data.
            max_iterations: Hard cap on requests, failed ones included. ``None``
                polls until the caller stops.
            sleep: Injection point for the wait. Defaults to :func:`time.sleep`.
            icao24: Exact 6-hex address filter.
            callsign: Case-insensitive partial callsign match.
            lat: Centre latitude for a radius search (needs ``lon``/``radius``).
            lon: Centre longitude for a radius search.
            radius: Radius in **kilometres**, 0 < radius <= 1000.
            bbox: ``(lat1, lon1, lat2, lon2)`` south-west first, or the
                pre-formatted string. **Use one**: polling the unfiltered feed
                means 9-10k aircraft every tick.
            min_alt: Minimum altitude in feet.
            max_alt: Maximum altitude in feet.
            min_speed: Minimum ground speed in knots.
            max_speed: Maximum ground speed in knots.
            registration: Exact tail number.
            airline: Case-insensitive substring of the operator name.
            photos: Fetch photo URLs. Off by default and best left off here — it
                is slow and only covers the first 50 aircraft of a page.
            limit: Page size. A **truncated** page makes the aircraft beyond the
                cut look like they disappeared, so leave it unset unless the
                filter already bounds the result.
            request_options: Per-request overrides applied to every poll.

        Yields:
            :class:`~skylink_api.models.poll.AdsbDiff`, one per completed
            request. Requests that failed with 429/5xx yield nothing.

        Raises:
            ValueError: negative ``interval`` or ``max_iterations`` below 1
                (before any request).
            BadRequestError: malformed bbox or an incomplete lat/lon/radius trio
                (400) — a configuration error, so it is not survived.
        """

        _check_interval(interval, max_iterations)
        spec = _aircraft_spec(
            icao24=icao24,
            callsign=callsign,
            lat=lat,
            lon=lon,
            radius=radius,
            bbox=bbox,
            min_alt=min_alt,
            max_alt=max_alt,
            min_speed=min_speed,
            max_speed=max_speed,
            registration=registration,
            airline=airline,
            photos=photos,
            limit=limit,
        )
        return self._iter_adsb(
            spec=spec,
            interval=interval,
            max_iterations=max_iterations,
            sleep=sleep,
            request_options=request_options,
        )

    def _iter_adsb(
        self,
        *,
        spec: RequestSpec,
        interval: float,
        max_iterations: int | None,
        sleep: Callable[[float], None],
        request_options: RequestOptions | None,
    ) -> Iterator[AdsbDiff]:
        previous: dict[str, AdsbAircraft] | None = None
        iterations = 0
        delay: float | None = None

        while max_iterations is None or iterations < max_iterations:
            if delay is not None:
                sleep(delay)
            delay = interval
            iterations += 1

            try:
                page = cast(AdsbAircraftList, self._client.execute(spec, request_options))
            except Exception as error:
                recovery = _recover_delay(error, interval=interval)
                if recovery is None:
                    raise
                delay = recovery
                continue

            diff = _diff(previous, page)
            previous = diff.snapshot
            yield diff


# ── async ────────────────────────────────────────────────────────────────────


class AsyncPoll:
    """``sky.poll`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Poll` — same arguments, same stopping rules, same diffing;
    the methods return async iterators and ``sleep`` is awaited::

        async with AsyncSkyLink(api_key="...") as sky:
            async for diff in sky.poll.adsb(bbox="51,-1,52,0.5"):
                render(diff)

    Note that the methods themselves are **not** coroutines: call them without
    ``await`` and drive the result with ``async for``.
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    def flight_status(
        self,
        flight_number: str,
        *,
        interval: float = 60.0,
        changes_only: bool = True,
        until_terminal: bool = True,
        max_iterations: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        request_options: RequestOptions | None = None,
    ) -> AsyncIterator[FlightStatusResponse]:
        """Watch one flight — ``GET /flight_status/{flight_number}`` on a timer.

        Async twin of :meth:`Poll.flight_status`; see it for the full argument
        documentation::

            async for status in sky.poll.flight_status("BA123"):
                await notify(status)

        Args:
            flight_number: IATA or ICAO designator.
            interval: Seconds between requests.
            changes_only: Yield only on a meaningful change (first response
                always yielded).
            until_terminal: Stop after landed/arrived/cancelled/diverted, having
                yielded that response first.
            max_iterations: Hard cap on requests, failed ones included.
            sleep: Awaitable wait. Defaults to :func:`asyncio.sleep`.
            request_options: Per-request overrides applied to every poll.

        Raises:
            ValueError: negative ``interval`` or ``max_iterations`` below 1
                (before any request).
        """

        _check_interval(interval, max_iterations)
        return self._iter_flight_status(
            flight_number,
            interval=interval,
            changes_only=changes_only,
            until_terminal=until_terminal,
            max_iterations=max_iterations,
            sleep=sleep,
            request_options=request_options,
        )

    async def _iter_flight_status(
        self,
        flight_number: str,
        *,
        interval: float,
        changes_only: bool,
        until_terminal: bool,
        max_iterations: int | None,
        sleep: Callable[[float], Awaitable[None]],
        request_options: RequestOptions | None,
    ) -> AsyncIterator[FlightStatusResponse]:
        spec = _flight_status_spec(flight_number)
        seen: tuple[str | None, ...] | None = None
        iterations = 0
        delay: float | None = None

        while max_iterations is None or iterations < max_iterations:
            if delay is not None:
                await sleep(delay)
            delay = interval
            iterations += 1

            try:
                result: Any = await self._client.execute(spec, request_options)
            except Exception as error:
                recovery = _recover_delay(error, interval=interval)
                if recovery is None:
                    raise
                delay = recovery
                continue

            response = cast(FlightStatusResponse, result)
            fingerprint = _status_fingerprint(response)
            if not changes_only or fingerprint != seen:
                yield response
            seen = fingerprint

            if until_terminal and is_terminal_status(response.status):
                return

    def adsb(
        self,
        *,
        interval: float = 10.0,
        max_iterations: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        icao24: str | None = None,
        callsign: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius: float | None = None,
        bbox: BBoxLike | None = None,
        min_alt: float | None = None,
        max_alt: float | None = None,
        min_speed: float | None = None,
        max_speed: float | None = None,
        registration: str | None = None,
        airline: str | None = None,
        photos: bool = False,
        limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> AsyncIterator[AdsbDiff]:
        """Watch the live feed — ``GET /adsb/aircraft`` on a timer, as diffs.

        Async twin of :meth:`Poll.adsb`; see it for the full argument
        documentation and the diff semantics::

            async for diff in sky.poll.adsb(bbox="51.0,-1.0,52.0,0.5"):
                await render(diff)

        Args:
            interval: Seconds between requests.
            max_iterations: Hard cap on requests, failed ones included.
            sleep: Awaitable wait. Defaults to :func:`asyncio.sleep`.
            icao24: Exact 6-hex address filter.
            callsign: Case-insensitive partial callsign match.
            lat: Centre latitude for a radius search.
            lon: Centre longitude for a radius search.
            radius: Radius in kilometres.
            bbox: Bounding box; use a filter, the unfiltered feed is 9-10k rows.
            min_alt: Minimum altitude in feet.
            max_alt: Maximum altitude in feet.
            min_speed: Minimum ground speed in knots.
            max_speed: Maximum ground speed in knots.
            registration: Exact tail number.
            airline: Case-insensitive substring of the operator name.
            photos: Fetch photo URLs (slow; off by default).
            limit: Page size — a truncated page looks like aircraft disappearing.
            request_options: Per-request overrides applied to every poll.

        Raises:
            ValueError: negative ``interval`` or ``max_iterations`` below 1
                (before any request).
        """

        _check_interval(interval, max_iterations)
        spec = _aircraft_spec(
            icao24=icao24,
            callsign=callsign,
            lat=lat,
            lon=lon,
            radius=radius,
            bbox=bbox,
            min_alt=min_alt,
            max_alt=max_alt,
            min_speed=min_speed,
            max_speed=max_speed,
            registration=registration,
            airline=airline,
            photos=photos,
            limit=limit,
        )
        return self._iter_adsb(
            spec=spec,
            interval=interval,
            max_iterations=max_iterations,
            sleep=sleep,
            request_options=request_options,
        )

    async def _iter_adsb(
        self,
        *,
        spec: RequestSpec,
        interval: float,
        max_iterations: int | None,
        sleep: Callable[[float], Awaitable[None]],
        request_options: RequestOptions | None,
    ) -> AsyncIterator[AdsbDiff]:
        previous: dict[str, AdsbAircraft] | None = None
        iterations = 0
        delay: float | None = None

        while max_iterations is None or iterations < max_iterations:
            if delay is not None:
                await sleep(delay)
            delay = interval
            iterations += 1

            try:
                result: Any = await self._client.execute(spec, request_options)
            except Exception as error:
                recovery = _recover_delay(error, interval=interval)
                if recovery is None:
                    raise
                delay = recovery
                continue

            page = cast(AdsbAircraftList, result)
            diff = _diff(previous, page)
            previous = diff.snapshot
            yield diff
