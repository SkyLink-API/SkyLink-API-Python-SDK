"""Public entry points: :class:`SkyLink` (blocking) and :class:`AsyncSkyLink`.

Besides the constructor, the ``request()`` escape hatch and lifecycle
management, this module attaches the resource namespaces. Each one is a
``functools.cached_property`` so that building a client stays free and only the
namespaces a program actually touches are instantiated.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from functools import cached_property
from typing import Any

import httpx

from ._base_client import AsyncAPIClient, SyncAPIClient, _default_async_sleep
from ._config import resolve_config
from ._types import (
    NOT_GIVEN,
    HistoryPlan,
    NotGiven,
    NotGivenOr,
    Provider,
    RequestOptions,
    RequestSpec,
    ResponseKind,
)
from .helpers.cache import CacheProtocol
from .models.distance import DistanceResponse
from .models.flight_status import FlightStatusResponse
from .resources.adsb import Adsb, AsyncAdsb
from .resources.aircraft import Aircraft, AsyncAircraft
from .resources.airlines import Airlines, AsyncAirlines
from .resources.airports import Airports, AsyncAirports
from .resources.batch import AsyncBatch, Batch
from .resources.briefing import AsyncBriefing, Briefing
from .resources.carbon import AsyncCarbon, Carbon
from .resources.charts import AsyncCharts, Charts
from .resources.compose import AsyncCompose, Compose
from .resources.delays import AsyncDelays, Delays
from .resources.distance import AsyncDistance, Distance, DistanceUnit
from .resources.flight_status import AsyncFlightStatus, FlightStatus
from .resources.geo import AsyncGeo, Geo
from .resources.history import AsyncHistory, History
from .resources.ml import AsyncMl, Ml
from .resources.navaids import AsyncNavaids, Navaids
from .resources.notams import AsyncNotams, Notams
from .resources.poll import AsyncPoll, Poll
from .resources.routes import AsyncRoutes, Routes
from .resources.schedules import AsyncSchedules, Schedules
from .resources.tickets import AsyncTickets, Tickets
from .resources.weather import AsyncWeather, Weather
from .resources.webhooks import AsyncWebhooks, Webhooks

__all__ = ["AsyncSkyLink", "SkyLink"]


class SkyLink(SyncAPIClient):
    """Blocking SkyLink API client.

    Example::

        from skylink_api import SkyLink

        with SkyLink(api_key="...") as sky:            # RapidAPI by default
            data = sky.request("GET", "/weather/metar/KJFK")

    Args:
        api_key: API key. Falls back to the environment — ``RAPIDAPI_KEY`` and then
            ``SKYLINK_API_KEY`` on the rapidapi channel, ``SKYLINK_API_KEY`` on the
            direct one. Required unless ``base_url`` is given.
        provider: ``"rapidapi"`` (default, ``https://skylink-api.p.rapidapi.com``, no
            version prefix) or ``"direct"`` (``https://data.skylinkapi.com/v3.1``).
        base_url: Override the endpoint entirely — used verbatim, no version is
            appended. With an explicit ``base_url`` a missing key is not an error,
            so staging instances running ``DISABLE_AUTH=true`` just work.
        timeout: Seconds or an :class:`httpx.Timeout`; ``None`` disables timeouts.
            Default: connect 5s, read/write 30s, pool 5s.
        max_retries: Retries for 429/500/502/503/504 and transport failures
            (default 3).
        history_plan: ``"ultra"`` (default) or ``"mega"`` — selects the
            ``/{plan}/history`` URL prefix; can be overridden per call.
        default_headers: Extra headers merged into every request.
        cache: Opt-in response cache — see
            :class:`~skylink_api.helpers.cache.MemoryCache`. Only successful GETs
            are cached, and only for operations with a non-zero TTL, so a cache
            without rules changes nothing.
        http_client: Bring your own httpx client (proxies, custom transport).
        sleep: Backoff sleep function — injection point for tests.
        environ: Environment mapping used for key lookup (defaults to ``os.environ``).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider: Provider | None = None,
        base_url: str | None = None,
        timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
        max_retries: int | None = None,
        history_plan: HistoryPlan | None = None,
        default_headers: Mapping[str, str] | None = None,
        cache: CacheProtocol | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        config = resolve_config(
            api_key=api_key,
            provider=provider,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            history_plan=history_plan,
            default_headers=default_headers,
            environ=environ,
        )
        super().__init__(config, http_client=http_client, sleep=sleep, cache=cache)

    @property
    def api_key(self) -> str | None:
        return self._config.api_key

    # ── alternative constructors ─────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        *,
        provider: Provider | None = None,
        base_url: str | None = None,
        timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
        max_retries: int | None = None,
        history_plan: HistoryPlan | None = None,
        default_headers: Mapping[str, str] | None = None,
        cache: CacheProtocol | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        environ: Mapping[str, str] | None = None,
    ) -> SkyLink:
        """Build a client whose key comes from the environment — and says so::

            sky = SkyLink.from_env()                      # RAPIDAPI_KEY / SKYLINK_API_KEY
            sky = SkyLink.from_env(provider="direct")     # SKYLINK_API_KEY

        Same behaviour as ``SkyLink()`` with no ``api_key``, minus the ambiguity:
        there is no ``api_key`` argument to accidentally pass ``None`` to, so a
        reader can see at a glance where the credential comes from. Everything
        else may still be overridden. A missing variable raises
        :class:`~skylink_api.AuthenticationError` (unless ``base_url`` is given).
        """

        return cls(
            provider=provider,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            history_plan=history_plan,
            default_headers=default_headers,
            cache=cache,
            http_client=http_client,
            sleep=sleep,
            environ=environ,
        )

    def with_options(
        self,
        *,
        timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
        max_retries: NotGivenOr[int] = NOT_GIVEN,
        history_plan: NotGivenOr[HistoryPlan] = NOT_GIVEN,
        default_headers: NotGivenOr[Mapping[str, str]] = NOT_GIVEN,
        cache: NotGivenOr[CacheProtocol | None] = NOT_GIVEN,
    ) -> SkyLink:
        """A clone with some settings changed, sharing this client's connections::

            patient = sky.with_options(timeout=120.0, max_retries=0)
            archive = sky.with_options(history_plan="mega")

        The clone **reuses the same** :class:`httpx.Client`, so no second
        connection pool is opened, and it starts with fresh namespaces (nothing
        is carried over from ``sky.weather`` & co.). Registered quota hooks are
        copied as a snapshot; later ``on_rate_limit`` calls on either client do
        not reach the other.

        Ownership of the transport stays with the original: closing the clone
        (``close()`` or leaving its ``with`` block) leaves the pool open, and
        closing the original closes it for both. Keep the original alive for as
        long as any clone is in use.

        ``default_headers`` replaces the mapping rather than merging into it, so a
        clone can also drop a header. Passing ``cache=None`` disables caching for
        the clone; omitting it keeps this client's cache (shared, not copied).
        """

        config = self._config.with_overrides(
            timeout=timeout,
            max_retries=max_retries,
            history_plan=history_plan,
            default_headers=default_headers,
        )
        cls = type(self)
        clone = cls.__new__(cls)
        SyncAPIClient.__init__(
            clone,
            config,
            http_client=self._http_client,
            sleep=self._sleep,
            cache=self._cache if isinstance(cache, NotGiven) else cache,
            owns_transport=False,
        )
        clone._copy_hooks_from(self)
        return clone

    # ── namespaces ───────────────────────────────────────────────────────────

    @cached_property
    def weather(self) -> Weather:
        """METAR, TAF, winds aloft, PIREPs and AIRMET/SIGMET (``/weather/*``)."""

        return Weather(self)

    @cached_property
    def airports(self) -> Airports:
        """Airport lookup by code, radius, text or client IP (``/airports/*``)."""

        return Airports(self)

    @cached_property
    def airlines(self) -> Airlines:
        """Airline lookup by IATA/ICAO code, name or country (``/airlines/search``)."""

        return Airlines(self)

    @cached_property
    def navaids(self) -> Navaids:
        """VOR/NDB/DME navigation aid search (``/navaids``)."""

        return Navaids(self)

    @cached_property
    def geo(self) -> Geo:
        """Country and region reference data (``/countries``, ``/regions``)."""

        return Geo(self)

    @cached_property
    def adsb(self) -> Adsb:
        """Live ADS-B positions, feed statistics and health (``/adsb/*``)."""

        return Adsb(self)

    @cached_property
    def aircraft(self) -> Aircraft:
        """Airframe registry, type performance and database stats (``/aircraft/*``)."""

        return Aircraft(self)

    @cached_property
    def charts(self) -> Charts:
        """Aerodrome chart links grouped by category (``/charts/*``)."""

        return Charts(self)

    @cached_property
    def delays(self) -> Delays:
        """Live FAA NAS status — ground stops, ground delays, closures."""

        return Delays(self)

    @cached_property
    def notams(self) -> Notams:
        """NOTAMs from the FAA SWIM feed (``/notams/{icao}``)."""

        return Notams(self)

    @cached_property
    def schedules(self) -> Schedules:
        """Airport departure and arrival boards (``/schedules/{direction}``)."""

        return Schedules(self)

    @cached_property
    def ml(self) -> Ml:
        """Model-backed predictions, e.g. block time (``/ml/flight-time``)."""

        return Ml(self)

    @cached_property
    def carbon(self) -> Carbon:
        """CO2 emission estimates per passenger and cabin (``/carbon/estimate``)."""

        return Carbon(self)

    @cached_property
    def briefing(self) -> Briefing:
        """Pre-flight briefings as JSON, text or PDF (``/briefing/*``)."""

        return Briefing(self)

    @cached_property
    def routes(self) -> Routes:
        """Callsign to route resolution and route network data (``/routes/*``)."""

        return Routes(self)

    @cached_property
    def tickets(self) -> Tickets:
        """Ticket availability and pricing (``/tickets/search``)."""

        return Tickets(self)

    @cached_property
    def webhooks(self) -> Webhooks:
        """Push subscriptions for flight status changes (``/webhooks/*``)."""

        return Webhooks(self)

    @cached_property
    def history(self) -> History:
        """Archived ADS-B flights, tracks and positions (``/{plan}/history/*``)."""

        return History(self)

    @cached_property
    def batch(self) -> Batch:
        """The same call for many identifiers, bounded and failure tolerant.

        ``sky.batch.metars([...])`` and friends return
        ``{identifier: model | SkyLinkError}`` — see
        :class:`~skylink_api.resources.batch.Batch`.
        """

        return Batch(self)

    @cached_property
    def poll(self) -> Poll:
        """Endpoints on a timer, as iterators.

        ``sky.poll.flight_status("BA123")`` yields status changes until the
        flight lands; ``sky.poll.adsb(bbox=...)`` yields feed diffs for a live
        map. Both survive 429/5xx and take an injectable ``sleep`` — see
        :class:`~skylink_api.resources.poll.Poll`.
        """

        return Poll(self)

    @cached_property
    def compose(self) -> Compose:
        """Multi-endpoint aggregates: whole pages in one call.

        ``sky.compose.airport_brief("EGLL")`` fetches eight endpoints in
        parallel and hands back one object where every failed part is ``None``
        with its error in ``errors`` — see
        :class:`~skylink_api.resources.compose.Compose`.
        """

        return Compose(self)

    # ── one-method namespaces, exposed as client methods ─────────────────────

    @cached_property
    def _flight_status(self) -> FlightStatus:
        return FlightStatus(self)

    @cached_property
    def _distance(self) -> Distance:
        return Distance(self)

    def flight_status(
        self,
        flight_number: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> FlightStatusResponse:
        """Status of one flight — ``GET /flight_status/{flight_number}``::

            status = sky.flight_status("BA123")

        Args:
            flight_number: IATA (``"BA123"``) or ICAO (``"BAW123"``) form, 2-10
                characters; the API upper-cases and strips it.

        Note:
            The payload is scraped: every field is an optional string, empty
            values arrive as ``""`` or ``"--"``, and ``departure``/``arrival``
            are **not** the same shape. See
            :meth:`~skylink_api.resources.flight_status.FlightStatus.get`.
        """

        return self._flight_status.get(flight_number, request_options=request_options)

    def distance(
        self,
        *,
        from_icao: str | None = None,
        to_icao: str | None = None,
        from_lat: float | None = None,
        from_lon: float | None = None,
        to_lat: float | None = None,
        to_lon: float | None = None,
        unit: DistanceUnit = "nm",
        request_options: RequestOptions | None = None,
    ) -> DistanceResponse:
        """Great-circle distance and initial bearing — ``GET /distance``::

            leg = sky.distance(from_icao="KJFK", to_icao="EGLL")
            leg = sky.distance(from_lat=40.64, from_lon=-73.78, to_icao="EGLL", unit="km")

        Each endpoint is given **either** as an airport code **or** as a
        latitude/longitude pair, and the two styles mix freely. See
        :meth:`~skylink_api.resources.distance.Distance.calculate` for the full
        documentation.
        """

        return self._distance.calculate(
            from_icao=from_icao,
            to_icao=to_icao,
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
            unit=unit,
            request_options=request_options,
        )

    # ── escape hatch ─────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        headers: Mapping[str, str] | None = None,
        response_kind: ResponseKind = "json",
        cast_to: Any = None,
        options: RequestOptions | None = None,
    ) -> Any:
        """Escape hatch for endpoints this SDK does not model yet.

        Applies the same auth, retry and error handling as the typed methods::

            sky.request("GET", "/weather/metar/KJFK", query={"parsed": True})
        """

        spec = RequestSpec(
            method=method.upper(),
            path=path,
            query=query,
            json_body=json_body,
            headers=headers,
            response_kind=response_kind,
            cast_to=cast_to,
        )
        return self.execute(spec, options)


