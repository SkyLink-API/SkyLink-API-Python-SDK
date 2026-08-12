"""Models for ``GET /airlines/search``.

The route returns a **bare JSON array**, not an envelope — there is no ``total``
and no ``airlines`` key, so the resource casts to ``list[Airline]`` directly.
"""

from __future__ import annotations

from ._base import SkyLinkModel

__all__ = ["Airline"]


class Airline(SkyLinkModel):
    """One airline from the OpenFlights-derived dataset."""

    id: int | None = None
    """Dataset row id."""

    name: str | None = None
    alias: str | None = None
    """Trading name when it differs from ``name``; often ``None``."""

    iata: str | None = None
    """2-letter IATA code."""

    icao: str | None = None
    """3-letter ICAO code."""

    callsign: str | None = None
    """Radio callsign (``"SPEEDBIRD"``), upper-case."""

    country: str | None = None
    """Country name, not an ISO code."""

    active: str | None = None
    """``"Y"`` or ``"N"`` — a **string, not a bool**.

    The dataset also contains a handful of other single-letter markers, so this
    is deliberately left un-narrowed and un-coerced. Compare against ``"Y"``
    explicitly rather than relying on truthiness (``"N"`` is truthy!).
    """

    logo: str | None = None
    """``https://media.skylinkapi.com/logos/{IATA}.png``, or ``None`` when the
    airline has no IATA code. The URL is generated, not verified — it may 404."""
