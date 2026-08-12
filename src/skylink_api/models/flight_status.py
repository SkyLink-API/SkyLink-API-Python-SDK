"""Models for ``GET /flight_status/{flight_number}``.

This is the most hostile payload in the API and the models mirror that:

* **The two legs are not the same object.** ``departure`` carries
  ``actual_time``/``actual_date``/``checkin``; ``arrival`` carries
  ``estimated_time``/``estimated_date``/``baggage``. They share four fields and
  differ in three, so they are two classes (:class:`FlightStatusDeparture`,
  :class:`FlightStatusArrival`) over a common base — not one union type.
* **Every value is an opaque string.** Times are airport-local ``"10:30"`` with
  no timezone, dates are ``"11 Feb"`` with no year. Parsing them into
  ``datetime`` would require inventing a year and a zone, so the SDK does not:
  everything stays ``str``.
* **Empty means empty string, not null.** The scraper writes ``""`` for a field
  it could not find, and the presentation layer then rewrites empty text fields
  to ``"--"`` and empty times to ``"--:--"``. So ``checkin == ""``,
  ``checkin == "--"`` and ``checkin is None`` all mean "unknown"; test for
  falsiness plus the ``"--"`` sentinel, never ``is None`` alone.
* **A leg can arrive as ``{}``.** When the source page has no arrival card the
  API still sends the key with an empty object, which is why every field is
  optional and both legs default to ``None``.

The response is untyped on the backend (``Dict[str, Any]``), so ``extra="allow"``
from :class:`~skylink_api.models._base.SkyLinkModel` is doing real work here.
"""

from __future__ import annotations

from ._base import SkyLinkModel

__all__ = [
    "FlightStatusArrival",
    "FlightStatusDeparture",
    "FlightStatusLeg",
    "FlightStatusResponse",
]


class FlightStatusLeg(SkyLinkModel):
    """Fields shared by the departure and the arrival leg.

    Never returned on its own — see :class:`FlightStatusDeparture` and
    :class:`FlightStatusArrival` for the asymmetric halves.
    """

    airport: str | None = None
    """Airport as presented, usually ``"LHR • London"`` (IATA code, a bullet and
    the city) but sometimes the bare code when the city lookup missed."""

    airport_full: str | None = None
    """Full airport name (``"London Heathrow Airport"``)."""

    scheduled_time: str | None = None
    """Airport-local time of day, ``"10:30"``. No date, no timezone, ``"--:--"``
    or ``""`` when unknown."""

    scheduled_date: str | None = None
    """Airport-local day and month, ``"11 Feb"`` — **no year**. Empty string when
    unknown."""

    terminal: str | None = None
    """Terminal designator (``"5"``, ``"2B"``); ``"--"`` when unknown."""

    gate: str | None = None
    """Gate designator (``"A12"``); ``"--"`` when unknown."""


class FlightStatusDeparture(FlightStatusLeg):
    """The departure leg — the half with *actual* times and a check-in desk."""

    actual_time: str | None = None
    """Actual (or best known) off-block time, airport-local ``"10:35"``."""

    actual_date: str | None = None
    """Actual departure day, ``"11 Feb"``, no year."""

    checkin: str | None = None
    """Check-in desk range (``"A1-A20"``). Departure only — the arrival leg has
    ``baggage`` in this slot."""


class FlightStatusArrival(FlightStatusLeg):
    """The arrival leg — the half with *estimated* times and a baggage belt."""

    estimated_time: str | None = None
    """Estimated on-block time, airport-local ``"14:50"``.

    May be synthesised by the API's ML flight-time model when the scraped value
    is missing or implausible; it is still an opaque local time either way.
    """

    estimated_date: str | None = None
    """Estimated arrival day, ``"11 Feb"``, no year."""

    baggage: str | None = None
    """Baggage reclaim belt (``"7"``). Arrival only — the departure leg has
    ``checkin`` in this slot."""


class FlightStatusResponse(SkyLinkModel):
    """Live status of one flight (``GET /flight_status/{flight_number}``)."""

    flight_number: str | None = None
    """Flight number as the source printed it — often spaced (``"BA 123"``),
    not necessarily identical to the value you passed in."""

    airline: str | None = None
    """Airline name, or ``"Unknown"`` when the source page did not name one."""

    status: str | None = None
    """Prose status straight from the source: ``"En Route"``, ``"Landed"``,
    ``"Scheduled"``, ``"Cancelled"``, ``"Unknown"``… Not an enumeration — treat
    it as display text, not a state machine input."""

    departure: FlightStatusDeparture | None = None
    """Departure leg; may be an empty object when the source had no data."""

    arrival: FlightStatusArrival | None = None
    """Arrival leg; may be an empty object when the source had no data."""
