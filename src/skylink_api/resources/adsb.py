"""Live ADS-B: tracked aircraft, feed statistics and ingest health.

Structure follows ``resources/weather.py``: module-level ``_*_spec()`` builders
own every endpoint detail, and the sync/async classes are thin
``execute(spec, request_options)`` wrappers that stay method-for-method
identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._qs import format_bbox
from .._types import BBoxLike, RequestOptions, RequestSpec
from ..models.adsb import AdsbAircraftList, AdsbHealth, AdsbStatistics

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["Adsb", "AsyncAdsb"]


# ── builders ─────────────────────────────────────────────────────────────────


def _aircraft_spec(
    *,
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
    offset: int | None = None,
) -> RequestSpec:
    """``GET /adsb/aircraft``."""

    return RequestSpec(
        method="GET",
        path="/adsb/aircraft",
        query={
            "icao24": icao24,
            "callsign": callsign,
            "lat": lat,
            "lon": lon,
            "radius": radius,
            "bbox": format_bbox(bbox) if bbox is not None else None,
            "min_alt": min_alt,
            "max_alt": max_alt,
            "min_speed": min_speed,
            "max_speed": max_speed,
            "registration": registration,
            "airline": airline,
            "photos": photos,
            "limit": limit,
            "offset": offset,
        },
        cast_to=AdsbAircraftList,
    )


def _statistics_spec() -> RequestSpec:
    """``GET /adsb/aircraft/statistics``."""

    return RequestSpec(
        method="GET",
        path="/adsb/aircraft/statistics",
        cast_to=AdsbStatistics,
    )


def _health_spec() -> RequestSpec:
    """``GET /adsb/health``."""

    return RequestSpec(method="GET", path="/adsb/health", cast_to=AdsbHealth)


# ── sync ─────────────────────────────────────────────────────────────────────


class Adsb:
    """``sky.adsb`` — the live ADS-B feed.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            page = sky.adsb.aircraft(bbox=(51.0, -1.0, 52.0, 0.5), limit=100)
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def aircraft(
        self,
        *,
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
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> AdsbAircraftList:
        """Currently tracked aircraft — ``GET /adsb/aircraft``.

        With no filters this returns the **entire** feed, which routinely runs to
        9-10k aircraft. Narrow it with ``bbox``/``radius`` or page it with
        ``limit``.

        Args:
            icao24: Exact ICAO 24-bit address, 6 hex characters. An exact match,
                so it collapses the result to 0 or 1 aircraft regardless of the
                other filters.
            callsign: Case-insensitive **partial** match (``"BAW"`` matches every
                British Airways callsign).
            lat: Centre latitude for a radius search. Requires ``lon`` and
                ``radius``; supplying only some of the three is a 400.
            lon: Centre longitude for a radius search.
            radius: Search radius in **kilometres**, 0 < radius <= 1000.
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first; a
                pre-formatted ``"lat1,lon1,lat2,lon2"`` string also works. The
                API rejects boxes where ``lat1 >= lat2`` or ``lon1 >= lon2``.
            min_alt: Minimum altitude in feet, 0-60000.
            max_alt: Maximum altitude in feet, 0-60000. Must exceed ``min_alt``.
            min_speed: Minimum ground speed in knots, 0-1000.
            max_speed: Maximum ground speed in knots, 0-1000. Must exceed
                ``min_speed``.
            registration: Exact tail number; like ``icao24`` this narrows the
                result to at most one aircraft.
            airline: Case-insensitive substring of the operator name.
            photos: Fetch photo URLs from the photo provider. **Defaults to
                ``False`` here** (unlike the aircraft lookup endpoints, where it
                defaults to ``True``) because it is slow and only covers the
                first 50 aircraft of the page.
            limit: Page size. **No upper bound** — pass 20000 to take the whole
                feed in one go if you really want it. Omit for no pagination.
            offset: Aircraft to skip. Results are sorted by ``icao24`` before
                paging, so walking ``offset`` is stable between calls.

        Note:
            ``total_count`` is the match count **before** paging — use it, not
            ``len(result.aircraft)``, to decide whether another page exists.

            Aircraft without a position are normal: a Mode S transponder that
            broadcasts only identity yields a row where ``latitude``,
            ``longitude``, ``altitude`` and the rest are ``None``.

        Raises:
            BadRequestError: malformed bounding box, an incomplete lat/lon/radius
                trio, ``min_alt >= max_alt`` or ``min_speed >= max_speed`` (400).
        """

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
            offset=offset,
        )
        return cast(AdsbAircraftList, self._client.execute(spec, request_options))

    def statistics(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> AdsbStatistics:
        """Feed-wide counters — ``GET /adsb/aircraft/statistics``.

        Covers the whole feed; the filters accepted by :meth:`aircraft` do not
        apply here.

        Note:
            ``altitude_stats`` is computed from airborne aircraft only and comes
            back as an empty object (every field ``None``) when none of them
            report an altitude — an empty feed is a normal ``200``, not a 404.
        """

        return cast(AdsbStatistics, self._client.execute(_statistics_spec(), request_options))

    def health(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> AdsbHealth:
        """Ingest pipeline health — ``GET /adsb/health``.

        Note:
            ``status`` is one of ``"healthy"``, ``"offline"``,
            ``"connected_no_data"`` or ``"degraded"``, typed as ``str`` so a new
            backend state never becomes a parse error. An unhealthy feed still
            answers ``200`` — this endpoint reports trouble, it does not raise
            it.
        """

        return cast(AdsbHealth, self._client.execute(_health_spec(), request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncAdsb:
    """``sky.adsb`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Adsb`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            page = await sky.adsb.aircraft(bbox=(51.0, -1.0, 52.0, 0.5), limit=100)
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def aircraft(
        self,
        *,
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
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> AdsbAircraftList:
        """Currently tracked aircraft — ``GET /adsb/aircraft``.

        With no filters this returns the **entire** feed, which routinely runs to
        9-10k aircraft. Narrow it with ``bbox``/``radius`` or page it with
        ``limit``.

        Args:
            icao24: Exact ICAO 24-bit address, 6 hex characters. An exact match,
                so it collapses the result to 0 or 1 aircraft regardless of the
                other filters.
            callsign: Case-insensitive **partial** match (``"BAW"`` matches every
                British Airways callsign).
            lat: Centre latitude for a radius search. Requires ``lon`` and
                ``radius``; supplying only some of the three is a 400.
            lon: Centre longitude for a radius search.
            radius: Search radius in **kilometres**, 0 < radius <= 1000.
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first; a
                pre-formatted ``"lat1,lon1,lat2,lon2"`` string also works. The
                API rejects boxes where ``lat1 >= lat2`` or ``lon1 >= lon2``.
            min_alt: Minimum altitude in feet, 0-60000.
            max_alt: Maximum altitude in feet, 0-60000. Must exceed ``min_alt``.
            min_speed: Minimum ground speed in knots, 0-1000.
            max_speed: Maximum ground speed in knots, 0-1000. Must exceed
                ``min_speed``.
            registration: Exact tail number; like ``icao24`` this narrows the
                result to at most one aircraft.
            airline: Case-insensitive substring of the operator name.
            photos: Fetch photo URLs from the photo provider. **Defaults to
                ``False`` here** (unlike the aircraft lookup endpoints, where it
                defaults to ``True``) because it is slow and only covers the
                first 50 aircraft of the page.
            limit: Page size. **No upper bound** — pass 20000 to take the whole
                feed in one go if you really want it. Omit for no pagination.
            offset: Aircraft to skip. Results are sorted by ``icao24`` before
                paging, so walking ``offset`` is stable between calls.

        Note:
            ``total_count`` is the match count **before** paging — use it, not
            ``len(result.aircraft)``, to decide whether another page exists.

            Aircraft without a position are normal: a Mode S transponder that
            broadcasts only identity yields a row where ``latitude``,
            ``longitude``, ``altitude`` and the rest are ``None``.

        Raises:
            BadRequestError: malformed bounding box, an incomplete lat/lon/radius
                trio, ``min_alt >= max_alt`` or ``min_speed >= max_speed`` (400).
        """

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
            offset=offset,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(AdsbAircraftList, result)

    async def statistics(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> AdsbStatistics:
        """Feed-wide counters — ``GET /adsb/aircraft/statistics``.

        Covers the whole feed; the filters accepted by :meth:`aircraft` do not
        apply here.

        Note:
            ``altitude_stats`` is computed from airborne aircraft only and comes
            back as an empty object (every field ``None``) when none of them
            report an altitude — an empty feed is a normal ``200``, not a 404.
        """

        result: Any = await self._client.execute(_statistics_spec(), request_options)
        return cast(AdsbStatistics, result)

    async def health(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> AdsbHealth:
        """Ingest pipeline health — ``GET /adsb/health``.

        Note:
            ``status`` is one of ``"healthy"``, ``"offline"``,
            ``"connected_no_data"`` or ``"degraded"``, typed as ``str`` so a new
            backend state never becomes a parse error. An unhealthy feed still
            answers ``200`` — this endpoint reports trouble, it does not raise
            it.
        """

        result: Any = await self._client.execute(_health_spec(), request_options)
        return cast(AdsbHealth, result)
