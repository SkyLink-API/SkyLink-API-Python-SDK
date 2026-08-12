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

__all__ = ["AsyncHistory", "History", "is_icao24", "resolve_plan"]

#: Traffic direction filter for :meth:`History.airport_traffic`: departures,
#: arrivals, or both (the default).
TrafficDirection = Literal["dep", "arr", "both"]

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

    The endpoint refuses an unfiltered search: without at least one of the five
    identifiers it answers ``422 At least one of icao24, registration, callsign,
    departure_icao, arrival_icao must be provided``. Checking here spends no
    quota and gives the caller a plain :class:`ValueError` instead.
    """

    if not any((icao24, registration, callsign, departure_icao, arrival_icao)):
        raise ValueError(
            "history.flights() needs at least one filter: "
            "icao24, registration, callsign, departure_icao or arrival_icao"
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
