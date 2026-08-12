"""Models for ``/delays/faa`` — live FAA National Airspace System alerts.

Everything here is scraped from the FAA's operational status page, which is why
**durations and times are prose, not numbers or timestamps**::

    "1 hour and 30 minutes"   # avg_delay
    "2 hours"                 # max_delay
    "9:59 pm EST"             # end_time

They are kept as ``str`` verbatim; parsing them is the caller's decision, not the
SDK's guess.

The envelope always carries all four arrays (empty when nothing is active), so a
quiet national airspace is a normal ``200`` with ``total_alerts=0`` and a
human-readable ``message`` — never a 404.
"""

from __future__ import annotations

from pydantic import Field

from ._base import SkyLinkModel

__all__ = [
    "AirspaceFlowProgram",
    "Closure",
    "FaaDelayResponse",
    "GroundDelay",
    "GroundStop",
]


class GroundDelay(SkyLinkModel):
    """A Ground Delay Program — departures to this airport are metered."""

    airport: str | None = None
    """ICAO or FAA identifier as printed by the FAA (often the 3-letter FAA
    code, e.g. ``"EWR"``, sometimes the ICAO ``"KEWR"``)."""

    airport_name: str | None = None
    reason: str | None = None
    """Free text, upper-cased by the source (``"WEATHER / THUNDERSTORMS"``)."""

    avg_delay: str | None = None
    """Prose duration such as ``"1 hour and 30 minutes"``. Not a number."""

    max_delay: str | None = None
    """Prose duration such as ``"2 hours"``. Not a number."""


class GroundStop(SkyLinkModel):
    """A Ground Stop — departures to this airport are held at their origin."""

    airport: str | None = None
    airport_name: str | None = None
    reason: str | None = None
    end_time: str | None = None
    """Expected end, as printed (``"9:59 pm EST"``). Opaque string."""


class Closure(SkyLinkModel):
    """An airport closure."""

    airport: str | None = None
    airport_name: str | None = None
    reason: str | None = None
    begin: str | None = None
    """Closure start, as printed. Opaque string."""

    reopen: str | None = None
    """Expected reopening, as printed. Opaque string."""


class AirspaceFlowProgram(SkyLinkModel):
    """An Airspace Flow Program — metering through a constrained area.

    Keyed by ATC **facility** (ARTCC), not by airport, which is why the
    per-airport endpoint cannot filter these.
    """

    facility: str | None = None
    """ARTCC identifier (``"ZNY"``, ``"ZDC"``)."""

    reason: str | None = None
    fca_start: str | None = None
    """Flow-constrained-area start, as printed. Opaque string."""

    fca_end: str | None = None
    """Flow-constrained-area end, as printed. Opaque string."""


class FaaDelayResponse(SkyLinkModel):
    """Envelope of ``GET /delays/faa`` and ``GET /delays/faa/{icao}``.

    All four arrays are always present and default to empty. US airports only —
    the FAA feed has no coverage anywhere else, so a European ICAO simply comes
    back with nothing rather than an error.
    """

    ground_delays: list[GroundDelay] = Field(default_factory=list)
    ground_stops: list[GroundStop] = Field(default_factory=list)
    closures: list[Closure] = Field(default_factory=list)
    airspace_flow_programs: list[AirspaceFlowProgram] = Field(default_factory=list)
    """**Not filtered** by the per-airport endpoint: flow programs are
    facility-level, so ``/delays/faa/KJFK`` returns every active program and
    leaves the relevance call to you."""

    total_alerts: int = 0
    """Sum of the four arrays' lengths."""

    message: str | None = None
    """Set **only when there are no delays** (``"No active FAA delays for KJFK"``);
    ``None`` whenever at least one alert is present."""
