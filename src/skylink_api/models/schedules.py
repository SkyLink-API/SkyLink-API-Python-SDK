"""Models for ``GET /schedules/departures`` and ``GET /schedules/arrivals``.

Two traps live here.

**PascalCase rows.** The envelope is snake_case like the rest of the API, but the
objects inside ``flights[]`` come straight from the scraped table and keep its
header casing: ``Time``, ``Date``, ``IATA``, ``Destination``/``Origin``,
``Flight``, ``Airline``, ``Status``. The SDK exposes ordinary snake_case
attributes and maps them with ``alias_generator=to_pascal``; ``IATA`` is the one
key the generator cannot produce (it would emit ``Iata``) so it carries an
explicit ``Field(alias=...)``. ``populate_by_name=True`` means both spellings
construct a model in your own code, but only the PascalCase form is on the wire.

**Departures and arrivals are different rows.** A departure row has
``Destination`` and no ``Origin``; an arrival row has ``Origin`` and no
``Destination``. Hence :class:`DepartureFlight` and :class:`ArrivalFlight`, each
with its own envelope, rather than one row type with two nullable fields.

Times and dates are opaque local strings — ``"16:05"`` and ``"11 Feb"``, no
timezone and no year — and ``Status`` is free prose (``"Estimated 16:39"``,
``"Landed 16:15"``, ``"Cancelled"``), so all of them stay ``str``.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_pascal

from ._base import SkyLinkModel

__all__ = [
    "ArrivalFlight",
    "ArrivalsResponse",
    "DepartureFlight",
    "DeparturesResponse",
    "ScheduleFlight",
    "ScheduleResponse",
]


class ScheduleFlight(SkyLinkModel):
    """One row of a schedule table, minus the direction specific column.

    Never returned on its own — see :class:`DepartureFlight` and
    :class:`ArrivalFlight`.
    """

    # Merged with SkyLinkModel's config (extra="allow", populate_by_name=True):
    # only the alias generator is added.
    model_config = ConfigDict(alias_generator=to_pascal)

    time: str | None = None
    """Wire key ``Time``. Airport-local time of day, ``"16:05"``. No timezone."""

    date: str | None = None
    """Wire key ``Date``. Day and month, ``"11 Feb"`` — **no year**."""

    iata: str | None = Field(default=None, alias="IATA")
    """Wire key ``IATA`` (all caps — the alias generator would produce ``Iata``,
    hence the explicit alias). IATA code of the *other* airport: the destination
    on a departure row, the origin on an arrival row."""

    flight: str | None = None
    """Wire key ``Flight``. Flight number as printed (``"C84093"``)."""

    airline: str | None = None
    """Wire key ``Airline``. Airline name, not a code."""

    status: str | None = None
    """Wire key ``Status``. Free prose from the source table — ``"Estimated
    16:39"``, ``"Landed 16:15"``, ``"Scheduled"``, ``"Cancelled"``. Not an
    enumeration; display it, do not switch on it."""


class DepartureFlight(ScheduleFlight):
    """A departure row: it has ``Destination`` and never ``Origin``."""

    destination: str | None = None
    """Wire key ``Destination``. City name of the destination (``"Seoul"``), not
    an airport code — the code is in :attr:`~ScheduleFlight.iata`."""


class ArrivalFlight(ScheduleFlight):
    """An arrival row: it has ``Origin`` and never ``Destination``."""

    origin: str | None = None
    """Wire key ``Origin``. City name of the origin (``"Marrakech"``), not an
    airport code — the code is in :attr:`~ScheduleFlight.iata`."""


class ScheduleResponse(SkyLinkModel):
    """Envelope shared by both schedule endpoints."""

    iata: str | None = None
    """IATA code the schedule was actually fetched for. When you queried by ICAO
    the API resolved it for you, so this differs from :attr:`airport_code`."""

    direction: str | None = None
    """``"departures"`` or ``"arrivals"``, echoed back."""

    airport_code: str | None = None
    """The identifier *you* passed — the ICAO code when you queried by ICAO, the
    IATA code otherwise."""

    total_flights: int | None = None
    """Rows the API collected across all fetched pages."""

    pages_fetched: int | None = None
    """How many upstream pages were walked to build this response."""


class DeparturesResponse(ScheduleResponse):
    """Envelope of ``GET /schedules/departures``."""

    flights: list[DepartureFlight] = Field(default_factory=list)


class ArrivalsResponse(ScheduleResponse):
    """Envelope of ``GET /schedules/arrivals``."""

    flights: list[ArrivalFlight] = Field(default_factory=list)
