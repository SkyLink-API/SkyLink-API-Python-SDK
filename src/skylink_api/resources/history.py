"""Archived ADS-B: flights, tracks, positions and airport traffic.

The backend mounts two copies of the same six endpoints, one per data plan, and
the plan is part of the **path** rather than a parameter::

    /ultra/history/flights   90-day window,  1 000 flights / 5 000 positions max
    /mega/history/flights   365-day window,  2 000 flights / 10 000 positions max

So every method here takes an optional ``plan``. Resolution order is
``plan`` argument → the client's ``history_plan`` → ``"ultra"``; see
:func:`resolve_plan`. Calling a plan the API key is not subscribed to is a
``403`` from the gateway, not a client-side error.

:meth:`History.positions` additionally dispatches on the *shape* of its argument:
six hex characters are treated as an ICAO24 address, anything else as a
registration, which the API resolves to an address server-side. Use
:meth:`History.positions_by_icao24` or :meth:`History.positions_by_registration`
when the identifier type is known and the guess must not be made — a registration
like ``"N12345"`` is not hex, but ``"ABC123"`` is ambiguous and would be read as
an address.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, cast

from .._constants import DEFAULT_HISTORY_PLAN
from .._qs import format_history_datetime
from .._types import DateLike, HistoryPlan, RequestOptions, RequestSpec
from ..models.history import (
    HistoryAirportTrafficResponse,
    HistoryFlight,
    HistoryFlightsResponse,
    HistoryPositionsResponse,
    HistoryTrackResponse,
)

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = [
    "DEFAULT_ITER_WINDOW_DAYS",
    "PLAN_FLIGHT_LIMITS",
    "PLAN_WINDOW_DAYS",
    "AsyncHistory",
    "History",
    "is_icao24",
    "resolve_plan",
]

#: Traffic direction filter for :meth:`History.airport_traffic`: departures,
#: arrivals, or both (the default).
TrafficDirection = Literal["dep", "arr", "both"]

#: Longest ``[start, end]`` range a **single** request may cover, per plan —
#: ``_MAX_DAYS`` in the backend's ``routers/v31/history_{ultra,mega}.py``. Asking
#: for more is a ``422``, not a truncated answer. :meth:`History.iter_flights`
#: exists precisely to spend several requests instead.
PLAN_WINDOW_DAYS: dict[str, int] = {"ultra": 90, "mega": 365}

#: Largest ``limit`` ``/{plan}/history/flights`` accepts (its default is 100).
PLAN_FLIGHT_LIMITS: dict[str, int] = {"ultra": 1000, "mega": 2000}

#: Default slice width of :meth:`History.iter_flights`. A week is small enough
#: that a busy airport pair stays under the per-request row cap and large enough
#: that a 90-day walk is 13 requests rather than 90.
DEFAULT_ITER_WINDOW_DAYS = 7

_ICAO24_RE = re.compile(r"^[0-9a-fA-F]{6}$")


def is_icao24(ident: str) -> bool:
    """True when ``ident`` looks like a 6-hex ICAO24 transponder address.

    This is what :meth:`History.positions` dispatches on. Note the overlap with
    registrations: ``"ABC123"`` is a valid hex string *and* a plausible tail
    number, and the address wins.
    """

    return bool(_ICAO24_RE.match(ident))


def resolve_plan(plan: HistoryPlan | None, client_plan: str | None = None) -> str:
    """Pick the ``/{plan}/history`` prefix.

    Per-call ``plan`` beats the client's ``history_plan``, which beats the
    ``"ultra"`` default.
    """

    return plan or client_plan or DEFAULT_HISTORY_PLAN


def _base(plan: str) -> str:
    return f"/{plan}/history"


def _window(start: DateLike | None, end: DateLike | None) -> dict[str, str | None]:
    """Serialise the ``start``/``end`` pair to ISO 8601.

    Both default to ``None`` — omitting them lets the API apply its own window
    (the last 24 hours), which is not the same as sending the current time.
    """

    return {
        "start": format_history_datetime(start) if start is not None else None,
        "end": format_history_datetime(end) if end is not None else None,
    }


# ── builders ─────────────────────────────────────────────────────────────────


def _check_flight_filters(
    *,
    icao24: str | None,
    registration: str | None,
    callsign: str | None,
    departure_icao: str | None,
    arrival_icao: str | None,
    caller: str = "history.flights()",
) -> None:
    """Refuse an unfiltered flight search before it costs a request.

    The endpoint answers ``422 At least one of icao24, registration, callsign,
    departure_icao, arrival_icao must be provided``; checking here spends no
    quota and gives the caller a plain :class:`ValueError` instead.
    """

    if not any((icao24, registration, callsign, departure_icao, arrival_icao)):
        raise ValueError(
            f"{caller} needs at least one filter: "
            "icao24, registration, callsign, departure_icao or arrival_icao"
        )


def _flights_spec(
    *,
    plan: str,
    start: DateLike | None = None,
    end: DateLike | None = None,
    icao24: str | None = None,
    registration: str | None = None,
    callsign: str | None = None,
    departure_icao: str | None = None,
    arrival_icao: str | None = None,
    limit: int | None = None,
) -> RequestSpec:
    """``GET /{plan}/history/flights``.

    At least one of the five identifiers is required — see
    :func:`_check_flight_filters`.
    """

    _check_flight_filters(
        icao24=icao24,
        registration=registration,
        callsign=callsign,
        departure_icao=departure_icao,
        arrival_icao=arrival_icao,
    )

    return RequestSpec(
        method="GET",
        path=f"{_base(plan)}/flights",
        query={
            **_window(start, end),
            "icao24": icao24.lower() if icao24 else None,
            "registration": registration,
            "callsign": callsign,
            "departure_icao": departure_icao,
            "arrival_icao": arrival_icao,
            "limit": limit,
        },
        cast_to=HistoryFlightsResponse,
    )


def _flight_spec(flight_id: str, *, plan: str) -> RequestSpec:
    """``GET /{plan}/history/flight/{flight_id}``."""

    return RequestSpec(
        method="GET",
        path=f"{_base(plan)}/flight/{flight_id}",
        cast_to=HistoryFlight,
    )


def _track_spec(flight_id: str, *, plan: str, limit: int | None = None) -> RequestSpec:
    """``GET /{plan}/history/flight/{flight_id}/track``."""

    return RequestSpec(
        method="GET",
        path=f"{_base(plan)}/flight/{flight_id}/track",
        query={"limit": limit},
        cast_to=HistoryTrackResponse,
    )


def _positions_by_icao24_spec(
    icao24: str,
    *,
    plan: str,
    start: DateLike | None = None,
    end: DateLike | None = None,
    limit: int | None = None,
) -> RequestSpec:
    """``GET /{plan}/history/positions/{icao24}`` — address lower-cased."""

    return RequestSpec(
        method="GET",
        path=f"{_base(plan)}/positions/{icao24.lower()}",
        query={**_window(start, end), "limit": limit},
        cast_to=HistoryPositionsResponse,
    )


def _positions_by_registration_spec(
    registration: str,
    *,
    plan: str,
    start: DateLike | None = None,
    end: DateLike | None = None,
    limit: int | None = None,
) -> RequestSpec:
    """``GET /{plan}/history/positions/registration/{registration}``."""

    return RequestSpec(
        method="GET",
        path=f"{_base(plan)}/positions/registration/{registration}",
        query={**_window(start, end), "limit": limit},
        cast_to=HistoryPositionsResponse,
    )


def _positions_spec(
    ident: str,
    *,
    plan: str,
    start: DateLike | None = None,
    end: DateLike | None = None,
    limit: int | None = None,
) -> RequestSpec:
    """Dispatch ``ident`` onto one of the two positions routes."""

    builder = _positions_by_icao24_spec if is_icao24(ident) else _positions_by_registration_spec
    return builder(ident, plan=plan, start=start, end=end, limit=limit)


def _airport_traffic_spec(
    icao: str,
    *,
    plan: str,
    direction: TrafficDirection = "both",
    start: DateLike | None = None,
    end: DateLike | None = None,
    limit: int | None = None,
) -> RequestSpec:
    """``GET /{plan}/history/airport/{icao}/traffic``."""

    return RequestSpec(
        method="GET",
        path=f"{_base(plan)}/airport/{icao}/traffic",
        query={**_window(start, end), "direction": direction, "limit": limit},
        cast_to=HistoryAirportTrafficResponse,
    )


# ── window walking (iter_flights) ────────────────────────────────────────────


def _as_datetime(value: DateLike, *, label: str) -> datetime:
    """Coerce a ``DateLike`` into a tz-aware UTC datetime for window arithmetic.

    The plain :meth:`History.flights` passes ``start``/``end`` through untouched,
    but slicing a range into windows means doing arithmetic on it, so here the
    value has to become a real datetime. Naive input is read as UTC, matching the
    backend (``validate_date_range`` stamps ``timezone.utc`` on naive values).
    """

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} must be an ISO 8601 datetime, got {value!r}") from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _slice_windows(
    start: datetime, end: datetime, window_days: int
) -> list[tuple[datetime, datetime]]:
    """Cut ``[start, end]`` into ``window_days``-long slices, **newest first**.

    Newest first because that is the order results are wanted in (a UI shows the
    most recent flights first) and because it makes ``max_items`` meaningful:
    "the 20 most recent" rather than "20 from wherever the walk started".

    Slices touch at the edges (each window's start is the previous window's end),
    which is why the caller must deduplicate: a flight recorded exactly on a
    boundary is returned by both requests.
    """

    span = timedelta(days=window_days)
    windows: list[tuple[datetime, datetime]] = []
    cursor = end
    while cursor > start:
        window_start = max(start, cursor - span)
        windows.append((window_start, cursor))
        cursor = window_start
    return windows


def _positive_int(value: object, name: str) -> int:
    """Read a caller supplied count, or raise :class:`ValueError` immediately.

    The twin of :func:`skylink_api.resources.adsb._positive_int`, and the same
    rule: anything that is not a finite number of at least 1 — ``None``, a
    string, ``NaN``, zero, a negative — fails at the call site rather than deep
    inside a generator. Values above a documented maximum are *not* handled here;
    those are clamped by the caller.
    """

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    number = int(value)
    if number < 1:
        raise ValueError(f"{name} must be at least 1, got {value!r}")
    return number


def _flight_key(flight: HistoryFlight) -> tuple[str, ...]:
    """Identity used to deduplicate flights across overlapping windows.

    ``flight_id`` (a UUID) whenever the row has one — it always does on this
    endpoint. The fallback covers a hypothetical row without it and is built from
    the columns that identify a movement: address, callsign and the two
    first/last-seen timestamps.
    """

    if flight.flight_id:
        return ("id", flight.flight_id)
    return (
        "row",
        flight.icao24 or "",
        flight.callsign or "",
        str(flight.flight_start),
        str(flight.flight_end),
    )


class _FlightWindowWalker:
    """Window planning, deduplication and the item cap for ``iter_flights``.

    Pure state, no I/O — the sync and async iterators share it so the parts that
    are easy to get wrong (window edges, duplicates, when to stop) cannot drift
    apart. All validation happens in the constructor, which is why the public
    methods can call it eagerly and fail before touching the network.
    """

    def __init__(
        self,
        *,
        plan: str,
        window_days: int,
        max_items: int | None,
        start: DateLike | None,
        end: DateLike | None,
        limit: int | None,
    ) -> None:
        max_window = PLAN_WINDOW_DAYS.get(plan, PLAN_WINDOW_DAYS[DEFAULT_HISTORY_PLAN])

        # Above the plan's window the slice is clamped, not refused: the walk is
        # made of several requests anyway, so a too-wide slice has an obvious
        # correct meaning ("as wide as this plan allows") and the alternative is
        # an error for asking the SDK to do exactly what it exists to do. Below
        # 1 — or not a number at all — there is nothing to clamp to.
        slice_days = min(_positive_int(window_days, "window_days"), max_window)
        max_rows = None if max_items is None else _positive_int(max_items, "max_items")

        end_dt = _as_datetime(end, label="end") if end is not None else datetime.now(timezone.utc)
        start_dt = (
            _as_datetime(start, label="start")
            if start is not None
            else end_dt - timedelta(days=max_window)
        )
        if start_dt >= end_dt:
            raise ValueError(f"start must be before end, got {start_dt} >= {end_dt}")

        #: Per-request row cap. Defaults to the plan maximum rather than the
        #: API's default of 100, so a busy window is not silently truncated.
        self.limit = (
            limit
            if limit is not None
            else PLAN_FLIGHT_LIMITS.get(plan, PLAN_FLIGHT_LIMITS[DEFAULT_HISTORY_PLAN])
        )
        self.windows = _slice_windows(start_dt, end_dt, slice_days)
        self.max_items = max_rows
        self.yielded = 0
        self._seen: set[tuple[str, ...]] = set()

    def take(self, response: HistoryFlightsResponse) -> tuple[list[HistoryFlight], bool]:
        """Filter one window's rows; return what to yield and whether to stop."""

        fresh: list[HistoryFlight] = []
        for flight in response.flights:
            key = _flight_key(flight)
            if key in self._seen:
                continue
            self._seen.add(key)
            fresh.append(flight)
            self.yielded += 1
            if self.max_items is not None and self.yielded >= self.max_items:
                return fresh, True
        return fresh, False


