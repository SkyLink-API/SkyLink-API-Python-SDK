"""Aviation weather: METAR, TAF, winds aloft, PIREPs and AIRMET/SIGMET.

This module is the template every other namespace follows:

1. **Literals** for the endpoint's enum-ish query parameters.
2. **Builders** — module level ``_*_spec()`` functions that turn Python
   arguments into a :class:`~skylink_api._types.RequestSpec`. They are pure, do
   the query/path/`cast_to` work exactly once, and are shared verbatim by the
   sync and async classes. All endpoint knowledge lives here.
3. **Sync class**, then **async class** — thin wrappers whose only job is
   ``execute(spec, request_options)``. The two must stay method-for-method
   identical, docstrings included.

Overloads: methods whose *return type* depends on an argument value (here
``parsed``) declare ``@overload`` variants keyed on ``Literal``, so
``metar(icao)`` has no ``parsed`` attribute on its type while
``metar(icao, parsed=True)`` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast, overload

from .._qs import format_bbox
from .._types import BBoxLike, RequestOptions, RequestSpec
from ..models.weather import (
    AirSigmetResponse,
    Metar,
    MetarWithParsed,
    PirepsResponse,
    Taf,
    TafWithParsed,
    WindsAloftResponse,
)

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncWeather", "Weather"]

#: Winds aloft forecast period. Values in between are accepted by the API but
#: snapped down to one of these three, so the SDK only offers the real ones.
WindsAloftForecast = Literal[6, 12, 24]

#: Winds aloft altitude band: ``low`` = 3,000-39,000 ft, ``high`` = 6,000-45,000 ft.
WindsAloftLevel = Literal["low", "high"]

#: AIRMET/SIGMET type filter. Omit it to get both.
AirSigmetType = Literal["airmet", "sigmet"]


# ── builders ─────────────────────────────────────────────────────────────────


def _metar_spec(icao: str, *, parsed: bool = False) -> RequestSpec:
    """``GET /weather/metar/{icao}``."""

    return RequestSpec(
        method="GET",
        path=f"/weather/metar/{icao}",
        query={"parsed": parsed},
        cast_to=MetarWithParsed if parsed else Metar,
    )


def _taf_spec(icao: str, *, parsed: bool = False) -> RequestSpec:
    """``GET /weather/taf/{icao}``."""

    return RequestSpec(
        method="GET",
        path=f"/weather/taf/{icao}",
        query={"parsed": parsed},
        cast_to=TafWithParsed if parsed else Taf,
    )


def _winds_aloft_spec(
    *,
    bbox: BBoxLike,
    forecast: WindsAloftForecast = 12,
    level: WindsAloftLevel = "low",
) -> RequestSpec:
    """``GET /weather/winds-aloft``."""

    return RequestSpec(
        method="GET",
        path="/weather/winds-aloft",
        query={"bbox": format_bbox(bbox), "forecast": forecast, "level": level},
        cast_to=WindsAloftResponse,
    )


def _pireps_spec(*, bbox: BBoxLike, hours: int = 2) -> RequestSpec:
    """``GET /weather/pireps``."""

    return RequestSpec(
        method="GET",
        path="/weather/pireps",
        query={"bbox": format_bbox(bbox), "hours": hours},
        cast_to=PirepsResponse,
    )


def _airsigmet_spec(*, bbox: BBoxLike, type: AirSigmetType | None = None) -> RequestSpec:
    """``GET /weather/airsigmet``."""

    return RequestSpec(
        method="GET",
        path="/weather/airsigmet",
        query={"bbox": format_bbox(bbox), "type": type},
        cast_to=AirSigmetResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Weather:
    """``sky.weather`` — aviation weather endpoints.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            metar = sky.weather.metar("KJFK", parsed=True)
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    @overload
    def metar(
        self,
        icao: str,
        *,
        parsed: Literal[False] = False,
        request_options: RequestOptions | None = None,
    ) -> Metar: ...

    @overload
    def metar(
        self,
        icao: str,
        *,
        parsed: Literal[True],
        request_options: RequestOptions | None = None,
    ) -> MetarWithParsed: ...

    def metar(
        self,
        icao: str,
        *,
        parsed: bool = False,
        request_options: RequestOptions | None = None,
    ) -> Metar:
        """Current METAR observation for an airport — ``GET /weather/metar/{icao}``.

        Args:
            icao: 4-letter ICAO code (``"KJFK"``). IATA codes are not accepted;
                anything but 4 characters is a 400.
            parsed: Also return decoded fields. With ``True`` the result is a
                :class:`~skylink_api.models.weather.MetarWithParsed` whose
                ``parsed`` attribute is **nullable** — the API sends ``null``
                rather than failing when the decoder chokes on a report.

        Raises:
            NotFoundError: unknown airport, or the station reports no current
                METAR (both are 404).
            ServiceUnavailableError: upstream weather feed is down (503).
        """

        return cast(Metar, self._client.execute(_metar_spec(icao, parsed=parsed), request_options))

    @overload
    def taf(
        self,
        icao: str,
        *,
        parsed: Literal[False] = False,
        request_options: RequestOptions | None = None,
    ) -> Taf: ...

    @overload
    def taf(
        self,
        icao: str,
        *,
        parsed: Literal[True],
        request_options: RequestOptions | None = None,
    ) -> TafWithParsed: ...

    def taf(
        self,
        icao: str,
        *,
        parsed: bool = False,
        request_options: RequestOptions | None = None,
    ) -> Taf:
        """Terminal aerodrome forecast — ``GET /weather/taf/{icao}``.

        Args:
            icao: 4-letter ICAO code.
            parsed: Also return the decoded forecast periods. As with
                :meth:`metar`, ``parsed`` may still come back ``None``.

        Raises:
            NotFoundError: unknown airport, or the station issues no TAF — many
                small fields report METAR only.
            ServiceUnavailableError: upstream weather feed is down (503).
        """

        return cast(Taf, self._client.execute(_taf_spec(icao, parsed=parsed), request_options))

    def winds_aloft(
        self,
        *,
        bbox: BBoxLike,
        forecast: WindsAloftForecast = 12,
        level: WindsAloftLevel = "low",
        request_options: RequestOptions | None = None,
    ) -> WindsAloftResponse:
        """Winds and temperatures aloft in a bounding box — ``GET /weather/winds-aloft``.

        **US coverage only** — the source is the FAA FD product, so a box over
        Europe legitimately returns 404 rather than an empty list.

        Args:
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first; a
                pre-formatted ``"lat1,lon1,lat2,lon2"`` string also works. The
                API rejects boxes where ``lat1 >= lat2`` or ``lon1 >= lon2``.
            forecast: Forecast period in hours — 6, 12 (default) or 24.
            level: ``"low"`` (3,000-39,000 ft) or ``"high"`` (6,000-45,000 ft).

        Raises:
            BadRequestError: malformed or inverted bounding box (400).
            NotFoundError: no FB winds station inside the box (404).
            ServiceUnavailableError: upstream feed is down (503).
        """

        spec = _winds_aloft_spec(bbox=bbox, forecast=forecast, level=level)
        return cast(WindsAloftResponse, self._client.execute(spec, request_options))

    def pireps(
        self,
        *,
        bbox: BBoxLike,
        hours: int = 2,
        request_options: RequestOptions | None = None,
    ) -> PirepsResponse:
        """Pilot reports in a bounding box — ``GET /weather/pireps``.

        Args:
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first.
            hours: How far back to look, 1-24 (default 2).

        Note:
            An empty box is a normal ``200`` with ``reports=[]`` and ``total=0``,
            not a 404.

        Raises:
            BadRequestError: malformed bounding box (400).
            ServiceUnavailableError: upstream feed is down (503).
        """

        spec = _pireps_spec(bbox=bbox, hours=hours)
        return cast(PirepsResponse, self._client.execute(spec, request_options))

    def airsigmet(
        self,
        *,
        bbox: BBoxLike,
        type: AirSigmetType | None = None,
        request_options: RequestOptions | None = None,
    ) -> AirSigmetResponse:
        """AIRMET/SIGMET advisories intersecting a bounding box — ``GET /weather/airsigmet``.

        Args:
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first. An
                advisory matches when any of its polygon vertices falls inside
                the box, or any box corner falls inside its polygon.
            type: ``"airmet"`` or ``"sigmet"``; omit for both.

        Note:
            No match is a normal ``200`` with ``reports=[]``, not a 404.

        Raises:
            BadRequestError: malformed bounding box (400).
            ServiceUnavailableError: upstream feed is down (503).
        """

        spec = _airsigmet_spec(bbox=bbox, type=type)
        return cast(AirSigmetResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncWeather:
    """``sky.weather`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Weather`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            metar = await sky.weather.metar("KJFK", parsed=True)
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    @overload
    async def metar(
        self,
        icao: str,
        *,
        parsed: Literal[False] = False,
        request_options: RequestOptions | None = None,
    ) -> Metar: ...

    @overload
    async def metar(
        self,
        icao: str,
        *,
        parsed: Literal[True],
        request_options: RequestOptions | None = None,
    ) -> MetarWithParsed: ...

    async def metar(
        self,
        icao: str,
        *,
        parsed: bool = False,
        request_options: RequestOptions | None = None,
    ) -> Metar:
        """Current METAR observation for an airport — ``GET /weather/metar/{icao}``.

        Args:
            icao: 4-letter ICAO code (``"KJFK"``). IATA codes are not accepted;
                anything but 4 characters is a 400.
            parsed: Also return decoded fields. With ``True`` the result is a
                :class:`~skylink_api.models.weather.MetarWithParsed` whose
                ``parsed`` attribute is **nullable** — the API sends ``null``
                rather than failing when the decoder chokes on a report.

        Raises:
            NotFoundError: unknown airport, or the station reports no current
                METAR (both are 404).
            ServiceUnavailableError: upstream weather feed is down (503).
        """

        result: Any = await self._client.execute(_metar_spec(icao, parsed=parsed), request_options)
        return cast(Metar, result)

    @overload
    async def taf(
        self,
        icao: str,
        *,
        parsed: Literal[False] = False,
        request_options: RequestOptions | None = None,
    ) -> Taf: ...

    @overload
    async def taf(
        self,
        icao: str,
        *,
        parsed: Literal[True],
        request_options: RequestOptions | None = None,
    ) -> TafWithParsed: ...

    async def taf(
        self,
        icao: str,
        *,
        parsed: bool = False,
        request_options: RequestOptions | None = None,
    ) -> Taf:
        """Terminal aerodrome forecast — ``GET /weather/taf/{icao}``.

        Args:
            icao: 4-letter ICAO code.
            parsed: Also return the decoded forecast periods. As with
                :meth:`metar`, ``parsed`` may still come back ``None``.

        Raises:
            NotFoundError: unknown airport, or the station issues no TAF — many
                small fields report METAR only.
            ServiceUnavailableError: upstream weather feed is down (503).
        """

        result: Any = await self._client.execute(_taf_spec(icao, parsed=parsed), request_options)
        return cast(Taf, result)

    async def winds_aloft(
        self,
        *,
        bbox: BBoxLike,
        forecast: WindsAloftForecast = 12,
        level: WindsAloftLevel = "low",
        request_options: RequestOptions | None = None,
    ) -> WindsAloftResponse:
        """Winds and temperatures aloft in a bounding box — ``GET /weather/winds-aloft``.

        **US coverage only** — the source is the FAA FD product, so a box over
        Europe legitimately returns 404 rather than an empty list.

        Args:
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first; a
                pre-formatted ``"lat1,lon1,lat2,lon2"`` string also works. The
                API rejects boxes where ``lat1 >= lat2`` or ``lon1 >= lon2``.
            forecast: Forecast period in hours — 6, 12 (default) or 24.
            level: ``"low"`` (3,000-39,000 ft) or ``"high"`` (6,000-45,000 ft).

        Raises:
            BadRequestError: malformed or inverted bounding box (400).
            NotFoundError: no FB winds station inside the box (404).
            ServiceUnavailableError: upstream feed is down (503).
        """

        spec = _winds_aloft_spec(bbox=bbox, forecast=forecast, level=level)
        result: Any = await self._client.execute(spec, request_options)
        return cast(WindsAloftResponse, result)

    async def pireps(
        self,
        *,
        bbox: BBoxLike,
        hours: int = 2,
        request_options: RequestOptions | None = None,
    ) -> PirepsResponse:
        """Pilot reports in a bounding box — ``GET /weather/pireps``.

        Args:
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first.
            hours: How far back to look, 1-24 (default 2).

        Note:
            An empty box is a normal ``200`` with ``reports=[]`` and ``total=0``,
            not a 404.

        Raises:
            BadRequestError: malformed bounding box (400).
            ServiceUnavailableError: upstream feed is down (503).
        """

        spec = _pireps_spec(bbox=bbox, hours=hours)
        result: Any = await self._client.execute(spec, request_options)
        return cast(PirepsResponse, result)

    async def airsigmet(
        self,
        *,
        bbox: BBoxLike,
        type: AirSigmetType | None = None,
        request_options: RequestOptions | None = None,
    ) -> AirSigmetResponse:
        """AIRMET/SIGMET advisories intersecting a bounding box — ``GET /weather/airsigmet``.

        Args:
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first. An
                advisory matches when any of its polygon vertices falls inside
                the box, or any box corner falls inside its polygon.
            type: ``"airmet"`` or ``"sigmet"``; omit for both.

        Note:
            No match is a normal ``200`` with ``reports=[]``, not a 404.

        Raises:
            BadRequestError: malformed bounding box (400).
            ServiceUnavailableError: upstream feed is down (503).
        """

        spec = _airsigmet_spec(bbox=bbox, type=type)
        result: Any = await self._client.execute(spec, request_options)
        return cast(AirSigmetResponse, result)
