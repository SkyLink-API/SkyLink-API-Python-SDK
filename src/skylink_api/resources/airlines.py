"""Airline lookup by ICAO or IATA code.

Follows the :mod:`skylink_api.resources.weather` template: builder, sync class,
async class. The only oddity is the response — a bare JSON array, so ``cast_to``
is ``list[Airline]`` rather than a model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._types import RequestOptions, RequestSpec
from ..models.airlines import Airline

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["Airlines", "AsyncAirlines"]


# ── builders ─────────────────────────────────────────────────────────────────


def _search_spec(*, icao: str | None = None, iata: str | None = None) -> RequestSpec:
    """``GET /airlines/search``.

    Raises:
        ValueError: no filter given — the API answers 400, so it is caught here
            before a request is made.
    """

    if icao is None and iata is None:
        raise ValueError("airlines.search() needs at least one of icao= or iata=")

    return RequestSpec(
        method="GET",
        path="/airlines/search",
        query={"icao": icao, "iata": iata},
        cast_to=list[Airline],
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Airlines:
    """``sky.airlines`` — airline lookup.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            ba = sky.airlines.search(iata="BA")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def search(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> list[Airline]:
        """Airlines matching a code — ``GET /airlines/search``.

        Args:
            icao: 3-letter ICAO code (``"BAW"``). Exactly 3 characters or 400.
            iata: 2-letter IATA code (``"BA"``). Exactly 2 characters or 400.

        At least one is required; supplying both narrows the search (the filters
        are combined with AND).

        Returns:
            A **plain list**, not an envelope. Usually one element, but codes are
            recycled and the dataset keeps defunct carriers, so several rows can
            share a code — check ``airline.active == "Y"`` to pick the live one.

        Note:
            ``active`` is the string ``"Y"``/``"N"``, never a bool — and ``"N"``
            is truthy, so never test it with a bare ``if``. ``logo`` is a
            generated URL (``.../logos/{IATA}.png``) that is ``None`` for
            airlines without an IATA code and may 404 for the rest.

        Raises:
            ValueError: neither code supplied (no request is sent).
            BadRequestError: code of the wrong length (400).
            NotFoundError: no airline carries that code (404) — an empty result
                is an error here, not an empty list.
        """

        spec = _search_spec(icao=icao, iata=iata)
        return cast("list[Airline]", self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncAirlines:
    """``sky.airlines`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Airlines`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            ba = await sky.airlines.search(iata="BA")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def search(
        self,
        *,
        icao: str | None = None,
        iata: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> list[Airline]:
        """Airlines matching a code — ``GET /airlines/search``.

        Args:
            icao: 3-letter ICAO code (``"BAW"``). Exactly 3 characters or 400.
            iata: 2-letter IATA code (``"BA"``). Exactly 2 characters or 400.

        At least one is required; supplying both narrows the search (the filters
        are combined with AND).

        Returns:
            A **plain list**, not an envelope. Usually one element, but codes are
            recycled and the dataset keeps defunct carriers, so several rows can
            share a code — check ``airline.active == "Y"`` to pick the live one.

        Note:
            ``active`` is the string ``"Y"``/``"N"``, never a bool — and ``"N"``
            is truthy, so never test it with a bare ``if``. ``logo`` is a
            generated URL (``.../logos/{IATA}.png``) that is ``None`` for
            airlines without an IATA code and may 404 for the rest.

        Raises:
            ValueError: neither code supplied (no request is sent).
            BadRequestError: code of the wrong length (400).
            NotFoundError: no airline carries that code (404) — an empty result
                is an error here, not an empty list.
        """

        spec = _search_spec(icao=icao, iata=iata)
        result: Any = await self._client.execute(spec, request_options)
        return cast("list[Airline]", result)
