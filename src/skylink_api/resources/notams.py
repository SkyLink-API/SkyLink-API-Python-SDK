"""Notices to Air Missions for an airport.

Structure follows :mod:`skylink_api.resources.weather`: builders first, then the
sync class, then its async mirror.

The two exclude filters go on the wire as comma separated lists. The builder
accepts either form — ``exclude_scope="FIR"``, ``exclude_scope=["FIR"]`` or
``exclude_qcode=["QK", "QF"]`` — and joins sequences itself, so the wire format
is decided in one place instead of by the query serialiser.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.notams import NotamsResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncNotams", "Notams"]

#: Where a NOTAM applies. Used by ``exclude_scope``; on responses the field stays
#: a plain ``str``.
NotamScope = Literal["AERODROME", "FIR"]

#: A CSV query parameter: one value, a sequence of values, or a ready-made
#: comma separated string.
CsvLike = str | Sequence[str]


# ── builders ─────────────────────────────────────────────────────────────────


def _format_csv(value: CsvLike | None) -> str | None:
    """Join a sequence into the API's comma separated form; pass strings through."""

    if value is None:
        return None
    if isinstance(value, str):
        return value
    joined = ",".join(str(item) for item in value)
    return joined or None


def _by_airport_spec(
    icao: str,
    *,
    exclude_qcode: CsvLike | None = None,
    exclude_scope: CsvLike | Sequence[NotamScope] | None = None,
    include_future: bool = False,
) -> RequestSpec:
    """``GET /notams/{icao}``."""

    return RequestSpec(
        method="GET",
        path=f"/notams/{icao}",
        query={
            "exclude_qcode": _format_csv(exclude_qcode),
            "exclude_scope": _format_csv(exclude_scope),
            "include_future": include_future,
        },
        cast_to=NotamsResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Notams:
    """``sky.notams`` — NOTAMs from the FAA SWIM feed.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            notams = sky.notams.by_airport("KJFK", exclude_scope="FIR")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def by_airport(
        self,
        icao: str,
        *,
        exclude_qcode: CsvLike | None = None,
        exclude_scope: CsvLike | Sequence[NotamScope] | None = None,
        include_future: bool = False,
        request_options: RequestOptions | None = None,
    ) -> NotamsResponse:
        """NOTAMs currently in effect at an airport — ``GET /notams/{icao}``.

        The result merges the aerodrome's own NOTAMs with the en-route notices
        filed under its FIR/ARTCC, the way a printed AIS aerodrome briefing does;
        each entry's ``scope`` says which is which. Expired NOTAMs are never
        returned.

        Args:
            icao: 4-letter ICAO code (``"KJFK"``), upper-cased server-side.
            exclude_qcode: Q-code prefixes to drop server-side, as a string or a
                sequence: ``"QK"`` (or ``["QK"]``) removes the whole checklist
                category ``QK*``, ``"QKKKK"`` only that exact code. Cuts response
                size a lot at busy airports.
            exclude_scope: Scopes to drop — ``"FIR"`` removes FIR-wide en-route
                notices, ``"AERODROME"`` the airport's own. A sequence is joined
                with commas.
            include_future: Also return NOTAMs whose effective time is still
                ahead, tagged ``status="FUTURE"``. Default ``False`` matches a
                live briefing and returns only ``status="ACTIVE"`` entries.

        Note:
            ``effective`` and ``expiration`` are **opaque strings** in mixed
            formats (ISO 8601, ``YYYYMMDDHHMM``, ``YYMMDDHHMM``, plus ``"PERM"``
            and an ``"EST"`` suffix) and the SDK never parses them — see
            :mod:`skylink_api.models.notams`.

            An airport with no NOTAMs is a normal ``200`` with ``notams=[]``, not
            a 404.

        Raises:
            UnprocessableEntityError: ``icao`` is not exactly 4 characters (422).
            ServiceUnavailableError: the SWIM feed is not ready or failed (503).
        """

        spec = _by_airport_spec(
            icao,
            exclude_qcode=exclude_qcode,
            exclude_scope=exclude_scope,
            include_future=include_future,
        )
        return cast(NotamsResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncNotams:
    """``sky.notams`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Notams`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            notams = await sky.notams.by_airport("KJFK", exclude_scope="FIR")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def by_airport(
        self,
        icao: str,
        *,
        exclude_qcode: CsvLike | None = None,
        exclude_scope: CsvLike | Sequence[NotamScope] | None = None,
        include_future: bool = False,
        request_options: RequestOptions | None = None,
    ) -> NotamsResponse:
        """NOTAMs currently in effect at an airport — ``GET /notams/{icao}``.

        The result merges the aerodrome's own NOTAMs with the en-route notices
        filed under its FIR/ARTCC; each entry's ``scope`` says which is which.
        Expired NOTAMs are never returned.

        Args:
            icao: 4-letter ICAO code (``"KJFK"``), upper-cased server-side.
            exclude_qcode: Q-code prefixes to drop server-side, as a string or a
                sequence: ``"QK"`` removes the whole ``QK*`` checklist category,
                ``"QKKKK"`` only that exact code.
            exclude_scope: Scopes to drop — ``"FIR"`` or ``"AERODROME"``; a
                sequence is joined with commas.
            include_future: Also return NOTAMs whose effective time is still
                ahead, tagged ``status="FUTURE"``.

        Note:
            ``effective`` and ``expiration`` are **opaque strings** in mixed
            formats and the SDK never parses them — see
            :mod:`skylink_api.models.notams`. No NOTAMs is a normal ``200`` with
            ``notams=[]``.

        Raises:
            UnprocessableEntityError: ``icao`` is not exactly 4 characters (422).
            ServiceUnavailableError: the SWIM feed is not ready or failed (503).
        """

        spec = _by_airport_spec(
            icao,
            exclude_qcode=exclude_qcode,
            exclude_scope=exclude_scope,
            include_future=include_future,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(NotamsResponse, result)
