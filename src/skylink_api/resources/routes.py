"""Route lookups (``/routes/*``).

Follows the layout of :mod:`skylink_api.resources.weather`: builders first, then
the sync class, then the async mirror.

:meth:`Routes.by_callsign` is the SDK's only method whose return type is a real
union — the endpoint answers with a VRS record or with an airline-level fallback
depending on whether the exact callsign is known. The ``cast_to`` is the
discriminated alias :data:`~skylink_api.models.routes.CallsignRoute`, so pydantic
picks the right model from the ``source`` key at parse time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.routes import (
    AirlineRoutesResult,
    AirportRoutesResponse,
    CallsignRoute,
    RoutePairsResponse,
    VrsRouteResult,
)

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncRoutes", "Routes"]

#: Which side of the airport the routes are listed for. ``"dep"`` = departures,
#: ``"arr"`` = arrivals, ``"both"`` = either (default). Anything else is a 422.
RouteDirection = Literal["dep", "arr", "both"]


# ── builders ─────────────────────────────────────────────────────────────────


def _by_callsign_spec(callsign: str) -> RequestSpec:
    """``GET /routes/callsign/{callsign}`` — discriminated union on ``source``."""

    return RequestSpec(
        method="GET",
        path=f"/routes/callsign/{callsign}",
        cast_to=CallsignRoute,
    )


def _by_airport_spec(
    code: str,
    *,
    direction: RouteDirection = "both",
    limit: int = 100,
) -> RequestSpec:
    """``GET /routes/airport/{code}``."""

    return RequestSpec(
        method="GET",
        path=f"/routes/airport/{code}",
        query={"direction": direction, "limit": limit},
        cast_to=AirportRoutesResponse,
    )


def _pairs_spec(
    *,
    departure: str | None = None,
    arrival: str | None = None,
    limit: int = 50,
) -> RequestSpec:
    """``GET /routes/pairs``."""

    return RequestSpec(
        method="GET",
        path="/routes/pairs",
        query={"departure": departure, "arrival": arrival, "limit": limit},
        cast_to=RoutePairsResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Routes:
    """``sky.routes`` — callsign→route resolution and route network data.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            route = sky.routes.by_callsign("BAW117")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def by_callsign(
        self,
        callsign: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> VrsRouteResult | AirlineRoutesResult:
        """Resolve a callsign to its route — ``GET /routes/callsign/{callsign}``.

        Returns **one of two different shapes**, told apart by ``source``::

            route = sky.routes.by_callsign("BAW117")
            if route.source == "vrs":
                print(route.departure_icao, "→", route.arrival_icao)  # ICAO
            else:
                print(route.airline_name, route.total_routes)         # no callsign!

        ``"vrs"`` is the exact match: this specific flight's airport pair, ICAO
        codes, ``confidence="high"``. ``"airline_routes"`` is the fallback the
        API falls back to when the callsign is not in VRS — it strips the digits
        and returns the *airline's* network (IATA codes, first 20 rows only,
        ``confidence="low"``) and carries **no ``callsign`` field at all**.

        Args:
            callsign: Flight callsign (``"BAW178"``, ``"DLH456"``). Upper-cased
                by the API; leading zeros in the number are normalised away.

        Raises:
            NotFoundError: neither the callsign nor its airline prefix is in the
                dataset — common, coverage is far from complete (404).
            ServiceUnavailableError: the route dataset is still loading after a
                restart; retry in a few seconds (503, retried automatically).
        """

        result: Any = self._client.execute(_by_callsign_spec(callsign), request_options)
        return cast("VrsRouteResult | AirlineRoutesResult", result)

    def by_airport(
        self,
        code: str,
        *,
        direction: RouteDirection = "both",
        limit: int = 100,
        request_options: RequestOptions | None = None,
    ) -> AirportRoutesResponse:
        """All known routes touching an airport — ``GET /routes/airport/{code}``.

        Args:
            code: IATA (3 letters) or ICAO (4 letters). ICAO is resolved to IATA
                before the lookup, so the rows always carry IATA codes even
                though ``response.code`` echoes what you sent.
            direction: ``"dep"``, ``"arr"`` or ``"both"`` (default).
            limit: 1-500, default 100. Rows are sorted by number of serving
                airlines first, so the limit keeps the busiest routes.

        Note:
            ``count`` is the number of rows *returned*, i.e. it saturates at
            ``limit`` — it is not the total number of matching routes. The
            endpoint has no offset, so there is no way to page past the limit.

        Raises:
            UnprocessableEntityError: ``direction`` outside the three values, or
                ``limit`` outside 1-500 (422).
            ServiceUnavailableError: route dataset still loading (503).
        """

        spec = _by_airport_spec(code, direction=direction, limit=limit)
        return cast(AirportRoutesResponse, self._client.execute(spec, request_options))

    def pairs(
        self,
        *,
        departure: str | None = None,
        arrival: str | None = None,
        limit: int = 50,
        request_options: RequestOptions | None = None,
    ) -> RoutePairsResponse:
        """Route pair statistics — ``GET /routes/pairs``.

        Args:
            departure: Filter by origin, IATA or ICAO. ICAO is resolved to IATA
                first.
            arrival: Filter by destination, IATA or ICAO.
            limit: 1-500, default 50.

        Note:
            Both filters are optional, but omitting them scans the whole dataset
            and returns an essentially arbitrary top-``limit`` slice — pass at
            least one. Rows are sorted by number of serving airlines, descending.

        Raises:
            UnprocessableEntityError: ``limit`` outside 1-500 (422).
            ServiceUnavailableError: route dataset still loading (503).
        """

        spec = _pairs_spec(departure=departure, arrival=arrival, limit=limit)
        return cast(RoutePairsResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncRoutes:
    """``sky.routes`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Routes`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            route = await sky.routes.by_callsign("BAW117")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def by_callsign(
        self,
        callsign: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> VrsRouteResult | AirlineRoutesResult:
        """Resolve a callsign to its route — ``GET /routes/callsign/{callsign}``.

        Returns **one of two different shapes**, told apart by ``source``::

            route = await sky.routes.by_callsign("BAW117")
            if route.source == "vrs":
                print(route.departure_icao, "→", route.arrival_icao)  # ICAO
            else:
                print(route.airline_name, route.total_routes)         # no callsign!

        ``"vrs"`` is the exact match: this specific flight's airport pair, ICAO
        codes, ``confidence="high"``. ``"airline_routes"`` is the fallback the
        API falls back to when the callsign is not in VRS — it strips the digits
        and returns the *airline's* network (IATA codes, first 20 rows only,
        ``confidence="low"``) and carries **no ``callsign`` field at all**.

        Args:
            callsign: Flight callsign (``"BAW178"``, ``"DLH456"``). Upper-cased
                by the API; leading zeros in the number are normalised away.

        Raises:
            NotFoundError: neither the callsign nor its airline prefix is in the
                dataset — common, coverage is far from complete (404).
            ServiceUnavailableError: the route dataset is still loading after a
                restart; retry in a few seconds (503, retried automatically).
        """

        result: Any = await self._client.execute(_by_callsign_spec(callsign), request_options)
        return cast("VrsRouteResult | AirlineRoutesResult", result)

    async def by_airport(
        self,
        code: str,
        *,
        direction: RouteDirection = "both",
        limit: int = 100,
        request_options: RequestOptions | None = None,
    ) -> AirportRoutesResponse:
        """All known routes touching an airport — ``GET /routes/airport/{code}``.

        Args:
            code: IATA (3 letters) or ICAO (4 letters). ICAO is resolved to IATA
                before the lookup, so the rows always carry IATA codes even
                though ``response.code`` echoes what you sent.
            direction: ``"dep"``, ``"arr"`` or ``"both"`` (default).
            limit: 1-500, default 100. Rows are sorted by number of serving
                airlines first, so the limit keeps the busiest routes.

        Note:
            ``count`` is the number of rows *returned*, i.e. it saturates at
            ``limit`` — it is not the total number of matching routes. The
            endpoint has no offset, so there is no way to page past the limit.

        Raises:
            UnprocessableEntityError: ``direction`` outside the three values, or
                ``limit`` outside 1-500 (422).
            ServiceUnavailableError: route dataset still loading (503).
        """

        spec = _by_airport_spec(code, direction=direction, limit=limit)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AirportRoutesResponse, result)

    async def pairs(
        self,
        *,
        departure: str | None = None,
        arrival: str | None = None,
        limit: int = 50,
        request_options: RequestOptions | None = None,
    ) -> RoutePairsResponse:
        """Route pair statistics — ``GET /routes/pairs``.

        Args:
            departure: Filter by origin, IATA or ICAO. ICAO is resolved to IATA
                first.
            arrival: Filter by destination, IATA or ICAO.
            limit: 1-500, default 50.

        Note:
            Both filters are optional, but omitting them scans the whole dataset
            and returns an essentially arbitrary top-``limit`` slice — pass at
            least one. Rows are sorted by number of serving airlines, descending.

        Raises:
            UnprocessableEntityError: ``limit`` outside 1-500 (422).
            ServiceUnavailableError: route dataset still loading (503).
        """

        spec = _pairs_spec(departure=departure, arrival=arrival, limit=limit)
        result: Any = await self._client.execute(spec, request_options)
        return cast(RoutePairsResponse, result)
