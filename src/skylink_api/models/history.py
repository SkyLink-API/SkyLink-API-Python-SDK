"""Models for the archived-ADS-B endpoints (``/{plan}/history/*``).

Four different flight shapes exist on the wire — the 27-column search row, the
33-column detail row, the 14-column companion rows inside a positions response
and the 15-column airport-traffic row. They overlap heavily and the backend
returns raw SQL rows, so the SDK models **one wide** :class:`HistoryFlight` with
every column optional instead of four near-identical classes. Read the endpoint
docs for which subset a given call actually fills in.

Two more things worth knowing:

* Positions call the barometric altitude ``altitude_baro``. The live ADS-B
  namespace calls the same quantity ``altitude`` — do not copy code between them.
* ``icao24`` comes back **UPPERCASE** in the envelopes and flight rows while the
  request side wants lowercase (the SDK lower-cases it for you). Inside position
  rows it is whatever the ingest wrote, usually lowercase.

Times here are genuine tz-aware ISO-8601 (asyncpg → ``+00:00``), so unlike the
scraped strings elsewhere in the API they are parsed into ``datetime``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number

__all__ = [
    "HistoryAirportTrafficResponse",
    "HistoryFilters",
    "HistoryFlight",
    "HistoryFlightsResponse",
    "HistoryPosition",
    "HistoryPositionsResponse",
    "HistoryTrackResponse",
]


class HistoryPosition(SkyLinkModel):
    """One archived ADS-B position sample (~5 s cadence, newest first)."""

    timestamp: datetime | None = None

    icao24: str | None = None
    """Transponder address as stored — lower case in position rows."""

    latitude: float | None = None
    longitude: float | None = None

    altitude_baro: Number | None = None
    """Barometric altitude in feet. **Named ``altitude`` in the live ADS-B feed.**"""

    ground_speed: Number | None = None
    """Knots."""

    track: Number | None = None
    """Degrees true."""

    vertical_rate: Number | None = None
    """Feet per minute; negative is descending."""

    callsign: str | None = None
    is_on_ground: bool | None = None
    registration: str | None = None
    aircraft_type: str | None = None

    gps_spoofed: bool | None = None
    """Set when the ingest flagged the position as implausible."""


class HistoryFlight(SkyLinkModel):
    """An archived flight, wide enough for every endpoint that returns one.

    Which fields are populated depends on where the row came from:

    * ``flights`` search — everything except the six detail-only columns below.
    * ``flight/{id}`` — all of it (``off_block_time``, ``on_block_time``,
      ``duration_source``, ``arrival_distance_nm``, ``created_at``,
      ``updated_at`` appear **only** here).
    * ``positions*.flights[]`` — identity, airports and the four time columns.
    * ``airport/{icao}/traffic`` — identity, airports, times, duration, distance.

    A field the endpoint does not select is simply absent, which is
    indistinguishable from a NULL column here; both land as ``None``.
    """

    flight_id: str | None = None
    """UUID; the handle for ``flight()`` and ``track()``."""

    icao24: str | None = None
    """**UPPERCASE** in flight rows."""

    callsign: str | None = None
    flight_number: str | None = None
    """IATA number (``"BA117"``) when it could be derived from the callsign."""

    tail_number: str | None = None
    """Registration. Named ``registration`` in the track envelope."""

    aircraft_type_icao: str | None = None
    aircraft_type_name: str | None = None
    airline_icao: str | None = None
    airline_iata: str | None = None
    airline_name: str | None = None

    departure_airport_icao: str | None = None
    departure_airport_iata: str | None = None
    departure_airport_name: str | None = None
    arrival_airport_icao: str | None = None
    arrival_airport_iata: str | None = None
    arrival_airport_name: str | None = None

    flight_state: str | None = None
    """Lifecycle state; ``"ARCHIVED"`` is the only one the traffic and positions
    endpoints return, the search may also show in-progress rows."""

    flight_start: datetime | None = None
    """First position seen — earlier than ``takeoff_time``."""

    flight_end: datetime | None = None
    """Last position seen — later than ``landing_time``."""

    takeoff_time: datetime | None = None
    landing_time: datetime | None = None

    off_block_time: datetime | None = None
    """Detail endpoint only."""

    on_block_time: datetime | None = None
    """Detail endpoint only."""

    flight_duration_min: Number | None = None
    duration_source: str | None = None
    """Detail endpoint only — how ``flight_duration_min`` was derived."""

    distance_nm: Number | None = None
    arrival_distance_nm: Number | None = None
    """Detail endpoint only."""

    confidence: Number | str | None = None
    """How sure the matcher is about the route it attributed to this track.

    The live API sends a **score** (``1.0``, ``0.85``), not the ``"high"`` /
    ``"medium"`` / ``"low"`` label the other endpoints use — the union keeps
    both readable should the backend ever switch representation.
    """

    route_source: str | None = None
    gps_spoofing_suspected: bool | None = None
    diversion_suspected: bool | None = None

    created_at: datetime | None = None
    """Detail endpoint only — row insertion time, not a flight time."""

    updated_at: datetime | None = None
    """Detail endpoint only."""


class HistoryFilters(SkyLinkModel):
    """The filters ``flights`` actually applied, echoed back.

    Useful for one thing in particular: ``resolved_icao24`` shows which
    transponder address a ``registration`` was translated into.
    """

    icao24: str | None = None
    registration: str | None = None
    resolved_icao24: str | None = None
    callsign: str | None = None
    departure_icao: str | None = None
    arrival_icao: str | None = None


class HistoryFlightsResponse(SkyLinkModel):
    """Envelope of ``GET /{plan}/history/flights``."""

    filters: HistoryFilters | None = None
    count: int = 0
    flights: list[HistoryFlight] = Field(default_factory=list)

    note: str | None = None
    """Set **instead of** an error when the requested ``registration`` is not in
    the aircraft database: a plain ``200`` with ``count=0``, ``flights=[]`` and
    an explanatory note. Check it before concluding the aircraft never flew."""


class HistoryTrackResponse(SkyLinkModel):
    """Envelope of ``GET /{plan}/history/flight/{id}/track``.

    Flight identity is flattened into the envelope here (and ``tail_number``
    becomes ``registration``), so it does not reuse :class:`HistoryFlight`.
    """

    flight_id: str | None = None
    icao24: str | None = None
    """**UPPERCASE**."""

    callsign: str | None = None
    registration: str | None = None
    aircraft_type_icao: str | None = None
    departure_airport_icao: str | None = None
    departure_airport_iata: str | None = None
    arrival_airport_icao: str | None = None
    arrival_airport_iata: str | None = None
    takeoff_time: datetime | None = None
    landing_time: datetime | None = None

    count: int = 0
    positions: list[HistoryPosition] = Field(default_factory=list)
    """Newest first, bounded to takeoff → landing plus a 5-minute pad."""


class HistoryPositionsResponse(SkyLinkModel):
    """Envelope of ``GET /{plan}/history/positions/{icao24}`` and its
    ``/positions/registration/{reg}`` twin."""

    icao24: str | None = None
    """**UPPERCASE**."""

    registration: str | None = None
    """Present **only** on the ``/positions/registration/{reg}`` route, where it
    echoes the normalised (upper case, separator-free) registration."""

    count: int = 0
    positions: list[HistoryPosition] = Field(default_factory=list)

    flights: list[HistoryFlight] = Field(default_factory=list)
    """Archived flights overlapping the queried window (max 50), so position
    segments can be correlated to flights. Narrow rows — see
    :class:`HistoryFlight`."""


class HistoryAirportTrafficResponse(SkyLinkModel):
    """Envelope of ``GET /{plan}/history/airport/{icao}/traffic``."""

    icao: str | None = None
    direction: str | None = None
    """``"dep"``, ``"arr"`` or ``"both"``, echoed back."""

    count: int = 0
    flights: list[HistoryFlight] = Field(default_factory=list)
