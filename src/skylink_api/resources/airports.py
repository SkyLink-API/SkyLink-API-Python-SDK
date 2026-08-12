"""Airport lookup and the three airport searches (by location, by IP, by text).

Structure mirrors :mod:`skylink_api.resources.weather`: literals, pure ``_*_spec``
builders, then the sync class and its async twin.

Two calls that look alike are deliberately kept apart:

* :meth:`Airports.search` takes a *code* and returns **one enriched airport** —
  the full record with runways, frequencies, navaids, country and region.
* :meth:`Airports.search_text` takes a *query* and returns a **ranked list** of
  slim airports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.airports import (
    AirportsByIPResponse,
    AirportsByLocationResponse,
    AirportsTextSearchResponse,
    EnrichedAirport,
)

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AirportType", "Airports", "AsyncAirports"]

#: Airport category, as classified by OurAirports. The API rejects anything else
#: with a 422, so the SDK offers exactly the seven real values.
AirportType = Literal[
    "large_airport",
    "medium_airport",
    "small_airport",
    "heliport",
    "seaplane_base",
    "closed",
    "balloonport",
]


# ── builders ─────────────────────────────────────────────────────────────────


def _search_spec(*, icao: str | None = None, iata: str | None = None) -> RequestSpec:
    """``GET /airports/search``.

    Raises:
        ValueError: neither or both codes given — the API answers 400 either way,
            so the check happens client side, before a request is made.
    """

    if icao is None and iata is None:
        raise ValueError("airports.search() needs exactly one of icao= or iata=, got neither")
    if icao is not None and iata is not None:
        raise ValueError("airports.search() needs exactly one of icao= or iata=, got both")

    return RequestSpec(
        method="GET",
        path="/airports/search",
        query={"icao": icao, "iata": iata},
        cast_to=EnrichedAirport,
    )


def _nearby_spec(
    *,
    lat: float,
    lon: float,
    radius: float = 50,
    type: AirportType | None = None,
    limit: int = 50,
) -> RequestSpec:
    """``GET /airports/search/location``."""

    return RequestSpec(
        method="GET",
        path="/airports/search/location",
        query={"lat": lat, "lon": lon, "radius": radius, "type": type, "limit": limit},
        cast_to=AirportsByLocationResponse,
    )


def _by_ip_spec(
    *,
    ip: str | None = None,
    radius: float = 100,
    type: AirportType | None = None,
    limit: int = 50,
) -> RequestSpec:
    """``GET /airports/search/ip``."""

    return RequestSpec(
        method="GET",
        path="/airports/search/ip",
        query={"ip": ip, "radius": radius, "type": type, "limit": limit},
        cast_to=AirportsByIPResponse,
    )


def _search_text_spec(*, q: str, limit: int = 20, type: AirportType | None = None) -> RequestSpec:
    """``GET /airports/search/text``."""

    return RequestSpec(
        method="GET",
        path="/airports/search/text",
        query={"q": q, "limit": limit, "type": type},
        cast_to=AirportsTextSearchResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Airports:
    """``sky.airports`` — airport lookup and search.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            jfk = sky.airports.search(icao="KJFK")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def search(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> EnrichedAirport:
        """One airport with everything attached — ``GET /airports/search``.

        Args:
            icao: 4-letter ICAO code (``"KJFK"``). Exactly 4 characters or 422.
            iata: 3-letter IATA code (``"JFK"``). Exactly 3 characters or 422.

        Pass **exactly one** of the two; both or neither is a client-side
        ``ValueError`` (the API would answer 400).

        Note:
            The enriched payload is forwarded from the source CSVs unnormalised,
            so ``frequencies[].frequency_mhz``, ``navaids[].frequency_khz`` and
            ``runways[].lighted``/``closed`` may arrive as **strings**
            (``"119.1"``, ``"1"``) and are typed ``str | int | float``.
            ``scheduled_service`` is ``"yes"``/``"no"``, not a bool.
            ``search_type`` echoes which parameter you used: ``"ICAO"`` or
            ``"IATA"``.

        Raises:
            ValueError: neither or both codes supplied (no request is sent).
            BadRequestError: code of the wrong length (400).
            NotFoundError: no airport carries that code (404).
        """

        spec = _search_spec(icao=icao, iata=iata)
        return cast(EnrichedAirport, self._client.execute(spec, request_options))

    def nearby(
        self,
        *,
        lat: float,
        lon: float,
        radius: float = 50,
        type: AirportType | None = None,
        limit: int = 50,
        request_options: RequestOptions | None = None,
    ) -> AirportsByLocationResponse:
        """Airports around a point — ``GET /airports/search/location``.

        Args:
            lat: Latitude in decimal degrees, -90 to 90.
            lon: Longitude in decimal degrees, -180 to 180.
            radius: Search radius in **kilometres**, 0 (exclusive) to 500.
                Default 50.
            type: Restrict to one airport category; omit for all of them.
            limit: Maximum results, 1-200 (default 50).

        Returns:
            An envelope whose ``airports`` are sorted by ``distance_km``,
            nearest first. ``airports_found`` counts the returned rows, so it
            never exceeds ``limit``.

        Note:
            An empty area is a normal ``200`` with ``airports=[]``, not a 404.

        Raises:
            UnprocessableEntityError: coordinates, radius or limit out of range (422).
        """

        spec = _nearby_spec(lat=lat, lon=lon, radius=radius, type=type, limit=limit)
        return cast(AirportsByLocationResponse, self._client.execute(spec, request_options))

    def by_ip(
        self,
        *,
        ip: str | None = None,
        radius: float = 100,
        type: AirportType | None = None,
        limit: int = 50,
        request_options: RequestOptions | None = None,
    ) -> AirportsByIPResponse:
        """Airports near an IP address — ``GET /airports/search/ip``.

        Args:
            ip: IP to geolocate. Omit it to use the address the request comes
                from — which, behind a proxy or from a server, is rarely the end
                user's.
            radius: Search radius in **kilometres**, 0 (exclusive) to 500.
                Default 100.
            type: Restrict to one airport category.
            limit: Maximum results, 1-200 (default 50).

        Note:
            A geolocation failure is **not** an exception: the API answers
            ``200`` with ``error`` set to the failure message, ``location=None``
            and no airports. Always check ``result.error`` first::

                result = sky.airports.by_ip(ip="203.0.113.7")
                if result.error:
                    ...

        Raises:
            UnprocessableEntityError: radius or limit out of range (422).
        """

        spec = _by_ip_spec(ip=ip, radius=radius, type=type, limit=limit)
        return cast(AirportsByIPResponse, self._client.execute(spec, request_options))

    def search_text(
        self,
        *,
        q: str,
        limit: int = 20,
        type: AirportType | None = None,
        request_options: RequestOptions | None = None,
    ) -> AirportsTextSearchResponse:
        """Free-text airport search — ``GET /airports/search/text``.

        Matches name, city, ICAO/IATA code, country and keywords, ranked by
        ``relevance_score`` (exact code matches first, larger airports favoured).

        Args:
            q: Query string, 2-100 characters.
            limit: Maximum results, 1-100 (default 20).
            type: Restrict to one airport category.

        Note:
            No match is a normal ``200`` with ``airports=[]``. Results are slim
            (:class:`~skylink_api.models.airports.AirportWithRelevance`) — call
            :meth:`search` with the ``ident`` for the full record.

        Raises:
            UnprocessableEntityError: query too short/long, or limit out of range (422).
        """

        spec = _search_text_spec(q=q, limit=limit, type=type)
        return cast(AirportsTextSearchResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncAirports:
    """``sky.airports`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Airports`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            jfk = await sky.airports.search(icao="KJFK")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def search(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> EnrichedAirport:
        """One airport with everything attached — ``GET /airports/search``.

        Args:
            icao: 4-letter ICAO code (``"KJFK"``). Exactly 4 characters or 422.
            iata: 3-letter IATA code (``"JFK"``). Exactly 3 characters or 422.

        Pass **exactly one** of the two; both or neither is a client-side
        ``ValueError`` (the API would answer 400).

        Note:
            The enriched payload is forwarded from the source CSVs unnormalised,
            so ``frequencies[].frequency_mhz``, ``navaids[].frequency_khz`` and
            ``runways[].lighted``/``closed`` may arrive as **strings**
            (``"119.1"``, ``"1"``) and are typed ``str | int | float``.
            ``scheduled_service`` is ``"yes"``/``"no"``, not a bool.
            ``search_type`` echoes which parameter you used: ``"ICAO"`` or
            ``"IATA"``.

        Raises:
            ValueError: neither or both codes supplied (no request is sent).
            BadRequestError: code of the wrong length (400).
            NotFoundError: no airport carries that code (404).
        """

        spec = _search_spec(icao=icao, iata=iata)
        result: Any = await self._client.execute(spec, request_options)
        return cast(EnrichedAirport, result)

    async def nearby(
        self,
        *,
        lat: float,
        lon: float,
        radius: float = 50,
        type: AirportType | None = None,
        limit: int = 50,
        request_options: RequestOptions | None = None,
    ) -> AirportsByLocationResponse:
        """Airports around a point — ``GET /airports/search/location``.

        Args:
            lat: Latitude in decimal degrees, -90 to 90.
            lon: Longitude in decimal degrees, -180 to 180.
            radius: Search radius in **kilometres**, 0 (exclusive) to 500.
                Default 50.
            type: Restrict to one airport category; omit for all of them.
            limit: Maximum results, 1-200 (default 50).

        Returns:
            An envelope whose ``airports`` are sorted by ``distance_km``,
            nearest first. ``airports_found`` counts the returned rows, so it
            never exceeds ``limit``.

        Note:
            An empty area is a normal ``200`` with ``airports=[]``, not a 404.

        Raises:
            UnprocessableEntityError: coordinates, radius or limit out of range (422).
        """

        spec = _nearby_spec(lat=lat, lon=lon, radius=radius, type=type, limit=limit)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AirportsByLocationResponse, result)

    async def by_ip(
        self,
        *,
        ip: str | None = None,
        radius: float = 100,
        type: AirportType | None = None,
        limit: int = 50,
        request_options: RequestOptions | None = None,
    ) -> AirportsByIPResponse:
        """Airports near an IP address — ``GET /airports/search/ip``.

        Args:
            ip: IP to geolocate. Omit it to use the address the request comes
                from — which, behind a proxy or from a server, is rarely the end
                user's.
            radius: Search radius in **kilometres**, 0 (exclusive) to 500.
                Default 100.
            type: Restrict to one airport category.
            limit: Maximum results, 1-200 (default 50).

        Note:
            A geolocation failure is **not** an exception: the API answers
            ``200`` with ``error`` set to the failure message, ``location=None``
            and no airports. Always check ``result.error`` first::

                result = await sky.airports.by_ip(ip="203.0.113.7")
                if result.error:
                    ...

        Raises:
            UnprocessableEntityError: radius or limit out of range (422).
        """

        spec = _by_ip_spec(ip=ip, radius=radius, type=type, limit=limit)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AirportsByIPResponse, result)

    async def search_text(
        self,
        *,
        q: str,
        limit: int = 20,
        type: AirportType | None = None,
        request_options: RequestOptions | None = None,
    ) -> AirportsTextSearchResponse:
        """Free-text airport search — ``GET /airports/search/text``.

        Matches name, city, ICAO/IATA code, country and keywords, ranked by
        ``relevance_score`` (exact code matches first, larger airports favoured).

        Args:
            q: Query string, 2-100 characters.
            limit: Maximum results, 1-100 (default 20).
            type: Restrict to one airport category.

        Note:
            No match is a normal ``200`` with ``airports=[]``. Results are slim
            (:class:`~skylink_api.models.airports.AirportWithRelevance`) — call
            :meth:`search` with the ``ident`` for the full record.

        Raises:
            UnprocessableEntityError: query too short/long, or limit out of range (422).
        """

        spec = _search_text_spec(q=q, limit=limit, type=type)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AirportsTextSearchResponse, result)
