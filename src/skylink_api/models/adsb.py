"""Models for the ``/adsb/*`` endpoints — the live ADS-B feed.

Three shapes live here: the aircraft list envelope, the tracking statistics
block and the service health probe.

Two traps drive the typing:

* **Position fields are optional.** A transponder that only broadcasts an
  identity (Mode S without ADS-B position, or an aircraft that has just come
  into range) yields a row where ``latitude``/``longitude``/``altitude`` and
  everything derived from them are ``null``. Only the timestamps are always
  present.
* **``altitude_stats`` is ``{}`` when nothing is airborne.** The backend builds
  it from the airborne subset and leaves the dict empty when that subset is —
  so every leaf is optional and an empty object is a valid parse.

Datetimes: ``last_seen``, ``first_seen``, ``timestamp`` and
``last_message_received`` are genuine ISO-8601 instants, but the backend
serialises them from ``datetime.now()`` — **naive, no ``Z`` and no offset**.
They arrive as ``datetime`` objects with ``tzinfo is None``; treat them as UTC.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number

__all__ = [
    "AdsbAircraft",
    "AdsbAircraftList",
    "AdsbAltitudeStats",
    "AdsbHealth",
    "AdsbStatistics",
]


# ── aircraft ─────────────────────────────────────────────────────────────────


class AdsbAircraft(SkyLinkModel):
    """One aircraft currently tracked by the ADS-B feed.

    Everything except the identity and the two timestamps may be ``None``: the
    feed forwards whatever the transponder actually broadcast.
    """

    icao24: str | None = None
    """ICAO 24-bit transponder address, 6 hex characters. Upper-cased by the API
    in responses even though queries take it lower-case."""

    callsign: str | None = None
    """Flight callsign as broadcast (``"BAW117"``), trailing spaces stripped."""

    latitude: float | None = None
    longitude: float | None = None
    altitude: Number | None = None
    """Barometric altitude in **feet**. Named ``altitude_baro`` in the history
    namespace — same quantity, different endpoint."""

    ground_speed: Number | None = None
    """Knots."""

    track: Number | None = None
    """Track angle in degrees (0-360)."""

    vertical_rate: Number | None = None
    """Feet per minute; negative when descending."""

    is_on_ground: bool | None = None
    """``None`` when the aircraft never broadcast a ground bit."""

    last_seen: datetime | None = None
    """Most recent message from this aircraft. Naive (no timezone) on the wire."""

    first_seen: datetime | None = None
    """First message of the current tracking session. Naive on the wire."""

    registration: str | None = None
    """Tail number, resolved from the aircraft database — not broadcast."""

    aircraft_type: str | None = None
    """Type as stored in the aircraft database; either a designator (``"B77W"``)
    or a full model name (``"Boeing 777-36N"``) depending on the source row."""

    airline: str | None = None
    photo_url: str | None = None
    """Only populated when the call passed ``photos=True`` (and only for the
    first 50 aircraft of the page)."""


class AdsbAircraftList(SkyLinkModel):
    """Envelope of ``GET /adsb/aircraft``."""

    aircraft: list[AdsbAircraft] = Field(default_factory=list)
    total_count: int = 0
    """Number of aircraft that matched the filters **before** pagination — use
    it to drive ``offset``, not ``len(aircraft)``."""

    timestamp: datetime | None = None
    """When the API assembled the response. Naive (no timezone) on the wire."""


# ── statistics ───────────────────────────────────────────────────────────────


class AdsbAltitudeStats(SkyLinkModel):
    """Altitude spread across the airborne aircraft, in feet.

    All three fields are optional because the API sends a bare ``{}`` whenever
    no airborne aircraft carries an altitude — an empty object parses into this
    model with every attribute ``None``.
    """

    min_altitude: Number | None = None
    max_altitude: Number | None = None
    avg_altitude: Number | None = None
    """Arithmetic mean — a float even when the inputs are whole numbers."""


class AdsbStatistics(SkyLinkModel):
    """Envelope of ``GET /adsb/aircraft/statistics``.

    Counts describe the whole feed, not a filtered subset; ``positioned`` +
    ``on_ground`` + ``airborne`` do not add up to ``total_aircraft`` because they
    answer different questions (``on_ground``/``airborne`` skip aircraft that
    never broadcast a ground bit).
    """

    total_aircraft: int = 0
    positioned_aircraft: int = 0
    """Aircraft reporting both a latitude and a longitude."""

    on_ground: int = 0
    airborne: int = 0
    altitude_stats: AdsbAltitudeStats = Field(default_factory=AdsbAltitudeStats)
    """Empty (all fields ``None``) when nothing airborne reports an altitude."""

    timestamp: datetime | None = None
    """Naive (no timezone) on the wire."""


# ── health ───────────────────────────────────────────────────────────────────


class AdsbHealth(SkyLinkModel):
    """Envelope of ``GET /adsb/health`` — the ingest pipeline's own status."""

    status: str | None = None
    """One of ``"healthy"``, ``"offline"``, ``"connected_no_data"`` or
    ``"degraded"``.

    Typed as ``str`` rather than a ``Literal`` on purpose: response enums stay
    open across the SDK so a new backend state never becomes a parse error.

    * ``healthy`` — connected and receiving positions.
    * ``connected_no_data`` — socket up, no recent aircraft (usually a feed-side
      outage).
    * ``degraded`` — the ingest loop runs but the socket is down.
    * ``offline`` — the ingest loop is not running at all.
    """

    connected: bool | None = None
    """Whether the socket to the ADS-B hub is currently open."""

    active_aircraft_count: int = 0
    connection_uptime: float | None = None
    """Seconds since the connection came up. Currently always ``None`` — the
    backend reserves the field but does not populate it."""

    last_message_received: datetime | None = None
    """Newest ``last_seen`` across the feed, or ``None`` when it is empty.
    Naive (no timezone) on the wire."""