# ── sync ─────────────────────────────────────────────────────────────────────


class History:
    """``sky.history`` — archived ADS-B flights and positions.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...", history_plan="mega") as sky:
            found = sky.history.flights(registration="G-STBA", limit=10)
            track = sky.history.track(found.flights[0].flight_id)

    Every method accepts ``plan="ultra"|"mega"`` to override the client default
    for that call.
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def _plan(self, plan: HistoryPlan | None) -> str:
        return resolve_plan(plan, self._client.history_plan)

    def flights(
        self,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        icao24: str | None = None,
        registration: str | None = None,
        callsign: str | None = None,
        departure_icao: str | None = None,
        arrival_icao: str | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryFlightsResponse:
        """Search archived flights — ``GET /{plan}/history/flights``.

        Args:
            start: Window start; ``datetime``/``date`` are formatted as ISO 8601,
                strings pass through. Defaults to 24 hours ago server-side.
            end: Window end, same handling. Defaults to now.
            icao24: 6-hex transponder address; lower-cased for the request.
            registration: Tail number (``"G-STBA"``). Resolved to an address
                server-side — see the note below.
            callsign: Exact match (``"BAW117"``), not a prefix.
            departure_icao: 4-letter ICAO of the departure airport.
            arrival_icao: 4-letter ICAO of the arrival airport.
            limit: Newest-first cap. Default 100; max 1 000 on ultra, 2 000 on
                mega. There is no pagination — no offset, no cursor.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            **At least one filter is required** (422 otherwise), and the window
            may not exceed the plan's span — 90 days on ultra, 365 on mega.

            A ``registration`` that is not in the aircraft database is **not** an
            error: the response is a normal ``200`` carrying ``count=0``,
            ``flights=[]`` and an explanatory ``note``. Check
            :attr:`~skylink_api.models.history.HistoryFlightsResponse.note`
            before treating an empty result as "this aircraft did not fly".

            Rows are the 27-column search shape; the detail-only columns of
            :meth:`flight` stay ``None``.

        Raises:
            UnprocessableEntityError: no filter, malformed value, window longer
                than the plan allows, or ``icao24`` and ``registration``
                disagreeing (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _flights_spec(
            plan=self._plan(plan),
            start=start,
            end=end,
            icao24=icao24,
            registration=registration,
            callsign=callsign,
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            limit=limit,
        )
        return cast(HistoryFlightsResponse, self._client.execute(spec, request_options))

    def iter_flights(
        self,
        *,
        window_days: int = DEFAULT_ITER_WINDOW_DAYS,
        max_items: int | None = None,
        plan: HistoryPlan | None = None,
        start: DateLike | None = None,
        end: DateLike | None = None,
        icao24: str | None = None,
        registration: str | None = None,
        callsign: str | None = None,
        departure_icao: str | None = None,
        arrival_icao: str | None = None,
        limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> Iterator[HistoryFlight]:
        """Walk archived flights across a long range, newest first.

        :meth:`flights` has no pagination — no offset, no cursor — and refuses a
        range longer than the plan allows (90 days on ultra, 365 on mega). So
        "every flight of this aircraft over the last six months" is not one call
        but a series of them. This is that series::

            for flight in sky.history.iter_flights(registration="G-STBA", max_items=50):
                print(flight.takeoff_time, flight.callsign)

        The range is cut into ``window_days``-long slices and walked **from the
        most recent slice backwards**, so the first flights yielded are the
        newest ones and ``max_items`` means "the N most recent".

        Args:
            window_days: Slice width, 1 to the plan's maximum (90 on ultra, 365
                on mega); a wider value is **clamped to the plan's maximum**, not
                rejected. Each slice is one request, so this trades requests
                against the per-request row cap: narrow windows cost quota, wide
                ones risk hitting ``limit`` and silently dropping the oldest
                flights of that slice.
            max_items: Stop after this many distinct flights. ``None`` walks the
                whole range.
            plan: Override the client's ``history_plan`` for every request.
            start: Range start. ``datetime``/``date``/ISO 8601 string; naive
                values are read as UTC. Defaults to the plan's full window before
                ``end`` (90 days on ultra, 365 on mega).
            end: Range end, same handling. Defaults to now (UTC).
            icao24: 6-hex transponder address.
            registration: Tail number, resolved to an address server-side.
            callsign: Exact match (``"BAW117"``), not a prefix.
            departure_icao: 4-letter ICAO of the departure airport.
            arrival_icao: 4-letter ICAO of the arrival airport.
            limit: Rows per **window**. Defaults to the plan maximum (1 000 /
                2 000) rather than the API's default of 100, because truncating a
                window here loses flights invisibly.
            request_options: Per-request overrides applied to every window.

        Yields:
            :class:`~skylink_api.models.history.HistoryFlight` rows, deduplicated
            by ``flight_id`` — window edges touch, so a flight sitting on a
            boundary is returned by two requests and yielded once.

        Note:
            The plan's day limit applies **per request**, not to the walk, so a
            six-month range is reachable on ultra. Whether rows that old still
            exist is a matter of retention, not of this SDK.

            Rows are matched on their ingest time (``created_at``), not on
            takeoff, and only ``ARCHIVED`` flights are stored — filtering by
            ``flight_state`` is pointless.

        Raises:
            ValueError: no filter given (same rule as :meth:`flights`),
                ``window_days`` or ``max_items`` below 1 or not a finite number,
                an unparseable ``start``/``end``, or ``start >= end``. All raised
                from the call itself, before any request; a ``window_days``
                *above* the plan maximum is clamped instead.
            UnprocessableEntityError: rejected by the API despite the local
                checks (e.g. ``icao24`` and ``registration`` disagreeing).
        """

        resolved_plan = self._plan(plan)
        _check_flight_filters(
            icao24=icao24,
            registration=registration,
            callsign=callsign,
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            caller="history.iter_flights()",
        )
        walker = _FlightWindowWalker(
            plan=resolved_plan,
            window_days=window_days,
            max_items=max_items,
            start=start,
            end=end,
            limit=limit,
        )
        return self._iter_flights(
            walker,
            plan=resolved_plan,
            icao24=icao24,
            registration=registration,
            callsign=callsign,
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            request_options=request_options,
        )

    def _iter_flights(
        self,
        walker: _FlightWindowWalker,
        *,
        plan: str,
        request_options: RequestOptions | None,
        **filters: Any,
    ) -> Iterator[HistoryFlight]:
        for window_start, window_end in walker.windows:
            spec = _flights_spec(
                plan=plan,
                start=window_start,
                end=window_end,
                limit=walker.limit,
                **filters,
            )
            response = cast(HistoryFlightsResponse, self._client.execute(spec, request_options))
            rows, done = walker.take(response)
            yield from rows
            if done:
                return

    def flight(
        self,
        flight_id: str,
        *,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryFlight:
        """One flight in full — ``GET /{plan}/history/flight/{flight_id}``.

        Args:
            flight_id: UUID from :meth:`flights`.
            plan: Override the client's ``history_plan`` for this call.

        Returns:
            The widest flight shape the API serves: the search columns plus
            ``off_block_time``, ``on_block_time``, ``duration_source``,
            ``arrival_distance_nm``, ``created_at`` and ``updated_at``.

        Raises:
            NotFoundError: no flight with that ID (404).
            UnprocessableEntityError: ``flight_id`` is not a UUID (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _flight_spec(flight_id, plan=self._plan(plan))
        return cast(HistoryFlight, self._client.execute(spec, request_options))

    def track(
        self,
        flight_id: str,
        *,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryTrackResponse:
        """Flown path of one flight — ``GET /{plan}/history/flight/{id}/track``.

        The window is derived from the flight itself (takeoff → landing, padded
        by 5 minutes each way), so no ``start``/``end`` is needed. For arbitrary
        windows use :meth:`positions`.

        Args:
            flight_id: UUID from :meth:`flights`.
            limit: Max position rows, newest first. Default 2 000 on ultra
                (5 000 on mega); maximum 5 000 and 10 000 respectively.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            Positions carry ``altitude_baro``, not ``altitude`` — the live ADS-B
            namespace names the same value differently.

        Raises:
            NotFoundError: unknown flight, or it has no usable time window (404).
            UnprocessableEntityError: ``flight_id`` is not a UUID (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _track_spec(flight_id, plan=self._plan(plan), limit=limit)
        return cast(HistoryTrackResponse, self._client.execute(spec, request_options))

    def positions(
        self,
        ident: str,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryPositionsResponse:
        """Positions for an aircraft, by address **or** registration.

        Dispatches on the identifier: 6 hex characters go to
        ``/positions/{icao24}``, anything else to
        ``/positions/registration/{reg}``.

        Args:
            ident: ``"4ca1fb"`` (address) or ``"G-STBA"`` (registration).
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows, newest first. Default 1 000; max 5 000 on ultra,
                10 000 on mega.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            A registration that is also valid hex (``"ABC123"``) is read as an
            address. Use :meth:`positions_by_registration` to force the route.

        Raises:
            NotFoundError: registration route only — unknown tail number (404).
            UnprocessableEntityError: malformed identifier or window (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _positions_spec(ident, plan=self._plan(plan), start=start, end=end, limit=limit)
        return cast(HistoryPositionsResponse, self._client.execute(spec, request_options))

    def positions_by_icao24(
        self,
        icao24: str,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryPositionsResponse:
        """Positions by transponder address — ``GET /{plan}/history/positions/{icao24}``.

        Args:
            icao24: 6-hex address; lower-cased for the request. It comes back
                UPPERCASE in the envelope.
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows, newest first (default 1 000).
            plan: Override the client's ``history_plan`` for this call.

        Note:
            The response also carries ``flights`` — up to 50 archived flights
            overlapping the window — so track segments can be attributed.

        Raises:
            UnprocessableEntityError: bad address or window (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _positions_by_icao24_spec(
            icao24, plan=self._plan(plan), start=start, end=end, limit=limit
        )
        return cast(HistoryPositionsResponse, self._client.execute(spec, request_options))

    def positions_by_registration(
        self,
        registration: str,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryPositionsResponse:
        """Positions by tail number — ``GET /{plan}/history/positions/registration/{reg}``.

        Args:
            registration: Tail number, with or without dashes (``"G-STBA"``).
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows, newest first (default 1 000).
            plan: Override the client's ``history_plan`` for this call.

        Note:
            This route — and only this route — echoes a ``registration`` field
            in the response, normalised to upper case without separators.

        Raises:
            NotFoundError: registration not in the aircraft database (404).
                Unlike :meth:`flights`, this one really is a 404, not a note.
            UnprocessableEntityError: malformed registration or window (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _positions_by_registration_spec(
            registration, plan=self._plan(plan), start=start, end=end, limit=limit
        )
        return cast(HistoryPositionsResponse, self._client.execute(spec, request_options))

    def airport_traffic(
        self,
        icao: str,
        *,
        direction: TrafficDirection = "both",
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryAirportTrafficResponse:
        """Movements at an airport — ``GET /{plan}/history/airport/{icao}/traffic``.

        Args:
            icao: 4-letter ICAO code.
            direction: ``"dep"``, ``"arr"`` or ``"both"`` (default).
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows. Default 100; max 1 000 on ultra, 2 000 on mega.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            Only completed (``ARCHIVED``) flights are returned, and the window is
            matched against the row's ingest time rather than its takeoff time —
            a flight at the edge of the window may be missing.

        Raises:
            UnprocessableEntityError: bad ICAO, bad ``direction`` or a window
                longer than the plan allows (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _airport_traffic_spec(
            icao,
            plan=self._plan(plan),
            direction=direction,
            start=start,
            end=end,
            limit=limit,
        )
        return cast(HistoryAirportTrafficResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncHistory:
    """``sky.history`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`History`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            found = await sky.history.flights(callsign="BAW117")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    def _plan(self, plan: HistoryPlan | None) -> str:
        return resolve_plan(plan, self._client.history_plan)

    async def flights(
        self,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        icao24: str | None = None,
        registration: str | None = None,
        callsign: str | None = None,
        departure_icao: str | None = None,
        arrival_icao: str | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryFlightsResponse:
        """Search archived flights — ``GET /{plan}/history/flights``.

        Args:
            start: Window start; ``datetime``/``date`` are formatted as ISO 8601,
                strings pass through. Defaults to 24 hours ago server-side.
            end: Window end, same handling. Defaults to now.
            icao24: 6-hex transponder address; lower-cased for the request.
            registration: Tail number (``"G-STBA"``). Resolved to an address
                server-side — see the note below.
            callsign: Exact match (``"BAW117"``), not a prefix.
            departure_icao: 4-letter ICAO of the departure airport.
            arrival_icao: 4-letter ICAO of the arrival airport.
            limit: Newest-first cap. Default 100; max 1 000 on ultra, 2 000 on
                mega. There is no pagination — no offset, no cursor.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            **At least one filter is required** (422 otherwise), and the window
            may not exceed the plan's span — 90 days on ultra, 365 on mega.

            A ``registration`` that is not in the aircraft database is **not** an
            error: the response is a normal ``200`` carrying ``count=0``,
            ``flights=[]`` and an explanatory ``note``. Check
            :attr:`~skylink_api.models.history.HistoryFlightsResponse.note`
            before treating an empty result as "this aircraft did not fly".

            Rows are the 27-column search shape; the detail-only columns of
            :meth:`flight` stay ``None``.

        Raises:
            UnprocessableEntityError: no filter, malformed value, window longer
                than the plan allows, or ``icao24`` and ``registration``
                disagreeing (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _flights_spec(
            plan=self._plan(plan),
            start=start,
            end=end,
            icao24=icao24,
            registration=registration,
            callsign=callsign,
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            limit=limit,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(HistoryFlightsResponse, result)

    def iter_flights(
        self,
        *,
        window_days: int = DEFAULT_ITER_WINDOW_DAYS,
        max_items: int | None = None,
        plan: HistoryPlan | None = None,
        start: DateLike | None = None,
        end: DateLike | None = None,
        icao24: str | None = None,
        registration: str | None = None,
        callsign: str | None = None,
        departure_icao: str | None = None,
        arrival_icao: str | None = None,
        limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> AsyncIterator[HistoryFlight]:
        """Walk archived flights across a long range, newest first.

        Async twin of :meth:`History.iter_flights`; see it for the windowing and
        deduplication notes. The method is **not** a coroutine — call it without
        ``await`` and drive it with ``async for``::

            async for flight in sky.history.iter_flights(callsign="BAW117", max_items=20):
                print(flight.takeoff_time)

        Args:
            window_days: Slice width, 1 to the plan's maximum (one request each);
                a wider value is clamped to that maximum.
            max_items: Stop after this many distinct flights.
            plan: Override the client's ``history_plan`` for every request.
            start: Range start; defaults to the plan's full window before ``end``.
            end: Range end; defaults to now (UTC).
            icao24: 6-hex transponder address.
            registration: Tail number.
            callsign: Exact match.
            departure_icao: Departure airport ICAO.
            arrival_icao: Arrival airport ICAO.
            limit: Rows per window; defaults to the plan maximum.
            request_options: Per-request overrides applied to every window.

        Raises:
            ValueError: no filter, bad ``window_days``/``max_items``, or an
                unusable ``start``/``end`` — before any request.
        """

        resolved_plan = self._plan(plan)
        _check_flight_filters(
            icao24=icao24,
            registration=registration,
            callsign=callsign,
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            caller="history.iter_flights()",
        )
        walker = _FlightWindowWalker(
            plan=resolved_plan,
            window_days=window_days,
            max_items=max_items,
            start=start,
            end=end,
            limit=limit,
        )
        return self._iter_flights(
            walker,
            plan=resolved_plan,
            icao24=icao24,
            registration=registration,
            callsign=callsign,
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            request_options=request_options,
        )

    async def _iter_flights(
        self,
        walker: _FlightWindowWalker,
        *,
        plan: str,
        request_options: RequestOptions | None,
        **filters: Any,
    ) -> AsyncIterator[HistoryFlight]:
        for window_start, window_end in walker.windows:
            spec = _flights_spec(
                plan=plan,
                start=window_start,
                end=window_end,
                limit=walker.limit,
                **filters,
            )
            result: Any = await self._client.execute(spec, request_options)
            rows, done = walker.take(cast(HistoryFlightsResponse, result))
            for flight in rows:
                yield flight
            if done:
                return

    async def flight(
        self,
        flight_id: str,
        *,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryFlight:
        """One flight in full — ``GET /{plan}/history/flight/{flight_id}``.

        Args:
            flight_id: UUID from :meth:`flights`.
            plan: Override the client's ``history_plan`` for this call.

        Returns:
            The widest flight shape the API serves: the search columns plus
            ``off_block_time``, ``on_block_time``, ``duration_source``,
            ``arrival_distance_nm``, ``created_at`` and ``updated_at``.

        Raises:
            NotFoundError: no flight with that ID (404).
            UnprocessableEntityError: ``flight_id`` is not a UUID (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _flight_spec(flight_id, plan=self._plan(plan))
        result: Any = await self._client.execute(spec, request_options)
        return cast(HistoryFlight, result)

    async def track(
        self,
        flight_id: str,
        *,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryTrackResponse:
        """Flown path of one flight — ``GET /{plan}/history/flight/{id}/track``.

        The window is derived from the flight itself (takeoff → landing, padded
        by 5 minutes each way), so no ``start``/``end`` is needed. For arbitrary
        windows use :meth:`positions`.

        Args:
            flight_id: UUID from :meth:`flights`.
            limit: Max position rows, newest first. Default 2 000 on ultra
                (5 000 on mega); maximum 5 000 and 10 000 respectively.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            Positions carry ``altitude_baro``, not ``altitude`` — the live ADS-B
            namespace names the same value differently.

        Raises:
            NotFoundError: unknown flight, or it has no usable time window (404).
            UnprocessableEntityError: ``flight_id`` is not a UUID (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _track_spec(flight_id, plan=self._plan(plan), limit=limit)
        result: Any = await self._client.execute(spec, request_options)
        return cast(HistoryTrackResponse, result)

    async def positions(
        self,
        ident: str,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryPositionsResponse:
        """Positions for an aircraft, by address **or** registration.

        Dispatches on the identifier: 6 hex characters go to
        ``/positions/{icao24}``, anything else to
        ``/positions/registration/{reg}``.

        Args:
            ident: ``"4ca1fb"`` (address) or ``"G-STBA"`` (registration).
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows, newest first. Default 1 000; max 5 000 on ultra,
                10 000 on mega.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            A registration that is also valid hex (``"ABC123"``) is read as an
            address. Use :meth:`positions_by_registration` to force the route.

        Raises:
            NotFoundError: registration route only — unknown tail number (404).
            UnprocessableEntityError: malformed identifier or window (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _positions_spec(ident, plan=self._plan(plan), start=start, end=end, limit=limit)
        result: Any = await self._client.execute(spec, request_options)
        return cast(HistoryPositionsResponse, result)

    async def positions_by_icao24(
        self,
        icao24: str,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryPositionsResponse:
        """Positions by transponder address — ``GET /{plan}/history/positions/{icao24}``.

        Args:
            icao24: 6-hex address; lower-cased for the request. It comes back
                UPPERCASE in the envelope.
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows, newest first (default 1 000).
            plan: Override the client's ``history_plan`` for this call.

        Note:
            The response also carries ``flights`` — up to 50 archived flights
            overlapping the window — so track segments can be attributed.

        Raises:
            UnprocessableEntityError: bad address or window (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _positions_by_icao24_spec(
            icao24, plan=self._plan(plan), start=start, end=end, limit=limit
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(HistoryPositionsResponse, result)

    async def positions_by_registration(
        self,
        registration: str,
        *,
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryPositionsResponse:
        """Positions by tail number — ``GET /{plan}/history/positions/registration/{reg}``.

        Args:
            registration: Tail number, with or without dashes (``"G-STBA"``).
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows, newest first (default 1 000).
            plan: Override the client's ``history_plan`` for this call.

        Note:
            This route — and only this route — echoes a ``registration`` field
            in the response, normalised to upper case without separators.

        Raises:
            NotFoundError: registration not in the aircraft database (404).
                Unlike :meth:`flights`, this one really is a 404, not a note.
            UnprocessableEntityError: malformed registration or window (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _positions_by_registration_spec(
            registration, plan=self._plan(plan), start=start, end=end, limit=limit
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(HistoryPositionsResponse, result)

    async def airport_traffic(
        self,
        icao: str,
        *,
        direction: TrafficDirection = "both",
        start: DateLike | None = None,
        end: DateLike | None = None,
        limit: int | None = None,
        plan: HistoryPlan | None = None,
        request_options: RequestOptions | None = None,
    ) -> HistoryAirportTrafficResponse:
        """Movements at an airport — ``GET /{plan}/history/airport/{icao}/traffic``.

        Args:
            icao: 4-letter ICAO code.
            direction: ``"dep"``, ``"arr"`` or ``"both"`` (default).
            start: Window start, ISO 8601. Defaults to 24 hours ago.
            end: Window end. Defaults to now.
            limit: Max rows. Default 100; max 1 000 on ultra, 2 000 on mega.
            plan: Override the client's ``history_plan`` for this call.

        Note:
            Only completed (``ARCHIVED``) flights are returned, and the window is
            matched against the row's ingest time rather than its takeoff time —
            a flight at the edge of the window may be missing.

        Raises:
            UnprocessableEntityError: bad ICAO, bad ``direction`` or a window
                longer than the plan allows (422).
            ServiceUnavailableError: the history database is unreachable (503).
        """

        spec = _airport_traffic_spec(
            icao,
            plan=self._plan(plan),
            direction=direction,
            start=start,
            end=end,
            limit=limit,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(HistoryAirportTrafficResponse, result)
