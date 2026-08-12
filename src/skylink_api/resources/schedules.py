"""Airport departure and arrival boards.

Structure follows :mod:`skylink_api.resources.weather`: builders first, then the
sync class, then its async mirror. Both endpoints take the same parameters and
differ only in path and row type, so a single private builder serves them.

Two input rules are enforced here rather than by the API round trip:

* exactly one of ``icao``/``iata`` — the API answers 400 for none and 400 for
  both, and a local :class:`ValueError` says so without spending a request;
* ``date`` is normalised to the endpoint's ``DD-MM-YYYY`` by
  :func:`~skylink_api._qs.format_schedule_date`, so ``date(2026, 2, 11)``,
  ``datetime(...)`` and ``"2026-02-11"`` all serialise the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .._qs import format_schedule_date
from .._types import DateLike, RequestOptions, RequestSpec
from ..models.schedules import ArrivalsResponse, DeparturesResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncSchedules", "Schedules"]

#: Which board to fetch. Also echoed back in the response envelope.
ScheduleDirection = Literal["departures", "arrivals"]


# ── builders ─────────────────────────────────────────────────────────────────


def _check_airport_selector(icao: str | None, iata: str | None) -> None:
    """Reject "neither" and "both" locally — the API rejects both cases with 400."""

    if icao is None and iata is None:
        raise ValueError("schedules require an airport: pass either icao= or iata=")
    if icao is not None and iata is not None:
        raise ValueError("pass either icao= or iata=, not both")


def _schedule_spec(
    direction: ScheduleDirection,
    *,
    icao: str | None = None,
    iata: str | None = None,
    date: DateLike | None = None,
    time: str | None = None,
    ts: int | None = None,
) -> RequestSpec:
    """``GET /schedules/{direction}``. Shared by departures and arrivals."""

    _check_airport_selector(icao, iata)
    return RequestSpec(
        method="GET",
        path=f"/schedules/{direction}",
        query={
            "icao": icao,
            "iata": iata,
            # DD-MM-YYYY — the endpoint's own format, applied by the builder.
            "date": None if date is None else format_schedule_date(date),
            "time": time,
            "ts": ts,
        },
        cast_to=DeparturesResponse if direction == "departures" else ArrivalsResponse,
    )


def _departures_spec(
    *,
    icao: str | None = None,
    iata: str | None = None,
    date: DateLike | None = None,
    time: str | None = None,
    ts: int | None = None,
) -> RequestSpec:
    """``GET /schedules/departures``."""

    return _schedule_spec("departures", icao=icao, iata=iata, date=date, time=time, ts=ts)


def _arrivals_spec(
    *,
    icao: str | None = None,
    iata: str | None = None,
    date: DateLike | None = None,
    time: str | None = None,
    ts: int | None = None,
) -> RequestSpec:
    """``GET /schedules/arrivals``."""

    return _schedule_spec("arrivals", icao=icao, iata=iata, date=date, time=time, ts=ts)


# ── sync ─────────────────────────────────────────────────────────────────────


class Schedules:
    """``sky.schedules`` — airport departure and arrival boards.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            board = sky.schedules.departures(icao="EGLL")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def departures(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        date: DateLike | None = None,
        time: str | None = None,
        ts: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DeparturesResponse:
        """Departure board for an airport — ``GET /schedules/departures``.

        Covers roughly the next 12 hours from the requested moment.

        Args:
            icao: 4-letter ICAO code (``"EGLL"``). Mutually exclusive with ``iata``.
            iata: 3-letter IATA code (``"LHR"``). Mutually exclusive with ``icao``.
            date: Day to fetch. Accepts ``date``, ``datetime`` or a string; goes
                on the wire as ``DD-MM-YYYY``. An unrecognised string is passed
                through unchanged, so a pre-formatted ``"11-02-2026"`` works.
                The window is 5 days back to 1 day forward — outside it the API
                answers 400.
            time: Time of day as ``"HH:MM"``, refining ``date``. Ignored by the
                API unless ``date`` is also given.
            ts: Unix timestamp in **milliseconds**, not seconds. Takes priority
                over ``date``/``time`` when both are sent.

        Returns:
            Rows with a ``destination`` (city name) and ``iata`` (its code). Wire
            keys inside ``flights[]`` are PascalCase — ``Time``, ``Date``,
            ``IATA``, ``Destination``, ``Flight``, ``Airline``, ``Status`` — and
            the model maps them to snake_case attributes.

        Raises:
            ValueError: neither or both of ``icao``/``iata`` were given (raised
                locally, before any request).
            BadRequestError: malformed ``date``/``time``, a date outside the
                5-days-back/1-day-forward window, or a code of the wrong length (400).
            NotFoundError: unknown airport, an ICAO airport with no IATA code, or
                no schedule published for it (404).
            InternalServerError: the upstream board is unavailable (500).
        """

        spec = _departures_spec(icao=icao, iata=iata, date=date, time=time, ts=ts)
        return cast(DeparturesResponse, self._client.execute(spec, request_options))

    def arrivals(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        date: DateLike | None = None,
        time: str | None = None,
        ts: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ArrivalsResponse:
        """Arrival board for an airport — ``GET /schedules/arrivals``.

        Same parameters as :meth:`departures`; the difference is in the rows.

        Args:
            icao: 4-letter ICAO code. Mutually exclusive with ``iata``.
            iata: 3-letter IATA code. Mutually exclusive with ``icao``.
            date: Day to fetch, sent as ``DD-MM-YYYY``; 5 days back to 1 day forward.
            time: ``"HH:MM"``, refining ``date``.
            ts: Unix timestamp in **milliseconds**; wins over ``date``/``time``.

        Returns:
            Rows with an ``origin`` (city name) instead of a ``destination`` —
            wire key ``Origin``. Arrival rows never carry ``Destination``.

        Raises:
            ValueError: neither or both of ``icao``/``iata`` were given.
            BadRequestError: bad date/time or airport code (400).
            NotFoundError: unknown airport or no schedule available (404).
            InternalServerError: the upstream board is unavailable (500).
        """

        spec = _arrivals_spec(icao=icao, iata=iata, date=date, time=time, ts=ts)
        return cast(ArrivalsResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncSchedules:
    """``sky.schedules`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Schedules`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            board = await sky.schedules.departures(icao="EGLL")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def departures(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        date: DateLike | None = None,
        time: str | None = None,
        ts: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DeparturesResponse:
        """Departure board for an airport — ``GET /schedules/departures``.

        Covers roughly the next 12 hours from the requested moment.

        Args:
            icao: 4-letter ICAO code (``"EGLL"``). Mutually exclusive with ``iata``.
            iata: 3-letter IATA code (``"LHR"``). Mutually exclusive with ``icao``.
            date: Day to fetch. Accepts ``date``, ``datetime`` or a string; goes
                on the wire as ``DD-MM-YYYY``. The window is 5 days back to 1 day
                forward — outside it the API answers 400.
            time: Time of day as ``"HH:MM"``, refining ``date``.
            ts: Unix timestamp in **milliseconds**, not seconds. Takes priority
                over ``date``/``time``.

        Returns:
            Rows with a ``destination`` (city name) and ``iata`` (its code); wire
            keys inside ``flights[]`` are PascalCase.

        Raises:
            ValueError: neither or both of ``icao``/``iata`` were given.
            BadRequestError: bad date/time or airport code (400).
            NotFoundError: unknown airport or no schedule available (404).
            InternalServerError: the upstream board is unavailable (500).
        """

        spec = _departures_spec(icao=icao, iata=iata, date=date, time=time, ts=ts)
        result: Any = await self._client.execute(spec, request_options)
        return cast(DeparturesResponse, result)

    async def arrivals(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        date: DateLike | None = None,
        time: str | None = None,
        ts: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ArrivalsResponse:
        """Arrival board for an airport — ``GET /schedules/arrivals``.

        Same parameters as :meth:`departures`; the difference is in the rows.

        Args:
            icao: 4-letter ICAO code. Mutually exclusive with ``iata``.
            iata: 3-letter IATA code. Mutually exclusive with ``icao``.
            date: Day to fetch, sent as ``DD-MM-YYYY``; 5 days back to 1 day forward.
            time: ``"HH:MM"``, refining ``date``.
            ts: Unix timestamp in **milliseconds**; wins over ``date``/``time``.

        Returns:
            Rows with an ``origin`` (city name) instead of a ``destination``.

        Raises:
            ValueError: neither or both of ``icao``/``iata`` were given.
            BadRequestError: bad date/time or airport code (400).
            NotFoundError: unknown airport or no schedule available (404).
            InternalServerError: the upstream board is unavailable (500).
        """

        spec = _arrivals_spec(icao=icao, iata=iata, date=date, time=time, ts=ts)
        result: Any = await self._client.execute(spec, request_options)
        return cast(ArrivalsResponse, result)
