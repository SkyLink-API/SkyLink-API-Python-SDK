"""Aerodrome charts: PDF links by airport and category, plus the source registry.

Structure follows ``resources/weather.py``: module-level ``_*_spec()`` builders
own every endpoint detail, and the sync/async classes are thin
``execute(spec, request_options)`` wrappers that stay method-for-method
identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.charts import ChartSourcesResponse, ChartsResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncCharts", "ChartCategory", "Charts"]

#: Chart category. ``GEN`` general information, ``GND`` ground/taxi diagrams,
#: ``SID`` departures, ``STAR`` arrivals, ``APP`` approaches.
ChartCategory = Literal["GEN", "GND", "SID", "STAR", "APP"]


# ── builders ─────────────────────────────────────────────────────────────────


def _by_airport_spec(icao: str, *, source: str | None = None) -> RequestSpec:
    """``GET /charts/{icao}``."""

    return RequestSpec(
        method="GET",
        path=f"/charts/{icao}",
        query={"source": source},
        cast_to=ChartsResponse,
    )


def _by_category_spec(
    icao: str,
    category: ChartCategory,
    *,
    source: str | None = None,
) -> RequestSpec:
    """``GET /charts/{icao}/{category}``."""

    return RequestSpec(
        method="GET",
        path=f"/charts/{icao}/{category}",
        query={"source": source},
        cast_to=ChartsResponse,
    )


def _sources_spec() -> RequestSpec:
    """``GET /charts/sources``."""

    return RequestSpec(method="GET", path="/charts/sources", cast_to=ChartSourcesResponse)


# ── sync ─────────────────────────────────────────────────────────────────────


class Charts:
    """``sky.charts`` — aerodrome chart links.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            charts = sky.charts.by_airport("KJFK")
            for chart in charts.charts.get("APP", []):
                print(chart.name, chart.url)
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def by_airport(
        self,
        icao: str,
        *,
        source: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> ChartsResponse:
        """Every chart for an airport, grouped by category — ``GET /charts/{icao}``.

        Args:
            icao: ICAO airport code, 3-4 characters. Case-insensitive.
            source: Override the auto-detected national source (``"faa"``,
                ``"uk"``, ``"france"``...). Normally unnecessary — the source is
                derived from the ICAO prefix. Call :meth:`sources` for the list.

        Note:
            ``charts`` is a **partial** map: categories with no charts are
            omitted rather than sent as empty lists, so use
            ``result.charts.get("SID", [])`` instead of indexing.

            The URLs point at the publisher's own servers; the API does not proxy
            or cache the PDFs, and some national AIPs expire their links.

        Raises:
            BadRequestError: the ``source`` override is not a known source (400).
            NotFoundError: the airport is not covered by any source, or the
                scrape returned nothing (404) — an airport with zero charts is
                *not* an empty ``200``.
        """

        spec = _by_airport_spec(icao, source=source)
        return cast(ChartsResponse, self._client.execute(spec, request_options))

    def by_category(
        self,
        icao: str,
        category: ChartCategory,
        *,
        source: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> ChartsResponse:
        """Charts of one category — ``GET /charts/{icao}/{category}``.

        Args:
            icao: ICAO airport code, 3-4 characters.
            category: ``"GEN"``, ``"GND"``, ``"SID"``, ``"STAR"`` or ``"APP"``.
                Case-sensitive on the wire — upper case only.
            source: Override the auto-detected national source.

        Note:
            Same envelope as :meth:`by_airport`, narrowed to a single key, and
            ``total_count`` reports the **filtered** count, not the airport's
            total.

        Raises:
            BadRequestError: unknown ``source`` override (400).
            NotFoundError: the airport has no charts in that category (404).
            UnprocessableEntityError: ``category`` outside the five values (422).
        """

        spec = _by_category_spec(icao, category, source=source)
        return cast(ChartsResponse, self._client.execute(spec, request_options))

    def sources(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> ChartSourcesResponse:
        """Supported chart providers and their ICAO prefixes — ``GET /charts/sources``.

        Note:
            ``icao_prefixes`` is mostly plain prefixes (``"K"``, ``"EG"``), but
            the catch-all Russian source carries a descriptive **sentinel**
            instead: ``"U* (except UA,UC,UG,UM,UT,UZ)"``. Do not feed these
            entries to a prefix matcher unchecked.
        """

        return cast(ChartSourcesResponse, self._client.execute(_sources_spec(), request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncCharts:
    """``sky.charts`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Charts`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            charts = await sky.charts.by_airport("KJFK")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def by_airport(
        self,
        icao: str,
        *,
        source: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> ChartsResponse:
        """Every chart for an airport, grouped by category — ``GET /charts/{icao}``.

        Args:
            icao: ICAO airport code, 3-4 characters. Case-insensitive.
            source: Override the auto-detected national source (``"faa"``,
                ``"uk"``, ``"france"``...). Call :meth:`sources` for the list.

        Note:
            ``charts`` is a **partial** map: categories with no charts are
            omitted, so use ``result.charts.get("SID", [])`` instead of indexing.

            The URLs point at the publisher's own servers; the API does not proxy
            the PDFs.

        Raises:
            BadRequestError: the ``source`` override is not a known source (400).
            NotFoundError: the airport is not covered, or the scrape returned
                nothing (404) — never an empty ``200``.
        """

        spec = _by_airport_spec(icao, source=source)
        result: Any = await self._client.execute(spec, request_options)
        return cast(ChartsResponse, result)

    async def by_category(
        self,
        icao: str,
        category: ChartCategory,
        *,
        source: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> ChartsResponse:
        """Charts of one category — ``GET /charts/{icao}/{category}``.

        Args:
            icao: ICAO airport code, 3-4 characters.
            category: ``"GEN"``, ``"GND"``, ``"SID"``, ``"STAR"`` or ``"APP"``.
            source: Override the auto-detected national source.

        Note:
            Same envelope as :meth:`by_airport`, narrowed to a single key;
            ``total_count`` reports the **filtered** count.

        Raises:
            BadRequestError: unknown ``source`` override (400).
            NotFoundError: the airport has no charts in that category (404).
            UnprocessableEntityError: ``category`` outside the five values (422).
        """

        spec = _by_category_spec(icao, category, source=source)
        result: Any = await self._client.execute(spec, request_options)
        return cast(ChartsResponse, result)

    async def sources(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> ChartSourcesResponse:
        """Supported chart providers and their ICAO prefixes — ``GET /charts/sources``.

        Note:
            ``icao_prefixes`` mostly holds plain prefixes, but the catch-all
            Russian source carries the descriptive **sentinel**
            ``"U* (except UA,UC,UG,UM,UT,UZ)"``.
        """

        result: Any = await self._client.execute(_sources_spec(), request_options)
        return cast(ChartSourcesResponse, result)
