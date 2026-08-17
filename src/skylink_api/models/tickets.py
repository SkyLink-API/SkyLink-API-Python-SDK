"""Models for ``GET /tickets/search``.

Three things about this payload are load-bearing and are reflected in the types:

1. **``layovers`` is ``None``, not ``[]``, for a direct flight.** The service
   writes ``"layovers": layovers if layovers else None``, so the field is
   ``list[Layover] | None`` and *not* a ``default_factory=list``. Testing
   ``if offer.layovers:`` works for both shapes; ``len(offer.layovers)`` does not.
2. **``departure_datetime``/``arrival_datetime`` are naive ISO strings** — the
   upstream (Google Flights) reports local airport time with no offset, and the
   service just calls ``.isoformat()`` on it. Parsing them into ``datetime``
   would silently produce a naive value that later code treats as UTC, turning a
   09:00 departure from London into 09:00Z. They stay ``str``.
3. **``price_usd`` is not guaranteed to be USD.** ``_to_usd()`` returns the
   original amount rounded when the FX rate lookup fails, without flagging it.
   Compare against ``original_currency`` before trusting the number — which is
   now possible: ``original_price``/``original_currency`` are emitted since the
   2026-08 backend release, having previously been documented but absent.
"""

from __future__ import annotations

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number

__all__ = ["Layover", "TicketLeg", "TicketOffer", "TicketSearchResponse"]


class TicketLeg(SkyLinkModel):
    """One flown segment of an itinerary.

    A direct flight has exactly one leg; a one-stop itinerary has two.
    """

    flight_number: str | None = None
    """Airline code plus number, concatenated by the API (``"BA117"``)."""

    airline: str | None = None
    """Marketing airline's full name (``"British Airways"``)."""

    airline_code: str | None = None
    """IATA airline code (``"BA"``). Falls back to the upstream enum name when
    the airline is outside the API's name→IATA table, so it is occasionally not
    a real IATA code."""

    departure_airport: str | None = None
    """IATA code (``"LHR"``)."""

    departure_airport_name: str | None = None
    arrival_airport: str | None = None
    """IATA code (``"JFK"``)."""

    arrival_airport_name: str | None = None

    departure_time: str | None = None
    """Local departure time as ``"HH:MM"`` — no date, no timezone."""

    arrival_time: str | None = None
    """Local arrival time as ``"HH:MM"``. May belong to the following day; the
    payload gives no way to tell other than comparing the datetimes below."""

    departure_datetime: str | None = None
    """Local departure timestamp, ISO 8601 **without a timezone offset**
    (``"2026-05-01T09:00:00"``).

    Warning:
        Deliberately a ``str``. The value is wall-clock time at the departure
        airport; attaching UTC to it — which is what ``datetime.fromisoformat``
        plus any tz-aware arithmetic effectively does — shifts every flight by
        the airport's offset. Convert it yourself with the airport's timezone if
        you need an instant.
    """

    arrival_datetime: str | None = None
    """Local arrival timestamp, naive ISO 8601. Same warning as
    :attr:`departure_datetime`."""

    duration_min: int | None = None
    """Block time of this leg in minutes."""


class Layover(SkyLinkModel):
    """A connection between two consecutive legs."""

    airport: str | None = None
    """IATA code of the connecting airport."""

    airport_name: str | None = None

    duration_min: int | None = None
    """Ground time in minutes. ``None`` when either surrounding leg was missing
    a datetime and the API could not subtract them."""


class TicketOffer(SkyLinkModel):
    """One priced itinerary. Offers come back cheapest first."""

    price_usd: Number | None = None
    """Total price for the whole party.

    Warning:
        Nominally USD, but when the currency conversion fails the API returns
        the amount **in the original currency** without changing the field name
        or setting any flag. Check ``original_currency`` when precision matters.
    """

    original_price: Number | None = None
    """Price as quoted upstream, before conversion to USD.

    .. versionchanged:: 0.2.0
       Used to be documented in the endpoint's OpenAPI example but never emitted.
       The ticket service now sends it; verified live on 2026-08-15
       (``JFK→LAX``: ``price_usd=168.52``, ``original_price=137.0``,
       ``original_currency="CHF"``). Still optional — an offer already quoted in
       USD has nothing to convert.
    """

    original_currency: str | None = None
    """ISO 4217 code of :attr:`original_price` (``"CHF"``).

    Together with :attr:`original_price` this is how you tell a genuinely
    converted price from the passthrough described on :attr:`price_usd`.
    """

    total_duration_min: int | None = None
    """Door-to-door duration including layovers, in minutes."""

    stops: int | None = None
    """Number of stops — ``0`` for a direct flight."""

    legs: list[TicketLeg] = Field(default_factory=list)

    layovers: list[Layover] | None = None
    """Connections between legs, or ``None`` for a direct flight.

    Warning:
        ``None``, **not** an empty list. This is the one collection in the SDK
        that does not default to ``[]``, because the distinction is exactly how
        the API signals a nonstop itinerary.
    """


class TicketSearchResponse(SkyLinkModel):
    """Envelope of ``GET /tickets/search``."""

    origin: str | None = None
    """Validated origin IATA code, echoed back."""

    destination: str | None = None
    """Validated destination IATA code, echoed back."""

    date: str | None = None
    """Travel date as ``"YYYY-MM-DD"``.

    A string, not a ``date``: it is the API's own ``str(date)`` echo of the
    parameter (defaulted to today + 7 days when the request omitted it), and
    round-tripping it through ``date`` would only add a failure mode.
    """

    passengers: int | None = None
    """Passenger count the search was run with."""

    count: int = 0
    """Number of offers in :attr:`flights`.

    Note:
        No longer capped at 15 — a ``JFK→LAX`` search returned 111 offers on
        2026-08-15, a ``LHR→JFK`` one 120. Page or slice on your side if you are
        rendering them; do not assume the list is short.
    """

    flights: list[TicketOffer] = Field(default_factory=list)
    """Empty when no fares were found — a normal ``200``, not a 404."""
