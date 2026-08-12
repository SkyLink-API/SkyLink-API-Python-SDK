"""Aircraft registry lookup, type performance profiles and database stats.

Structure follows ``resources/weather.py``: module-level ``_*_spec()`` builders
own every endpoint detail, and the sync/async classes are thin
``execute(spec, request_options)`` wrappers that stay method-for-method
identical.

The namespace's defining quirk: the two lookup endpoints **never return 404**.
An unknown identifier is a ``200`` carrying ``found=False`` and
``aircraft=None``, so callers branch on the payload instead of catching an
exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._types import RequestOptions, RequestSpec
from ..models.aircraft import AircraftDatabaseStats, AircraftLookup, AircraftPerformance

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["Aircraft", "AsyncAircraft"]


# ── builders ─────────────────────────────────────────────────────────────────


def _by_registration_spec(registration: str, *, photos: bool = True) -> RequestSpec:
    """``GET /aircraft/registration/{registration}``."""

    return RequestSpec(
        method="GET",
        path=f"/aircraft/registration/{registration}",
        query={"photos": photos},
        cast_to=AircraftLookup,
    )


def _by_icao24_spec(icao24: str, *, photos: bool = True) -> RequestSpec:
    """``GET /aircraft/icao24/{icao24}``."""

    return RequestSpec(
        method="GET",
        path=f"/aircraft/icao24/{icao24}",
        query={"photos": photos},
        cast_to=AircraftLookup,
    )


def _performance_spec(icao_type: str) -> RequestSpec:
    """``GET /aircraft/performance/{icao_type}``."""

    return RequestSpec(
        method="GET",
        path=f"/aircraft/performance/{icao_type}",
        cast_to=AircraftPerformance,
    )


def _database_stats_spec() -> RequestSpec:
    """``GET /aircraft/database/stats``."""

    return RequestSpec(
        method="GET",
        path="/aircraft/database/stats",
        cast_to=AircraftDatabaseStats,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Aircraft:
    """``sky.aircraft`` — airframe registry and type performance data.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            result = sky.aircraft.by_registration("G-STBA")
            if result.aircraft is not None:
                print(result.aircraft.type_name)
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def by_registration(
        self,
        registration: str,
        *,
        photos: bool = True,
        request_options: RequestOptions | None = None,
    ) -> AircraftLookup:
        """Registry record for a tail number — ``GET /aircraft/registration/{reg}``.

        Args:
            registration: Tail number, 2-10 characters (``"N12345"``,
                ``"G-STBA"``, ``"D-AIZY"``). Case-insensitive; the API
                upper-cases it and echoes it back as ``query``.
            photos: Include photos of the airframe. **Defaults to ``True``**,
                matching the backend — pass ``False`` to skip the photo provider
                round-trip, which is the slow part of this call. (ADS-B goes the
                other way: ``sky.adsb.aircraft`` defaults ``photos`` to
                ``False``.)

        Note:
            An unknown registration is **not** an error. The response is a plain
            ``200`` with ``found=False`` and ``aircraft=None``::

                result = sky.aircraft.by_registration("ZZ-ZZZ")
                assert result.found is False and result.aircraft is None

            ``aircraft.year_built`` is a **string** (``"2010"``) — the registry
            value is forwarded verbatim.

        Raises:
            UnprocessableEntityError: registration shorter than 2 or longer than
                10 characters (422).
        """

        spec = _by_registration_spec(registration, photos=photos)
        return cast(AircraftLookup, self._client.execute(spec, request_options))

    def by_icao24(
        self,
        icao24: str,
        *,
        photos: bool = True,
        request_options: RequestOptions | None = None,
    ) -> AircraftLookup:
        """Registry record for a transponder address — ``GET /aircraft/icao24/{hex}``.

        Args:
            icao24: ICAO 24-bit hex address, 4-6 characters (``"40621D"``).
                Case-insensitive.
            photos: Include photos of the airframe. **Defaults to ``True``**,
                matching the backend.

        Note:
            As with :meth:`by_registration`, a miss is a ``200`` with
            ``found=False`` and ``aircraft=None``, never a 404.

        Raises:
            UnprocessableEntityError: address shorter than 4 or longer than 6
                characters (422).
        """

        spec = _by_icao24_spec(icao24, photos=photos)
        return cast(AircraftLookup, self._client.execute(spec, request_options))

    def performance(
        self,
        icao_type: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> AircraftPerformance:
        """Performance profile of an aircraft **type** — ``GET /aircraft/performance/{icao_type}``.

        Args:
            icao_type: ICAO type designator, 2-4 alphanumerics (``"A320"``,
                ``"B77W"``, ``"E190"``). Not a registration and not an ICAO24.

        Note:
            Reference data for the type, not for a specific airframe — cabin and
            weight figures are typical certified values, not an operator's
            configuration. **Every field is optional**: the dataset is scraped
            and thin coverage for rarer designators is normal.

        Raises:
            NotFoundError: no performance row for this type (404).
            UnprocessableEntityError: designator outside 2-4 alphanumerics (422).
        """

        spec = _performance_spec(icao_type)
        return cast(AircraftPerformance, self._client.execute(spec, request_options))

    def database_stats(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> AircraftDatabaseStats:
        """Size and load state of the registry — ``GET /aircraft/database/stats``.

        Note:
            Handy as a readiness probe: while ``loaded`` is ``False`` the lookup
            endpoints answer ``found=False`` for everything rather than failing.
        """

        spec = _database_stats_spec()
        return cast(AircraftDatabaseStats, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncAircraft:
    """``sky.aircraft`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Aircraft`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            result = await sky.aircraft.by_registration("G-STBA")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def by_registration(
        self,
        registration: str,
        *,
        photos: bool = True,
        request_options: RequestOptions | None = None,
    ) -> AircraftLookup:
        """Registry record for a tail number — ``GET /aircraft/registration/{reg}``.

        Args:
            registration: Tail number, 2-10 characters (``"N12345"``,
                ``"G-STBA"``, ``"D-AIZY"``). Case-insensitive; the API
                upper-cases it and echoes it back as ``query``.
            photos: Include photos of the airframe. **Defaults to ``True``**,
                matching the backend — pass ``False`` to skip the photo provider
                round-trip, which is the slow part of this call. (ADS-B goes the
                other way: ``sky.adsb.aircraft`` defaults ``photos`` to
                ``False``.)

        Note:
            An unknown registration is **not** an error. The response is a plain
            ``200`` with ``found=False`` and ``aircraft=None``.

            ``aircraft.year_built`` is a **string** (``"2010"``) — the registry
            value is forwarded verbatim.

        Raises:
            UnprocessableEntityError: registration shorter than 2 or longer than
                10 characters (422).
        """

        spec = _by_registration_spec(registration, photos=photos)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AircraftLookup, result)

    async def by_icao24(
        self,
        icao24: str,
        *,
        photos: bool = True,
        request_options: RequestOptions | None = None,
    ) -> AircraftLookup:
        """Registry record for a transponder address — ``GET /aircraft/icao24/{hex}``.

        Args:
            icao24: ICAO 24-bit hex address, 4-6 characters (``"40621D"``).
                Case-insensitive.
            photos: Include photos of the airframe. **Defaults to ``True``**,
                matching the backend.

        Note:
            As with :meth:`by_registration`, a miss is a ``200`` with
            ``found=False`` and ``aircraft=None``, never a 404.

        Raises:
            UnprocessableEntityError: address shorter than 4 or longer than 6
                characters (422).
        """

        spec = _by_icao24_spec(icao24, photos=photos)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AircraftLookup, result)

    async def performance(
        self,
        icao_type: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> AircraftPerformance:
        """Performance profile of an aircraft **type** — ``GET /aircraft/performance/{icao_type}``.

        Args:
            icao_type: ICAO type designator, 2-4 alphanumerics (``"A320"``,
                ``"B77W"``, ``"E190"``). Not a registration and not an ICAO24.

        Note:
            Reference data for the type, not for a specific airframe. **Every
            field is optional** — the dataset is scraped and thin coverage for
            rarer designators is normal.

        Raises:
            NotFoundError: no performance row for this type (404).
            UnprocessableEntityError: designator outside 2-4 alphanumerics (422).
        """

        spec = _performance_spec(icao_type)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AircraftPerformance, result)

    async def database_stats(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> AircraftDatabaseStats:
        """Size and load state of the registry — ``GET /aircraft/database/stats``.

        Note:
            Handy as a readiness probe: while ``loaded`` is ``False`` the lookup
            endpoints answer ``found=False`` for everything rather than failing.
        """

        spec = _database_stats_spec()
        result: Any = await self._client.execute(spec, request_options)
        return cast(AircraftDatabaseStats, result)
