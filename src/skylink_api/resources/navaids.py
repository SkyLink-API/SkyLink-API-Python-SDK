"""Navigation aid search (VOR, NDB, ILS, DME, TACAN, ...).

Follows the :mod:`skylink_api.resources.weather` template. The one addition is a
**client-side guard**: the dataset holds ~70,000 navaids and the API refuses an
unfiltered query with a 400, so the builder raises before any request is sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._qs import format_bbox
from .._types import BBoxLike, RequestOptions, RequestSpec
from ..models.navaids import NavaidsResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncNavaids", "Navaids"]

#: The filters that satisfy the "at least one" rule, in the order the error
#: message lists them.
_FILTER_NAMES = ("ident", "airport", "type", "country", "bbox")


# ── builders ─────────────────────────────────────────────────────────────────


def _list_spec(
    *,
    ident: str | None = None,
    airport: str | None = None,
    type: str | None = None,
    country: str | None = None,
    bbox: BBoxLike | None = None,
    limit: int = 100,
) -> RequestSpec:
    """``GET /navaids``.

    Raises:
        ValueError: no filter given. The API answers 400 rather than dumping the
            whole dataset, so the SDK stops it here.
    """

    if all(value is None for value in (ident, airport, type, country, bbox)):
        raise ValueError(
            "navaids.list() requires at least one filter: "
            + ", ".join(_FILTER_NAMES)
            + ". Returning the full navaid dataset unfiltered is not supported."
        )

    return RequestSpec(
        method="GET",
        path="/navaids",
        query={
            "ident": ident,
            "airport": airport,
            "type": type,
            "country": country,
            "bbox": format_bbox(bbox) if bbox is not None else None,
            "limit": limit,
        },
        cast_to=NavaidsResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Navaids:
    """``sky.navaids`` — navigation aid search.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            ils = sky.navaids.list(airport="KJFK", type="ILS")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def list(
        self,
        *,
        ident: str | None = None,
        airport: str | None = None,
        type: str | None = None,
        country: str | None = None,
        bbox: BBoxLike | None = None,
        limit: int = 100,
        request_options: RequestOptions | None = None,
    ) -> NavaidsResponse:
        """Search navigation aids — ``GET /navaids``.

        **At least one filter is required.** Omitting all of them raises
        ``ValueError`` before anything is sent (the API would answer 400 — it
        will not return the whole ~70,000 row dataset).

        Args:
            ident: Navaid identifier — **partial**, case-insensitive substring
                match (``"JFK"`` also matches ``"JFKX"``).
            airport: ICAO code of the associated airport — exact match,
                upper-cased by the API.
            type: Navaid type — exact, case-insensitive (``"VOR"``, ``"NDB"``,
                ``"ILS"``, ``"DME"``, ``"TACAN"``, ``"VOR-DME"``, ``"VORTAC"``).
                Not a ``Literal``: the dataset carries a long tail of variants.
            country: ISO 3166-1 alpha-2 code — exact match.
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first; a
                pre-formatted ``"lat1,lon1,lat2,lon2"`` string also works. The
                API rejects boxes where ``lat1 > lat2`` or ``lon1 > lon2``.
            limit: Maximum rows, 1-500 (default 100).

        Returns:
            An envelope whose ``total`` is the match count **before** ``limit``
            and can therefore exceed ``len(navaids)`` — there is no offset, so
            narrow the filters instead. ``filters_applied`` echoes the filters
            the API used, normalised (codes upper-cased).

        Note:
            ``usageType`` is the only camelCase key in the API; it is exposed as
            ``navaid.usage_type``. Frequencies are numbers here but arrive as
            strings inside :meth:`Airports.search
            <skylink_api.resources.airports.Airports.search>` results, so both
            are accepted.

        Raises:
            ValueError: no filter supplied (no request is sent).
            BadRequestError: malformed or inverted bounding box (400).
            ServiceUnavailableError: the navaid dataset is not loaded yet (503).
        """

        spec = _list_spec(
            ident=ident, airport=airport, type=type, country=country, bbox=bbox, limit=limit
        )
        return cast(NavaidsResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncNavaids:
    """``sky.navaids`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Navaids`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            ils = await sky.navaids.list(airport="KJFK", type="ILS")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def list(
        self,
        *,
        ident: str | None = None,
        airport: str | None = None,
        type: str | None = None,
        country: str | None = None,
        bbox: BBoxLike | None = None,
        limit: int = 100,
        request_options: RequestOptions | None = None,
    ) -> NavaidsResponse:
        """Search navigation aids — ``GET /navaids``.

        **At least one filter is required.** Omitting all of them raises
        ``ValueError`` before anything is sent (the API would answer 400 — it
        will not return the whole ~70,000 row dataset).

        Args:
            ident: Navaid identifier — **partial**, case-insensitive substring
                match (``"JFK"`` also matches ``"JFKX"``).
            airport: ICAO code of the associated airport — exact match,
                upper-cased by the API.
            type: Navaid type — exact, case-insensitive (``"VOR"``, ``"NDB"``,
                ``"ILS"``, ``"DME"``, ``"TACAN"``, ``"VOR-DME"``, ``"VORTAC"``).
                Not a ``Literal``: the dataset carries a long tail of variants.
            country: ISO 3166-1 alpha-2 code — exact match.
            bbox: ``(lat1, lon1, lat2, lon2)``, south-west corner first; a
                pre-formatted ``"lat1,lon1,lat2,lon2"`` string also works. The
                API rejects boxes where ``lat1 > lat2`` or ``lon1 > lon2``.
            limit: Maximum rows, 1-500 (default 100).

        Returns:
            An envelope whose ``total`` is the match count **before** ``limit``
            and can therefore exceed ``len(navaids)`` — there is no offset, so
            narrow the filters instead. ``filters_applied`` echoes the filters
            the API used, normalised (codes upper-cased).

        Note:
            ``usageType`` is the only camelCase key in the API; it is exposed as
            ``navaid.usage_type``. Frequencies are numbers here but arrive as
            strings inside :meth:`AsyncAirports.search
            <skylink_api.resources.airports.AsyncAirports.search>` results, so
            both are accepted.

        Raises:
            ValueError: no filter supplied (no request is sent).
            BadRequestError: malformed or inverted bounding box (400).
            ServiceUnavailableError: the navaid dataset is not loaded yet (503).
        """

        spec = _list_spec(
            ident=ident, airport=airport, type=type, country=country, bbox=bbox, limit=limit
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(NavaidsResponse, result)