class AsyncSkyLink(AsyncAPIClient):
    """Asyncio SkyLink API client.

    Example::

        from skylink_api import AsyncSkyLink

        async with AsyncSkyLink(api_key="...") as sky:
            data = await sky.request("GET", "/weather/metar/KJFK")

    Takes the same arguments as :class:`SkyLink`, except ``http_client`` is an
    :class:`httpx.AsyncClient` and ``sleep`` is an awaitable callable.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider: Provider | None = None,
        base_url: str | None = None,
        timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
        max_retries: int | None = None,
        history_plan: HistoryPlan | None = None,
        default_headers: Mapping[str, str] | None = None,
        cache: CacheProtocol | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = _default_async_sleep,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        config = resolve_config(
            api_key=api_key,
            provider=provider,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            history_plan=history_plan,
            default_headers=default_headers,
            environ=environ,
        )
        super().__init__(config, http_client=http_client, sleep=sleep, cache=cache)

    @property
    def api_key(self) -> str | None:
        return self._config.api_key

    # ── alternative constructors ─────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        *,
        provider: Provider | None = None,
        base_url: str | None = None,
        timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
        max_retries: int | None = None,
        history_plan: HistoryPlan | None = None,
        default_headers: Mapping[str, str] | None = None,
        cache: CacheProtocol | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = _default_async_sleep,
        environ: Mapping[str, str] | None = None,
    ) -> AsyncSkyLink:
        """Build a client whose key comes from the environment — see
        :meth:`SkyLink.from_env`::

            async with AsyncSkyLink.from_env() as sky:
                ...
        """

        return cls(
            provider=provider,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            history_plan=history_plan,
            default_headers=default_headers,
            cache=cache,
            http_client=http_client,
            sleep=sleep,
            environ=environ,
        )

    def with_options(
        self,
        *,
        timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
        max_retries: NotGivenOr[int] = NOT_GIVEN,
        history_plan: NotGivenOr[HistoryPlan] = NOT_GIVEN,
        default_headers: NotGivenOr[Mapping[str, str]] = NOT_GIVEN,
        cache: NotGivenOr[CacheProtocol | None] = NOT_GIVEN,
    ) -> AsyncSkyLink:
        """A clone with some settings changed, sharing this client's connections.

        Not a coroutine — the clone is built in place::

            archive = sky.with_options(history_plan="mega")
            flights = await archive.history.flights(icao24="4ca7b3")

        Same semantics as :meth:`SkyLink.with_options`: the
        :class:`httpx.AsyncClient` is reused, namespaces are not inherited, quota
        hooks are copied as a snapshot, and the clone does not own the transport —
        ``await clone.aclose()`` leaves the original's pool open.
        """

        config = self._config.with_overrides(
            timeout=timeout,
            max_retries=max_retries,
            history_plan=history_plan,
            default_headers=default_headers,
        )
        cls = type(self)
        clone = cls.__new__(cls)
        AsyncAPIClient.__init__(
            clone,
            config,
            http_client=self._http_client,
            sleep=self._sleep,
            cache=self._cache if isinstance(cache, NotGiven) else cache,
            owns_transport=False,
        )
        clone._copy_hooks_from(self)
        return clone

    # ── namespaces ───────────────────────────────────────────────────────────

    @cached_property
    def weather(self) -> AsyncWeather:
        """METAR, TAF, winds aloft, PIREPs and AIRMET/SIGMET (``/weather/*``)."""

        return AsyncWeather(self)

    @cached_property
    def airports(self) -> AsyncAirports:
        """Airport lookup by code, radius, text or client IP (``/airports/*``)."""

        return AsyncAirports(self)

    @cached_property
    def airlines(self) -> AsyncAirlines:
        """Airline lookup by IATA/ICAO code, name or country (``/airlines/search``)."""

        return AsyncAirlines(self)

    @cached_property
    def navaids(self) -> AsyncNavaids:
        """VOR/NDB/DME navigation aid search (``/navaids``)."""

        return AsyncNavaids(self)

    @cached_property
    def geo(self) -> AsyncGeo:
        """Country and region reference data (``/countries``, ``/regions``)."""

        return AsyncGeo(self)

    @cached_property
    def adsb(self) -> AsyncAdsb:
        """Live ADS-B positions, feed statistics and health (``/adsb/*``)."""

        return AsyncAdsb(self)

    @cached_property
    def aircraft(self) -> AsyncAircraft:
        """Airframe registry, type performance and database stats (``/aircraft/*``)."""

        return AsyncAircraft(self)

    @cached_property
    def charts(self) -> AsyncCharts:
        """Aerodrome chart links grouped by category (``/charts/*``)."""

        return AsyncCharts(self)

    @cached_property
    def delays(self) -> AsyncDelays:
        """Live FAA NAS status — ground stops, ground delays, closures."""

        return AsyncDelays(self)

    @cached_property
    def notams(self) -> AsyncNotams:
        """NOTAMs from the FAA SWIM feed (``/notams/{icao}``)."""

        return AsyncNotams(self)

    @cached_property
    def schedules(self) -> AsyncSchedules:
        """Airport departure and arrival boards (``/schedules/{direction}``)."""

        return AsyncSchedules(self)

    @cached_property
    def ml(self) -> AsyncMl:
        """Model-backed predictions, e.g. block time (``/ml/flight-time``)."""

        return AsyncMl(self)

    @cached_property
    def carbon(self) -> AsyncCarbon:
        """CO2 emission estimates per passenger and cabin (``/carbon/estimate``)."""

        return AsyncCarbon(self)

    @cached_property
    def briefing(self) -> AsyncBriefing:
        """Pre-flight briefings as JSON, text or PDF (``/briefing/*``)."""

        return AsyncBriefing(self)

    @cached_property
    def routes(self) -> AsyncRoutes:
        """Callsign to route resolution and route network data (``/routes/*``)."""

        return AsyncRoutes(self)

    @cached_property
    def tickets(self) -> AsyncTickets:
        """Ticket availability and pricing (``/tickets/search``)."""

        return AsyncTickets(self)

    @cached_property
    def webhooks(self) -> AsyncWebhooks:
        """Push subscriptions for flight status changes (``/webhooks/*``)."""

        return AsyncWebhooks(self)

    @cached_property
    def history(self) -> AsyncHistory:
        """Archived ADS-B flights, tracks and positions (``/{plan}/history/*``)."""

        return AsyncHistory(self)

    @cached_property
    def batch(self) -> AsyncBatch:
        """The same call for many identifiers, bounded and failure tolerant.

        ``await sky.batch.metars([...])`` returns
        ``{identifier: model | SkyLinkError}`` — see
        :class:`~skylink_api.resources.batch.AsyncBatch`.
        """

        return AsyncBatch(self)

    @cached_property
    def poll(self) -> AsyncPoll:
        """Endpoints on a timer, as async iterators.

        ``async for status in sky.poll.flight_status("BA123")`` — see
        :class:`~skylink_api.resources.poll.AsyncPoll`.
        """

        return AsyncPoll(self)

    @cached_property
    def compose(self) -> AsyncCompose:
        """Multi-endpoint aggregates: whole pages in one call.

        ``await sky.compose.airport_brief("EGLL")`` — see
        :class:`~skylink_api.resources.compose.AsyncCompose`.
        """

        return AsyncCompose(self)

    # ── one-method namespaces, exposed as client methods ─────────────────────

    @cached_property
    def _flight_status(self) -> AsyncFlightStatus:
        return AsyncFlightStatus(self)

    @cached_property
    def _distance(self) -> AsyncDistance:
        return AsyncDistance(self)

    async def flight_status(
        self,
        flight_number: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> FlightStatusResponse:
        """Status of one flight — ``GET /flight_status/{flight_number}``::

            status = await sky.flight_status("BA123")

        See :meth:`SkyLink.flight_status` for the argument and payload notes.
        """

        return await self._flight_status.get(flight_number, request_options=request_options)

    async def distance(
        self,
        *,
        from_icao: str | None = None,
        to_icao: str | None = None,
        from_lat: float | None = None,
        from_lon: float | None = None,
        to_lat: float | None = None,
        to_lon: float | None = None,
        unit: DistanceUnit = "nm",
        request_options: RequestOptions | None = None,
    ) -> DistanceResponse:
        """Great-circle distance and initial bearing — ``GET /distance``::

            leg = await sky.distance(from_icao="KJFK", to_icao="EGLL")

        See :meth:`SkyLink.distance` for the full documentation.
        """

        return await self._distance.calculate(
            from_icao=from_icao,
            to_icao=to_icao,
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
            unit=unit,
            request_options=request_options,
        )

    # ── escape hatch ─────────────────────────────────────────────────────────

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        headers: Mapping[str, str] | None = None,
        response_kind: ResponseKind = "json",
        cast_to: Any = None,
        options: RequestOptions | None = None,
    ) -> Any:
        """Escape hatch for endpoints this SDK does not model yet."""

        spec = RequestSpec(
            method=method.upper(),
            path=path,
            query=query,
            json_body=json_body,
            headers=headers,
            response_kind=response_kind,
            cast_to=cast_to,
        )
        return await self.execute(spec, options)
