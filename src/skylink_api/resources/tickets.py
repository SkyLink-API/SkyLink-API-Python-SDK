"""Flight ticket search (``/tickets/*``).

Follows the layout of :mod:`skylink_api.resources.weather`: builders first, then
the sync class, then the async mirror.

Date handling: the endpoint wants ``YYYY-MM-DD``, so the builder pushes ``date``
through :func:`skylink_api._qs.format_ticket_date`, which accepts a ``date``, a
``datetime`` (time part dropped) or a string. This is the only endpoint-specific
formatting done here — see ``_qs`` for the table of the other formats.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._qs import format_ticket_date
from .._types import DateLike, RequestOptions, RequestSpec
from ..models.tickets import TicketSearchResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncTickets", "Tickets"]


# ── builders ─────────────────────────────────────────────────────────────────


def _search_spec(
    *,
    origin: str,
    destination: str,
    date: DateLike | None = None,
    passengers: int = 1,
) -> RequestSpec:
    """``GET /tickets/search``. ``date`` is normalised to ``YYYY-MM-DD``."""

    return RequestSpec(
        method="GET",
        path="/tickets/search",
        query={
            "origin": origin,
            "destination": destination,
            "date": format_ticket_date(date) if date is not None else None,
            "passengers": passengers,
        },
        cast_to=TicketSearchResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Tickets:
    """``sky.tickets`` — ticket availability and pricing.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            offers = sky.tickets.search(origin="LHR", destination="JFK")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def search(
        self,
        *,
        origin: str,
        destination: str,
        date: DateLike | None = None,
        passengers: int = 1,
        request_options: RequestOptions | None = None,
    ) -> TicketSearchResponse:
        """Search one-way fares — ``GET /tickets/search``.

        Results are sorted cheapest first and cached by the API for an hour.
        There is no small cap on the number of offers — searches on busy city
        pairs come back with 100+.

        Args:
            origin: **IATA** code, 3 letters (``"LHR"``). ICAO is rejected.
            destination: IATA code, different from ``origin``.
            date: Travel date. Accepts ``date``, ``datetime`` (time ignored) or
                a ``"YYYY-MM-DD"`` string; anything else is passed through
                untouched. Defaults server-side to **today + 7 days**. Dates in
                the past are a 422.
            passengers: 1-9 (default 1). Prices are for the whole party.

        Note:
            Three quirks of the payload:

            * ``offer.layovers`` is ``None`` for a direct flight, not ``[]``.
            * ``leg.departure_datetime``/``arrival_datetime`` are **naive local
              ISO strings** with no timezone — they are wall-clock time at the
              respective airport and are typed ``str`` so nothing treats them
              as UTC.
            * ``offer.price_usd`` silently keeps the **original currency** when
              the API's FX lookup fails; compare with ``original_currency``.

            No fares found is a normal ``200`` with ``flights=[]``.

        Raises:
            UnprocessableEntityError: non-IATA code, identical origin and
                destination, past date, or ``passengers`` outside 1-9 (422).
            ServiceUnavailableError: the upstream ticket search is unavailable
                (503) — retried automatically.
        """

        spec = _search_spec(
            origin=origin,
            destination=destination,
            date=date,
            passengers=passengers,
        )
        return cast(TicketSearchResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncTickets:
    """``sky.tickets`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Tickets`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            offers = await sky.tickets.search(origin="LHR", destination="JFK")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def search(
        self,
        *,
        origin: str,
        destination: str,
        date: DateLike | None = None,
        passengers: int = 1,
        request_options: RequestOptions | None = None,
    ) -> TicketSearchResponse:
        """Search one-way fares — ``GET /tickets/search``.

        Results are sorted cheapest first and cached by the API for an hour.
        There is no small cap on the number of offers — searches on busy city
        pairs come back with 100+.

        Args:
            origin: **IATA** code, 3 letters (``"LHR"``). ICAO is rejected.
            destination: IATA code, different from ``origin``.
            date: Travel date. Accepts ``date``, ``datetime`` (time ignored) or
                a ``"YYYY-MM-DD"`` string; anything else is passed through
                untouched. Defaults server-side to **today + 7 days**. Dates in
                the past are a 422.
            passengers: 1-9 (default 1). Prices are for the whole party.

        Note:
            Three quirks of the payload:

            * ``offer.layovers`` is ``None`` for a direct flight, not ``[]``.
            * ``leg.departure_datetime``/``arrival_datetime`` are **naive local
              ISO strings** with no timezone — they are wall-clock time at the
              respective airport and are typed ``str`` so nothing treats them
              as UTC.
            * ``offer.price_usd`` silently keeps the **original currency** when
              the API's FX lookup fails; compare with ``original_currency``.

            No fares found is a normal ``200`` with ``flights=[]``.

        Raises:
            UnprocessableEntityError: non-IATA code, identical origin and
                destination, past date, or ``passengers`` outside 1-9 (422).
            ServiceUnavailableError: the upstream ticket search is unavailable
                (503) — retried automatically.
        """

        spec = _search_spec(
            origin=origin,
            destination=destination,
            date=date,
            passengers=passengers,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(TicketSearchResponse, result)
