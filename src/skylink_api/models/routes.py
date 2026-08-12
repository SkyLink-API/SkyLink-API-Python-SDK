"""Models for the ``/routes/*`` endpoints.

``GET /routes/callsign/{callsign}`` is the only endpoint in the API that returns
**two structurally different objects** from the same path. ``lookup_route_by_callsign``
tries VRS standing data first (exact callsign → one specific airport pair) and
falls back to the airline-level route list keyed on the callsign prefix::

    {"callsign": "BAW117", "departure_icao": "EGLL", ..., "source": "vrs"}
    {"callsign_prefix": "BAW", "routes": [...],       ..., "source": "airline_routes"}

The fallback has **no ``callsign`` key at all** — an important detail, because a
single flattened model would let ``route.callsign`` silently be ``None`` there.
Instead the two shapes are separate models joined by a pydantic *discriminated
union* on ``source`` (:data:`CallsignRoute`), so a wrong-shaped payload fails
loudly and static type checkers force callers to narrow before reading fields.

``source`` is therefore the one response field typed as a ``Literal`` rather
than ``str`` — the discriminator has to be exact. Every other enum-ish response
field (``confidence``, ``direction``) stays ``str``.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number

__all__ = [
    "AirlineRoute",
    "AirlineRoutesResult",
    "AirportRoutesResponse",
    "CallsignRoute",
    "RoutePair",
    "RoutePairsResponse",
    "VrsRouteResult",
]


# ── callsign lookup: variant A, VRS standing data ────────────────────────────


class VrsRouteResult(SkyLinkModel):
    """An exact callsign match from VRS standing data (``source == "vrs"``).

    This is the useful variant: it names the two airports the specific flight
    operates between.
    """

    source: Literal["vrs"]
    """Union discriminator."""

    callsign: str | None = None
    """The callsign as requested, upper-cased and stripped."""

    callsign_prefix: str | None = None
    """The callsign with its numeric suffix removed (``"BAW117"`` → ``"BAW"``) —
    an ICAO airline designator."""

    airline_code: str | None = None
    """Airline code from the VRS record."""

    departure_icao: str | None = None
    """First airport of :attr:`airports`."""

    arrival_icao: str | None = None
    """Last airport of :attr:`airports`."""

    airports: list[str] = Field(default_factory=list)
    """Full ICAO sequence for the flight. Longer than two entries for a
    multi-sector callsign, in which case ``departure_icao``/``arrival_icao``
    skip the intermediate stops."""

    confidence: str | None = None
    """Always ``"high"`` for this variant."""


# ── callsign lookup: variant B, airline-level fallback ───────────────────────


class AirlineRoute(SkyLinkModel):
    """One route flown by the airline, as listed in the fallback variant.

    Airports are **IATA** here, unlike the ICAO codes of :class:`VrsRouteResult`.
    """

    src: str | None = None
    """Origin IATA code (``"LHR"``)."""

    dst: str | None = None
    """Destination IATA code (``"JFK"``)."""

    km: Number | None = None
    """Great-circle distance in kilometres."""

    duration_min: int | None = None
    """Typical block time in minutes."""


class AirlineRoutesResult(SkyLinkModel):
    """Airline-level fallback (``source == "airline_routes"``).

    Returned when the exact callsign is not in VRS: the API strips the digits,
    looks the prefix up in the airline route dataset and hands back that
    airline's network. It says nothing about where *this* flight is going.

    Note:
        There is **no ``callsign`` field** on this variant — only
        :attr:`callsign_prefix`. That is why the union is discriminated instead
        of merged.
    """

    source: Literal["airline_routes"]
    """Union discriminator."""

    callsign_prefix: str | None = None
    """The prefix that matched (``"BAW"``, or its first two letters)."""

    airline_code: str | None = None
    """IATA airline code the dataset is keyed by."""

    airline_name: str | None = None

    routes: list[AirlineRoute] = Field(default_factory=list)
    """**Truncated to the first 20 routes** by the API, regardless of
    :attr:`total_routes`."""

    total_routes: int | None = None
    """How many routes the airline actually has — usually far more than
    ``len(routes)``."""

    confidence: str | None = None
    """Always ``"low"`` for this variant: the route is a guess about the
    airline, not about the flight."""


#: Result of ``GET /routes/callsign/{callsign}`` — a real union, resolved by the
#: ``source`` key. Narrow it before use::
#:
#:     route = sky.routes.by_callsign("BAW117")
#:     if route.source == "vrs":
#:         print(route.departure_icao, "→", route.arrival_icao)
#:     else:
#:         print(route.airline_name, "flies", route.total_routes, "routes")
CallsignRoute: TypeAlias = Annotated[
    VrsRouteResult | AirlineRoutesResult,
    Field(discriminator="source"),
]


# ── airport routes and route pairs (identical row shape) ─────────────────────


class RoutePair(SkyLinkModel):
    """One directed airport pair with the airlines that serve it.

    The same row is returned by ``/routes/airport/{code}`` and ``/routes/pairs``.
    """

    departure: str | None = None
    """Origin IATA code."""

    arrival: str | None = None
    """Destination IATA code."""

    airlines: list[str] = Field(default_factory=list)
    """Full airline **names**, not codes (``["British Airways", "Iberia"]``)."""

    km: Number | None = None
    """Great-circle distance in kilometres."""

    duration_min: int | None = None
    """Typical block time in minutes."""


class AirportRoutesResponse(SkyLinkModel):
    """Envelope of ``GET /routes/airport/{code}``."""

    code: str | None = None
    """The requested code, upper-cased and echoed back **as given** — an ICAO
    input is not rewritten here even though the lookup resolves it to IATA
    internally, so it may not match the codes inside :attr:`routes`."""

    direction: str | None = None
    """``"dep"``, ``"arr"`` or ``"both"``, echoed back."""

    count: int = 0
    """Rows in :attr:`routes` **after** the limit was applied, so it is never
    the total number of matching routes."""

    routes: list[RoutePair] = Field(default_factory=list)
    """Sorted by number of serving airlines, descending."""


class RoutePairsResponse(SkyLinkModel):
    """Envelope of ``GET /routes/pairs``."""

    count: int = 0
    """Rows in :attr:`routes` after the limit was applied."""

    routes: list[RoutePair] = Field(default_factory=list)
    """Sorted by number of serving airlines, descending."""
