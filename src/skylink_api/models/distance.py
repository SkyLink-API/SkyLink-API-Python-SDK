"""Models for ``GET /distance`` — great-circle distance and bearing.

Both endpoints of the calculation are the same :class:`Coordinates` shape, so a
point resolved from an airport code and a point given as raw latitude/longitude
are indistinguishable in the response apart from the identifier fields being
filled in. The midpoint is always a bare coordinate pair.

``unit`` comes back as a plain ``str`` (``"nm"``, ``"km"``, ``"mi"``) even though
the request side is a ``Literal`` — the SDK keeps response enums open.
"""

from __future__ import annotations

from ._base import SkyLinkModel

__all__ = ["Coordinates", "DistanceResponse"]


class Coordinates(SkyLinkModel):
    """A point on the globe, optionally carrying the airport it was resolved from.

    ``icao_code``/``iata_code``/``name`` are populated only when the point was
    given as an airport code; a point supplied as raw latitude/longitude — and
    the computed midpoint — leaves all three ``None``.
    """

    latitude: float | None = None
    longitude: float | None = None
    icao_code: str | None = None
    """Resolved ICAO code. ``None`` for raw coordinates and for the midpoint."""

    iata_code: str | None = None
    """Resolved IATA code, when the airport has one."""

    name: str | None = None
    """Airport name from the database."""


class DistanceResponse(SkyLinkModel):
    """Envelope of ``GET /distance``."""

    from_point: Coordinates | None = None
    to_point: Coordinates | None = None
    distance: float | None = None
    """Great-circle distance in whatever ``unit`` says."""

    unit: str | None = None
    """``"nm"`` (default), ``"km"`` or ``"mi"``, echoed back."""

    bearing: float | None = None
    """**Initial** bearing in degrees from true north (0-360). On a great circle
    the bearing changes along the route, so this is not the bearing you would
    fly the whole way."""

    bearing_cardinal: str | None = None
    """The initial bearing binned to a compass point (``"NE"``, ``"SSW"``)."""

    midpoint: Coordinates | None = None
    """Great-circle midpoint — bare coordinates, never an airport."""
