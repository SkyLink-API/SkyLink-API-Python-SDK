"""Great-circle distance and bearing between two aviation waypoints.

Structure follows ``resources/weather.py``: a module-level ``_*_spec()`` builder
owns the endpoint detail, and the sync/async classes are thin
``execute(spec, request_options)`` wrappers.

The client also exposes this as a shortcut method — ``sky.distance(...)`` — so
in practice you rarely touch the class directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.distance import DistanceResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncDistance", "Distance", "DistanceUnit"]

#: Output unit: nautical miles (default), kilometres or statute miles.
DistanceUnit = Literal["nm", "km", "mi"]


# ── builders ─────────────────────────────────────────────────────────────────


def _calculate_spec(
    *,
    from_icao: str | None = None,
    to_icao: str | None = None,
    from_lat: float | None = None,
    from_lon: float | None = None,
    to_lat: float | None = None,
    to_lon: float | None = None,
    unit: DistanceUnit = "nm",
) -> RequestSpec:
    """``GET /distance`` (no trailing slash)."""

    return RequestSpec(
        method="GET",
        path="/distance",
        query={
            "from_icao": from_icao,
            "to_icao": to_icao,
            "from_lat": from_lat,
            "from_lon": from_lon,
            "to_lat": to_lat,
            "to_lon": to_lon,
            "unit": unit,
        },
        cast_to=DistanceResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Distance:
    """``sky.distance`` — great-circle distance and bearing.

    The client exposes :meth:`calculate` directly as ``sky.distance(...)``::

        with SkyLink(api_key="...") as sky:
            leg = sky.distance(from_icao="KJFK", to_icao="EGLL")
            print(leg.distance, leg.unit, leg.bearing_cardinal)
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def calculate(
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
        """Distance and initial bearing between two points — ``GET /distance``.

        Each endpoint is specified **either** by airport code **or** by a
        latitude/longitude pair, and the two styles mix freely::

            sky.distance(from_icao="KJFK", to_icao="EGLL")
            sky.distance(from_lat=40.64, from_lon=-73.78, to_icao="EGLL", unit="km")

        Args:
            from_icao: Origin airport, ICAO **or** IATA code (``"KJFK"`` or
                ``"JFK"``). Takes precedence over ``from_lat``/``from_lon``.
            to_icao: Destination airport, ICAO or IATA code.
            from_lat: Origin latitude, -90 to 90. Needs ``from_lon``.
            from_lon: Origin longitude, -180 to 180.
            to_lat: Destination latitude, -90 to 90. Needs ``to_lon``.
            to_lon: Destination longitude, -180 to 180.
            unit: ``"nm"`` (default), ``"km"`` or ``"mi"``.

        Note:
            ``bearing`` is the **initial** bearing; along a great circle it drifts
            continuously, so it is not a heading to hold for the whole leg.
            ``midpoint`` is bare coordinates — it is never resolved to an
            airport.

        Raises:
            BadRequestError: an endpoint was left unspecified — neither a code
                nor a complete lat/lon pair (400).
            NotFoundError: an airport code did not resolve (404).
            UnprocessableEntityError: latitude or longitude out of range (422).
        """

        spec = _calculate_spec(
            from_icao=from_icao,
            to_icao=to_icao,
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
            unit=unit,
        )
        return cast(DistanceResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncDistance:
    """``sky.distance`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Distance`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            leg = await sky.distance(from_icao="KJFK", to_icao="EGLL")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def calculate(
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
        """Distance and initial bearing between two points — ``GET /distance``.

        Each endpoint is specified **either** by airport code **or** by a
        latitude/longitude pair, and the two styles mix freely.

        Args:
            from_icao: Origin airport, ICAO **or** IATA code (``"KJFK"`` or
                ``"JFK"``). Takes precedence over ``from_lat``/``from_lon``.
            to_icao: Destination airport, ICAO or IATA code.
            from_lat: Origin latitude, -90 to 90. Needs ``from_lon``.
            from_lon: Origin longitude, -180 to 180.
            to_lat: Destination latitude, -90 to 90. Needs ``to_lon``.
            to_lon: Destination longitude, -180 to 180.
            unit: ``"nm"`` (default), ``"km"`` or ``"mi"``.

        Note:
            ``bearing`` is the **initial** bearing; along a great circle it drifts
            continuously. ``midpoint`` is bare coordinates — never an airport.

        Raises:
            BadRequestError: an endpoint was left unspecified — neither a code
                nor a complete lat/lon pair (400).
            NotFoundError: an airport code did not resolve (404).
            UnprocessableEntityError: latitude or longitude out of range (422).
        """

        spec = _calculate_spec(
            from_icao=from_icao,
            to_icao=to_icao,
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
            unit=unit,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(DistanceResponse, result)
