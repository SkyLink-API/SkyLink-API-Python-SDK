"""Live ADS-B: tracked aircraft, feed statistics and ingest health.

Structure follows ``resources/weather.py``: module-level ``_*_spec()`` builders
own every endpoint detail, and the sync/async classes are thin
``execute(spec, request_options)`` wrappers that stay method-for-method
identical.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any, cast

from .._qs import format_bbox
from .._types import BBoxLike, RequestOptions, RequestSpec
from ..models.adsb import AdsbAircraft, AdsbAircraftList, AdsbHealth, AdsbStatistics

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["MAX_ITER_PAGE_SIZE", "Adsb", "AsyncAdsb"]

#: Upper bound for ``iter_aircraft(page_size=...)``.
#:
#: The endpoint itself declares ``limit`` with ``ge=1`` and **no** upper bound, so
#: ``aircraft(limit=25000)`` is legal and stays legal — see
#: :meth:`Adsb.aircraft`. The iterator caps it anyway: its whole point is to
#: fetch a large result in digestible pieces, and a "page size" of 100 000 is a
#: typo, not a request. Ask for the whole feed in one go with ``aircraft()`` if
#: that is really what you want.
#:
#: A larger ``page_size`` is **clamped to this value, not rejected** — the same
#: rule the TypeScript SDK follows: a ceiling the SDK invented must not break a
#: call the API would have accepted. Values below 1, and non-numbers, *are*
#: rejected, because there is no sensible page size to fall back to.
MAX_ITER_PAGE_SIZE = 1000


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


def _positive_int(value: object, name: str) -> int:
    """Read a caller supplied count, or raise :class:`ValueError` immediately.

    Rejects what has no sane interpretation — ``None``, a string, ``NaN``,
    infinity, zero and negatives — with a ``ValueError`` rather than letting it
    fail later inside a generator, where the traceback would point at the
    iteration site instead of the call. Booleans are refused too: ``True`` is a
    typo for ``1``, not a page size.
    """

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    number = int(value)
    if number < 1:
        raise ValueError(f"{name} must be at least 1, got {value!r}")
    return number


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


class _AircraftPager:
    """Offset bookkeeping shared by the sync and async ``iter_aircraft``.

    Pure state, no I/O: the caller asks for the next ``limit``, performs the
    request however it likes, and hands the page back. Keeping it here means the
    two iterators cannot drift apart on the part that is easy to get wrong —
    when to stop.

    Three stop conditions, in the order they are checked:

    #. the page is empty;
    #. the page carries the **same ``icao24`` set** as the previous one, which
       means the backend ignored ``offset`` and we would otherwise loop forever;
    #. the page is shorter than the requested limit, or ``max_items`` is reached.

    ``page_size`` above :data:`MAX_ITER_PAGE_SIZE` is **clamped**; ``page_size``
    below 1 and a ``max_items`` below 1 are :class:`ValueError`.
    """

    def __init__(self, *, page_size: int, max_items: int | None) -> None:
        self.page_size = min(_positive_int(page_size, "page_size"), MAX_ITER_PAGE_SIZE)
        self.max_items = None if max_items is None else _positive_int(max_items, "max_items")
        self.offset = 0
        self.yielded = 0
        self._fingerprint: tuple[str | None, ...] | None = None

    def next_limit(self) -> int | None:
        """Page size for the next request, or ``None`` when nothing is left to ask for.

        Shrinks to the remaining allowance so the last request of a
        ``max_items`` run does not fetch (and pay for) rows that are thrown away.
        """

        if self.max_items is None:
            return self.page_size
        remaining = self.max_items - self.yielded
        if remaining <= 0:
            return None
        return min(self.page_size, remaining)

    def consume(self, page: AdsbAircraftList, *, limit: int) -> tuple[list[AdsbAircraft], bool]:
        """Record a page; return the rows to yield and whether this was the last one."""

        rows = list(page.aircraft)
        if not rows:
            return [], True

        fingerprint = tuple(aircraft.icao24 for aircraft in rows)
        if fingerprint == self._fingerprint:
            return [], True
        self._fingerprint = fingerprint

        self.offset += len(rows)
        if self.max_items is not None:
            rows = rows[: self.max_items - self.yielded]
        self.yielded += len(rows)

        exhausted = len(page.aircraft) < limit
        capped = self.max_items is not None and self.yielded >= self.max_items
        return rows, exhausted or capped


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

    def iter_aircraft(
        self,
        *,
        page_size: int = 100,
        max_items: int | None = None,
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
        request_options: RequestOptions | None = None,
    ) -> Iterator[AdsbAircraft]:
        """Walk every matching aircraft, one page at a time.

        ``/adsb/aircraft`` is the only paginated endpoint in the API, and this
        drives its ``limit``/``offset`` for you: aircraft are yielded one by one,
        a new request is made when the current page runs out, and iteration ends
        on the first short or empty page::

            for aircraft in sky.adsb.iter_aircraft(airline="British", page_size=200):
                print(aircraft.icao24, aircraft.callsign)

        Takes the same filters as :meth:`aircraft` — minus ``limit``/``offset``,
        which it owns.

        Args:
            page_size: Rows per request, 1 to :data:`MAX_ITER_PAGE_SIZE` (1 000).
                The endpoint has no upper bound of its own; the cap is here so a
                mistyped page size cannot ask for the entire feed at once, and a
                larger value is **clamped down to it rather than rejected**. The
                last request of a ``max_items`` run is shrunk to what is still
                needed.
            max_items: Stop after this many aircraft. ``None`` (default) walks
                the whole result — with no filters that is 9-10k aircraft and
                ~100 requests, so pass a filter, a cap, or both.
            icao24: Exact 6-hex address; collapses the result to 0 or 1 aircraft.
            callsign: Case-insensitive partial match.
            lat: Centre latitude for a radius search (needs ``lon``/``radius``).
            lon: Centre longitude for a radius search.
            radius: Radius in **kilometres**, 0 < radius <= 1000.
            bbox: ``(lat1, lon1, lat2, lon2)`` south-west first, or a
                pre-formatted string.
            min_alt: Minimum altitude in feet.
            max_alt: Maximum altitude in feet.
            min_speed: Minimum ground speed in knots.
            max_speed: Maximum ground speed in knots.
            registration: Exact tail number.
            airline: Case-insensitive substring of the operator name.
            photos: Fetch photo URLs. Off by default, and best left off: it is
                slow and only covers the first 50 aircraft **of each page**.
            request_options: Per-request overrides applied to every page.

        Yields:
            :class:`~skylink_api.models.adsb.AdsbAircraft`, in the API's own
            ``icao24`` order.

        Note:
            The feed is **live**. Rows are sorted by ``icao24`` before paging, so
            walking ``offset`` is stable, but an aircraft that appears or drops
            out mid-walk can still be seen twice or missed — this is a snapshot
            of a moving target, not a transaction.

            Iteration also stops if a page repeats the previous page's ``icao24``
            set, which is what a backend that ignores ``offset`` looks like. That
            is a safety net against an infinite loop, not an expected condition.

        Raises:
            ValueError: ``page_size`` or ``max_items`` below 1, or not a finite
                number — raised from the call itself, before any request. A
                ``page_size`` **above** the cap is clamped, not an error.
            BadRequestError: same filter errors as :meth:`aircraft` (400).
        """

        pager = _AircraftPager(page_size=page_size, max_items=max_items)
        return self._iter_aircraft(
            pager,
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
            request_options=request_options,
        )

    def _iter_aircraft(
        self,
        pager: _AircraftPager,
        *,
        request_options: RequestOptions | None,
        **filters: Any,
    ) -> Iterator[AdsbAircraft]:
        while True:
            limit = pager.next_limit()
            if limit is None:
                return
            spec = _aircraft_spec(**filters, limit=limit, offset=pager.offset)
            page = cast(AdsbAircraftList, self._client.execute(spec, request_options))
            rows, done = pager.consume(page, limit=limit)
            yield from rows
            if done:
                return

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

    def iter_aircraft(
        self,
        *,
        page_size: int = 100,
        max_items: int | None = None,
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
        request_options: RequestOptions | None = None,
    ) -> AsyncIterator[AdsbAircraft]:
        """Walk every matching aircraft, one page at a time.

        Async twin of :meth:`Adsb.iter_aircraft`; see it for the pagination and
        live-feed notes. The method is **not** a coroutine — call it without
        ``await`` and drive it with ``async for``::

            async for aircraft in sky.adsb.iter_aircraft(bbox=(51.0, -1.0, 52.0, 0.5)):
                print(aircraft.icao24)

        Args:
            page_size: Rows per request, 1 to :data:`MAX_ITER_PAGE_SIZE` (1 000);
                a larger value is clamped down to the cap.
            max_items: Stop after this many aircraft; ``None`` walks everything.
            icao24: Exact 6-hex address.
            callsign: Case-insensitive partial match.
            lat: Centre latitude for a radius search.
            lon: Centre longitude for a radius search.
            radius: Radius in kilometres.
            bbox: ``(lat1, lon1, lat2, lon2)`` south-west first, or a string.
            min_alt: Minimum altitude in feet.
            max_alt: Maximum altitude in feet.
            min_speed: Minimum ground speed in knots.
            max_speed: Maximum ground speed in knots.
            registration: Exact tail number.
            airline: Case-insensitive substring of the operator name.
            photos: Fetch photo URLs (slow; off by default).
            request_options: Per-request overrides applied to every page.

        Raises:
            ValueError: ``page_size`` or ``max_items`` below 1, or not a finite
                number — raised from the call itself, before any request. A
                ``page_size`` above the cap is clamped, not an error.
        """

        pager = _AircraftPager(page_size=page_size, max_items=max_items)
        return self._iter_aircraft(
            pager,
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
            request_options=request_options,
        )

    async def _iter_aircraft(
        self,
        pager: _AircraftPager,
        *,
        request_options: RequestOptions | None,
        **filters: Any,
    ) -> AsyncIterator[AdsbAircraft]:
        while True:
            limit = pager.next_limit()
            if limit is None:
                return
            spec = _aircraft_spec(**filters, limit=limit, offset=pager.offset)
            result: Any = await self._client.execute(spec, request_options)
            rows, done = pager.consume(cast(AdsbAircraftList, result), limit=limit)
            for aircraft in rows:
                yield aircraft
            if done:
                return

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
