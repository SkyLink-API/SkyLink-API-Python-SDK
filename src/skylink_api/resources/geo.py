"""Geographic reference data: countries and regions.

Two related resources share one namespace because they are two views of the same
OurAirports reference set and are almost always used together — an airport's
``iso_country``/``iso_region`` resolve here.

Follows the :mod:`skylink_api.resources.weather` template: literals, pure
``_*_spec`` builders, sync class, async class. Note the list and detail routes
return **different shapes**: a ``{items, total}`` envelope versus the flat record
itself, hence the separate ``*Detail`` models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.geo import CountriesResponse, CountryDetail, RegionDetail, RegionsResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncGeo", "Continent", "Geo"]

#: Continent code used by the OurAirports dataset. Anything else is a 400, so
#: the SDK offers exactly the seven real values.
Continent = Literal["AF", "AN", "AS", "EU", "NA", "OC", "SA"]


# ── builders ─────────────────────────────────────────────────────────────────


def _countries_spec(*, continent: Continent | None = None) -> RequestSpec:
    """``GET /countries``."""

    return RequestSpec(
        method="GET",
        path="/countries",
        query={"continent": continent},
        cast_to=CountriesResponse,
    )


def _country_spec(code: str) -> RequestSpec:
    """``GET /countries/{code}``."""

    return RequestSpec(
        method="GET",
        path=f"/countries/{code}",
        cast_to=CountryDetail,
    )


def _regions_spec(*, country: str | None = None, continent: Continent | None = None) -> RequestSpec:
    """``GET /regions``."""

    return RequestSpec(
        method="GET",
        path="/regions",
        query={"country": country, "continent": continent},
        cast_to=RegionsResponse,
    )


def _region_spec(code: str) -> RequestSpec:
    """``GET /regions/{code}``."""

    return RequestSpec(
        method="GET",
        path=f"/regions/{code}",
        cast_to=RegionDetail,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Geo:
    """``sky.geo`` — country and region reference data.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            europe = sky.geo.countries(continent="EU")
            california = sky.geo.region("US-CA")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def countries(
        self,
        *,
        continent: Continent | None = None,
        request_options: RequestOptions | None = None,
    ) -> CountriesResponse:
        """List countries — ``GET /countries``.

        Args:
            continent: Restrict to one continent (``"AF"``, ``"AN"``, ``"AS"``,
                ``"EU"``, ``"NA"``, ``"OC"``, ``"SA"``). Case-insensitive on the
                wire. Omit it for all ~250 rows.

        Note:
            Unfiltered and unpaginated by design — the whole dataset comes back
            in one response, so cache it rather than calling per request.

        Raises:
            BadRequestError: unknown continent code (400).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        spec = _countries_spec(continent=continent)
        return cast(CountriesResponse, self._client.execute(spec, request_options))

    def country(
        self,
        code: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> CountryDetail:
        """One country by code — ``GET /countries/{code}``.

        Args:
            code: ISO 3166-1 alpha-2 code (``"US"``, ``"GB"``). Upper-cased by
                the API, so ``"gb"`` works too.

        Returns:
            The record itself — this route has **no envelope**, unlike
            :meth:`countries`.

        Raises:
            NotFoundError: no country with that code (404).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        return cast(CountryDetail, self._client.execute(_country_spec(code), request_options))

    def regions(
        self,
        *,
        country: str | None = None,
        continent: Continent | None = None,
        request_options: RequestOptions | None = None,
    ) -> RegionsResponse:
        """List regions — ``GET /regions``.

        Regions are ISO 3166-2 subdivisions: states, provinces, territories.

        Args:
            country: ISO 3166-1 alpha-2 code (``"US"``). Case-insensitive.
            continent: Continent code. Both filters combine with AND.

        Note:
            Without a filter this returns all ~3,900 regions worldwide in one
            unpaginated response.

        Raises:
            BadRequestError: unknown continent code (400).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        spec = _regions_spec(country=country, continent=continent)
        return cast(RegionsResponse, self._client.execute(spec, request_options))

    def region(
        self,
        code: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> RegionDetail:
        """One region by code — ``GET /regions/{code}``.

        Args:
            code: ISO 3166-2 code — country and subdivision joined by a hyphen
                (``"US-CA"``, ``"GB-ENG"``). Upper-cased by the API.

        Returns:
            The record itself — this route has **no envelope**, unlike
            :meth:`regions`.

        Raises:
            NotFoundError: no region with that code (404).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        return cast(RegionDetail, self._client.execute(_region_spec(code), request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncGeo:
    """``sky.geo`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Geo`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            california = await sky.geo.region("US-CA")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def countries(
        self,
        *,
        continent: Continent | None = None,
        request_options: RequestOptions | None = None,
    ) -> CountriesResponse:
        """List countries — ``GET /countries``.

        Args:
            continent: Restrict to one continent (``"AF"``, ``"AN"``, ``"AS"``,
                ``"EU"``, ``"NA"``, ``"OC"``, ``"SA"``). Case-insensitive on the
                wire. Omit it for all ~250 rows.

        Note:
            Unfiltered and unpaginated by design — the whole dataset comes back
            in one response, so cache it rather than calling per request.

        Raises:
            BadRequestError: unknown continent code (400).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        spec = _countries_spec(continent=continent)
        result: Any = await self._client.execute(spec, request_options)
        return cast(CountriesResponse, result)

    async def country(
        self,
        code: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> CountryDetail:
        """One country by code — ``GET /countries/{code}``.

        Args:
            code: ISO 3166-1 alpha-2 code (``"US"``, ``"GB"``). Upper-cased by
                the API, so ``"gb"`` works too.

        Returns:
            The record itself — this route has **no envelope**, unlike
            :meth:`countries`.

        Raises:
            NotFoundError: no country with that code (404).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        result: Any = await self._client.execute(_country_spec(code), request_options)
        return cast(CountryDetail, result)

    async def regions(
        self,
        *,
        country: str | None = None,
        continent: Continent | None = None,
        request_options: RequestOptions | None = None,
    ) -> RegionsResponse:
        """List regions — ``GET /regions``.

        Regions are ISO 3166-2 subdivisions: states, provinces, territories.

        Args:
            country: ISO 3166-1 alpha-2 code (``"US"``). Case-insensitive.
            continent: Continent code. Both filters combine with AND.

        Note:
            Without a filter this returns all ~3,900 regions worldwide in one
            unpaginated response.

        Raises:
            BadRequestError: unknown continent code (400).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        spec = _regions_spec(country=country, continent=continent)
        result: Any = await self._client.execute(spec, request_options)
        return cast(RegionsResponse, result)

    async def region(
        self,
        code: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> RegionDetail:
        """One region by code — ``GET /regions/{code}``.

        Args:
            code: ISO 3166-2 code — country and subdivision joined by a hyphen
                (``"US-CA"``, ``"GB-ENG"``). Upper-cased by the API.

        Returns:
            The record itself — this route has **no envelope**, unlike
            :meth:`regions`.

        Raises:
            NotFoundError: no region with that code (404).
            ServiceUnavailableError: the reference dataset is not loaded yet (503).
        """

        result: Any = await self._client.execute(_region_spec(code), request_options)
        return cast(RegionDetail, result)
